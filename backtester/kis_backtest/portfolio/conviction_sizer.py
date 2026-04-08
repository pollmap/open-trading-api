"""확신 기반 포지션 사이징 — Ackman의 "확신이 높으면 크게 걸어라"

CatalystTracker 스코어 → 확신도 → Half-Kelly 포지션 비중 연결.

Bill Ackman 철학:
- 확신 8-10 → 집중 투자 (15-20%)
- 확신 5-7  → 중간 비중 (5-15%)
- 확신 1-4  → 소규모 또는 스킵 (2-5%)

Half-Kelly 공식:
    conviction_normalized = (final_conviction - 1) / 9   # 1-10 → 0-1
    kelly_raw = conviction_normalized * kelly_fraction
    weight = min(kelly_raw, max_position_pct)
    amount = weight * total_capital

Usage:
    from kis_backtest.portfolio.conviction_sizer import ConvictionSizer

    sizer = ConvictionSizer(max_position_pct=0.20)

    # 확신도 설정
    level = sizer.set_conviction("005930", base_conviction=8.0, catalyst_score=2.5)
    print(level.final_conviction)  # 10.0 (clamped)

    # 포지션 사이징
    pos = sizer.size_position("005930", total_capital=100_000_000)
    print(pos.weight, pos.amount)  # 0.20, 20_000_000

    # 포트폴리오 전체
    portfolio = sizer.size_portfolio(["005930", "000660"], total_capital=100_000_000)

    # JSON 저장/복원
    sizer.save("conviction_state.json")
    sizer2 = ConvictionSizer.load("conviction_state.json")
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_VERSION = "1.0.0"


def _clamp(value: float, lo: float, hi: float) -> float:
    """값을 [lo, hi] 범위로 클램핑."""
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class ConvictionLevel:
    """종목별 확신도

    Attributes:
        symbol: 종목 코드
        base_conviction: 기본 확신도 (1-10, CUFA 보고서 또는 수동)
        catalyst_boost: 카탈리스트 부스트 (0-3, CatalystTracker 스코어)
        kill_condition_penalty: 킬 조건 감점 (0-5, 활성 킬 조건 수)
        final_conviction: 최종 확신도 (base + boost - penalty, 1-10 클램핑)
    """
    symbol: str
    base_conviction: float
    catalyst_boost: float
    kill_condition_penalty: float
    final_conviction: float


@dataclass(frozen=True)
class PositionSize:
    """포지션 사이즈 결과

    Attributes:
        symbol: 종목 코드
        conviction: 최종 확신도
        weight: 포트폴리오 비중 (0.0-1.0)
        amount: 투자 금액 (KRW)
        kelly_raw: Raw Kelly fraction (capping 전)
        capped: max_position_pct로 잘렸는지 여부
    """
    symbol: str
    conviction: float
    weight: float
    amount: float
    kelly_raw: float
    capped: bool


class ConvictionSizer:
    """확신 기반 포지션 사이저

    Ackman 스타일 집중 투자: 확신이 높으면 비중을 키운다.
    Half-Kelly 공식으로 리스크 관리.
    """

    def __init__(
        self,
        max_position_pct: float = 0.20,
        min_position_pct: float = 0.02,
        kelly_fraction: float = 0.5,
    ) -> None:
        if max_position_pct <= 0 or max_position_pct > 1.0:
            raise ValueError(f"max_position_pct must be in (0, 1.0]: {max_position_pct}")
        if min_position_pct < 0 or min_position_pct >= max_position_pct:
            raise ValueError(
                f"min_position_pct must be in [0, max_position_pct): {min_position_pct}"
            )
        if kelly_fraction <= 0 or kelly_fraction > 1.0:
            raise ValueError(f"kelly_fraction must be in (0, 1.0]: {kelly_fraction}")

        self._max_position_pct = max_position_pct
        self._min_position_pct = min_position_pct
        self._kelly_fraction = kelly_fraction
        self._convictions: Dict[str, ConvictionLevel] = {}

    # ── Properties ────────────────────────────────────────

    @property
    def max_position_pct(self) -> float:
        return self._max_position_pct

    @property
    def min_position_pct(self) -> float:
        return self._min_position_pct

    @property
    def kelly_fraction(self) -> float:
        return self._kelly_fraction

    @property
    def symbols(self) -> List[str]:
        """등록된 종목 목록"""
        return list(self._convictions.keys())

    # ── 확신도 설정 ────────────────────────────────────────

    def set_conviction(
        self,
        symbol: str,
        base_conviction: float,
        catalyst_score: Optional[float] = None,
        kill_conditions_active: int = 0,
    ) -> ConvictionLevel:
        """종목 확신도 설정

        Args:
            symbol: 종목 코드
            base_conviction: 기본 확신도 (1-10)
            catalyst_score: CatalystTracker total score (None이면 부스트 0)
            kill_conditions_active: 활성 킬 조건 수 (0+)

        Returns:
            설정된 ConvictionLevel
        """
        base = _clamp(base_conviction, 1.0, 10.0)

        # catalyst_score → catalyst_boost (0-3 범위)
        # 스코어 0-2 → boost 0, 2-5 → boost 0-1.5, 5-10 → boost 1.5-3.0
        if catalyst_score is not None and catalyst_score > 0:
            catalyst_boost = _clamp(catalyst_score * 0.3, 0.0, 3.0)
        else:
            catalyst_boost = 0.0

        # kill_conditions_active → penalty (각 킬 조건당 1.0, 최대 5.0)
        kill_penalty = _clamp(float(kill_conditions_active) * 1.0, 0.0, 5.0)

        # 최종 확신도: base + boost - penalty, 1-10 클램핑
        final = _clamp(base + catalyst_boost - kill_penalty, 1.0, 10.0)

        level = ConvictionLevel(
            symbol=symbol,
            base_conviction=base,
            catalyst_boost=round(catalyst_boost, 4),
            kill_condition_penalty=kill_penalty,
            final_conviction=round(final, 4),
        )
        self._convictions[symbol] = level

        logger.info(
            "확신도 설정: %s base=%.1f boost=%.2f penalty=%.1f → final=%.2f",
            symbol, base, catalyst_boost, kill_penalty, final,
        )
        return level

    def get_conviction(self, symbol: str) -> Optional[ConvictionLevel]:
        """종목 확신도 조회"""
        return self._convictions.get(symbol)

    def remove_conviction(self, symbol: str) -> bool:
        """종목 확신도 제거"""
        if symbol in self._convictions:
            del self._convictions[symbol]
            return True
        return False

    # ── 포지션 사이징 ──────────────────────────────────────

    def size_position(self, symbol: str, total_capital: float) -> PositionSize:
        """단일 종목 포지션 사이즈 계산

        Half-Kelly 공식:
            conviction_normalized = (final_conviction - 1) / 9
            kelly_raw = conviction_normalized * kelly_fraction
            weight = min(kelly_raw, max_position_pct)

        Args:
            symbol: 종목 코드
            total_capital: 총 투자 자본 (KRW)

        Returns:
            PositionSize
        """
        level = self._convictions.get(symbol)
        if level is None:
            raise KeyError(f"확신도 미설정: {symbol}. set_conviction() 먼저 호출")

        if total_capital <= 0:
            raise ValueError(f"total_capital must be positive: {total_capital}")

        conviction = level.final_conviction

        # Half-Kelly
        conviction_normalized = (conviction - 1.0) / 9.0  # 1-10 → 0-1
        kelly_raw = conviction_normalized * self._kelly_fraction

        # 최소 비중 적용 (conviction 1이면 kelly_raw=0 → weight=0으로 스킵)
        if kelly_raw < self._min_position_pct:
            weight = 0.0
        else:
            weight = min(kelly_raw, self._max_position_pct)

        capped = kelly_raw > self._max_position_pct
        amount = weight * total_capital

        return PositionSize(
            symbol=symbol,
            conviction=conviction,
            weight=round(weight, 6),
            amount=round(amount, 2),
            kelly_raw=round(kelly_raw, 6),
            capped=capped,
        )

    def size_portfolio(
        self,
        symbols: List[str],
        total_capital: float,
    ) -> Dict[str, PositionSize]:
        """포트폴리오 전체 사이징

        Args:
            symbols: 종목 코드 리스트
            total_capital: 총 투자 자본 (KRW)

        Returns:
            {symbol: PositionSize} — weight > 0인 종목만 포함
        """
        result: Dict[str, PositionSize] = {}
        for sym in symbols:
            pos = self.size_position(sym, total_capital)
            if pos.weight > 0:
                result[sym] = pos

        total_weight = sum(p.weight for p in result.values())
        if total_weight > 1.0:
            logger.warning(
                "포트폴리오 총 비중 %.2f > 1.0 — 비중 조정 필요", total_weight,
            )

        logger.info(
            "포트폴리오 사이징: %d/%d 종목 편입, 총 비중 %.2f",
            len(result), len(symbols), total_weight,
        )
        return result

    # ── JSON 영속화 ────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """직렬화용 딕셔너리"""
        return {
            "version": _VERSION,
            "max_position_pct": self._max_position_pct,
            "min_position_pct": self._min_position_pct,
            "kelly_fraction": self._kelly_fraction,
            "convictions": {
                sym: asdict(level) for sym, level in self._convictions.items()
            },
            "saved_at": datetime.now().isoformat(),
        }

    def save(self, path: str) -> None:
        """JSON 파일로 저장"""
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("확신 사이저 저장: %s", file_path)

    @classmethod
    def load(cls, path: str) -> ConvictionSizer:
        """JSON 파일에서 복원"""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"상태 파일 없음: {file_path}")

        data = json.loads(file_path.read_text(encoding="utf-8"))

        sizer = cls(
            max_position_pct=data["max_position_pct"],
            min_position_pct=data["min_position_pct"],
            kelly_fraction=data["kelly_fraction"],
        )

        for sym, conv_data in data.get("convictions", {}).items():
            level = ConvictionLevel(**conv_data)
            sizer._convictions[sym] = level

        logger.info(
            "확신 사이저 복원: %d 종목, version=%s",
            len(sizer._convictions), data.get("version", "unknown"),
        )
        return sizer

    def __repr__(self) -> str:
        return (
            f"ConvictionSizer(max={self._max_position_pct:.0%}, "
            f"min={self._min_position_pct:.0%}, "
            f"kelly={self._kelly_fraction}, "
            f"symbols={len(self._convictions)})"
        )
