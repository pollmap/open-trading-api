"""매크로 레짐 대시보드 — Druckenmiller의 "큰 그림" 시스템

10대 거시지표를 추적하고 4-state 레짐(확장/수축/위기/회복)을 판별한다.
MCP ECOS(18도구) + FRED(24도구)를 통해 실시간 데이터를 수집.

Stan Druckenmiller 철학: "큰 그림이 맞으면 종목은 덜 중요하다."
- 레짐이 '확장'이면 → 공격적 주식 비중
- 레짐이 '위기'이면 → 현금+금으로 방어
- 레짐 전환 시 → 포트폴리오 즉시 재구성

Usage:
    from kis_backtest.portfolio.macro_regime import MacroRegimeDashboard

    dashboard = MacroRegimeDashboard()

    # MCP에서 데이터 수집
    await dashboard.fetch_indicators(mcp_provider)

    # 레짐 판별
    regime = dashboard.classify_regime()
    print(regime)  # Regime.EXPANSION

    # 권장 자산배분
    alloc = dashboard.recommended_allocation()
    print(alloc)   # {"equity": 0.7, "crypto": 0.2, "cash": 0.1}

    # 대시보드 요약
    print(dashboard.summary())
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider

logger = logging.getLogger(__name__)


class Regime(str, Enum):
    """매크로 레짐 4-state"""
    EXPANSION = "expansion"    # 확장: 성장↑ 금리→/↑ 유동성↑
    CONTRACTION = "contraction"  # 수축: 성장↓ 금리↑ 유동성↓
    CRISIS = "crisis"          # 위기: 급락, 스프레드 확대, 변동성 급등
    RECOVERY = "recovery"      # 회복: 금리↓ 유동성↑ 성장 반등 시작


# 레짐별 권장 자산배분 (Druckenmiller 스타일)
REGIME_ALLOCATION: Dict[Regime, Dict[str, float]] = {
    Regime.EXPANSION: {
        "equity": 0.70,
        "crypto": 0.20,
        "cash": 0.10,
    },
    Regime.CONTRACTION: {
        "equity": 0.20,
        "bond": 0.50,
        "gold": 0.20,
        "cash": 0.10,
    },
    Regime.CRISIS: {
        "cash": 0.70,
        "gold": 0.20,
        "inverse": 0.10,
    },
    Regime.RECOVERY: {
        "equity": 0.50,
        "crypto": 0.15,
        "bond": 0.20,
        "cash": 0.15,
    },
}


@dataclass
class MacroIndicator:
    """단일 거시지표

    Attributes:
        name: 지표명
        value: 현재 값
        prev_value: 이전 값 (전기 or 전월)
        unit: 단위 (%, bp, 조원 등)
        source: 데이터 출처 (ECOS, FRED 등)
        signal: 시그널 (-1: 부정, 0: 중립, +1: 긍정)
        updated_at: 업데이트 시점
    """
    name: str
    value: Optional[float] = None
    prev_value: Optional[float] = None
    unit: str = ""
    source: str = ""
    signal: int = 0  # -1, 0, +1
    updated_at: Optional[str] = None

    @property
    def change(self) -> Optional[float]:
        """전기 대비 변화"""
        if self.value is not None and self.prev_value is not None:
            return self.value - self.prev_value
        return None

    @property
    def change_pct(self) -> Optional[float]:
        """전기 대비 변화율 (%)"""
        if self.change is not None and self.prev_value and self.prev_value != 0:
            return (self.change / abs(self.prev_value)) * 100
        return None


# 10대 거시지표 정의
#
# 2026-04-11 Sprint 2 수정 (R11 silent-fail 버그 수리):
# 기존 `ecos_get_indicator`와 `fred_get_series`는 Nexus MCP에 등록되지 않은
# 도구였음 (2일간 try/except가 실패를 삼켜 "검증 완료" 잘못 기록).
# 실제 nexus MCP `discover_tools()` 결과:
#   - ECOS: ecos_get_base_rate / ecos_get_gdp / ecos_get_m2 / ecos_get_exchange_rate
#           / ecos_get_stat_data (범용)
#   - FRED: macro_fred (유일)
INDICATOR_DEFINITIONS: List[Dict[str, str]] = [
    {"name": "기준금리", "source": "ECOS", "tool": "ecos_get_base_rate", "unit": "%"},
    {"name": "CPI (소비자물가)", "source": "ECOS", "tool": "ecos_get_stat_data", "unit": "%"},
    {"name": "GDP 성장률", "source": "ECOS", "tool": "ecos_get_gdp", "unit": "%"},
    {"name": "M2 통화량", "source": "ECOS", "tool": "ecos_get_m2", "unit": "조원"},
    {"name": "실업률", "source": "ECOS", "tool": "ecos_get_stat_data", "unit": "%"},
    {"name": "미국 기준금리", "source": "FRED", "tool": "macro_fred", "unit": "%"},
    {"name": "미국 CPI", "source": "FRED", "tool": "macro_fred", "unit": "%"},
    {"name": "원/달러 환율", "source": "ECOS", "tool": "ecos_get_exchange_rate", "unit": "원"},
    {"name": "유가 (WTI)", "source": "FRED", "tool": "macro_fred", "unit": "USD"},
    {"name": "신용스프레드", "source": "FRED", "tool": "macro_fred", "unit": "bp"},
]

# ECOS MCP 도구 호출 설정 (지표별 전용 도구 + 파라미터)
# GDP/M2/환율은 전용 도구, CPI/실업률은 범용 ecos_get_stat_data
_ECOS_INDICATOR_CONFIG: Dict[str, Dict[str, Any]] = {
    "CPI (소비자물가)": {
        "tool": "ecos_get_stat_data",
        "args": {
            "stat_code": "021Y125",
            "item_code": "0",
            "start_date": "202001",
        },
    },
    "GDP 성장률": {
        "tool": "ecos_get_gdp",
        "args": {},  # 기본값: 10년 전 ~ 최신
    },
    "M2 통화량": {
        "tool": "ecos_get_m2",
        "args": {},  # 기본값: 5년 전 ~ 최신
    },
    "실업률": {
        "tool": "ecos_get_stat_data",
        "args": {
            "stat_code": "028Y015",
            "item_code": "I81A",
            "start_date": "202001",
        },
    },
    "원/달러 환율": {
        "tool": "ecos_get_exchange_rate",
        "args": {"currency": "USD"},
    },
}

_FRED_SERIES_MAP: Dict[str, str] = {
    "미국 기준금리": "FEDFUNDS",
    "미국 CPI": "CPIAUCSL",
    "유가 (WTI)": "DCOILWTICO",
    "신용스프레드": "BAMLH0A0HYM2",  # ICE BofA High Yield Spread
}


@dataclass(frozen=True)
class RegimeResult:
    """레짐 판별 결과"""
    regime: Regime
    confidence: float           # 판별 신뢰도 (0.0 ~ 1.0)
    score: float                # 종합 스코어 (-10 ~ +10)
    positive_signals: int       # 긍정 시그널 수
    negative_signals: int       # 부정 시그널 수
    neutral_signals: int        # 중립 시그널 수
    allocation: Dict[str, float]  # 권장 자산배분

    def summary(self) -> str:
        """한줄 요약"""
        regime_emoji = {
            Regime.EXPANSION: "🟢",
            Regime.RECOVERY: "🔵",
            Regime.CONTRACTION: "🟡",
            Regime.CRISIS: "🔴",
        }
        emoji = regime_emoji.get(self.regime, "⚪")
        alloc_str = " / ".join(f"{k}={v:.0%}" for k, v in self.allocation.items())
        return (
            f"{emoji} 레짐: {self.regime.value.upper()} "
            f"(신뢰도={self.confidence:.0%}, 스코어={self.score:+.1f}) "
            f"| 시그널: +{self.positive_signals}/-{self.negative_signals}/={self.neutral_signals} "
            f"| 배분: {alloc_str}"
        )


class MacroRegimeDashboard:
    """매크로 레짐 대시보드

    10대 거시지표를 추적하고 4-state 레짐을 판별한다.
    MCP 도구를 통해 실시간 데이터를 수집.
    """

    def __init__(self, state_file: Optional[str] = None) -> None:
        self._indicators: Dict[str, MacroIndicator] = {}
        self._state_file = state_file
        self._last_regime: Optional[RegimeResult] = None

        # 기본 지표 초기화
        for defn in INDICATOR_DEFINITIONS:
            self._indicators[defn["name"]] = MacroIndicator(
                name=defn["name"],
                source=defn["source"],
                unit=defn["unit"],
            )

        if state_file:
            self._load(state_file)

    @property
    def indicators(self) -> Dict[str, MacroIndicator]:
        """전체 지표 딕셔너리"""
        return dict(self._indicators)

    @property
    def last_regime(self) -> Optional[RegimeResult]:
        """마지막 레짐 판별 결과"""
        return self._last_regime

    # ── 데이터 수집 ──────────────────────────────────────────

    def update_indicator(
        self,
        name: str,
        value: float,
        prev_value: Optional[float] = None,
    ) -> MacroIndicator:
        """지표 수동 업데이트"""
        if name not in self._indicators:
            self._indicators[name] = MacroIndicator(name=name)

        ind = self._indicators[name]
        prev = prev_value if prev_value is not None else ind.value

        self._indicators[name] = MacroIndicator(
            name=name,
            value=value,
            prev_value=prev,
            unit=ind.unit,
            source=ind.source,
            signal=self._compute_signal(name, value, prev),
            updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        self._save()
        return self._indicators[name]

    async def fetch_indicators(self, mcp: MCPDataProvider) -> Dict[str, MacroIndicator]:
        """MCP를 통해 전체 지표 수집

        Nexus MCP 실제 도구 이름 (2026-04-11 Sprint 2 수정):
            ECOS: ecos_get_base_rate / gdp / m2 / exchange_rate / stat_data
            FRED: macro_fred
        실패한 지표는 이전 값 유지 (try/except로 graceful degradation).
        """
        # 기준금리 (전용 도구 get_risk_free_rate → 내부적으로 ecos_get_base_rate)
        await self._fetch_base_rate(mcp)

        # ECOS 지표들 (지표별 전용 도구 또는 범용 stat_data)
        for name, config in _ECOS_INDICATOR_CONFIG.items():
            await self._fetch_ecos_indicator(mcp, name, config)

        # FRED 지표들 (macro_fred 통일)
        for name, series_id in _FRED_SERIES_MAP.items():
            await self._fetch_fred_series(mcp, name, series_id)

        self._save()
        return dict(self._indicators)

    async def _fetch_base_rate(self, mcp: MCPDataProvider) -> None:
        """한국 기준금리 수집"""
        try:
            rate = await mcp.get_risk_free_rate()
            self.update_indicator("기준금리", rate * 100)  # 0.0275 → 2.75
        except Exception as e:
            logger.warning("기준금리 수집 실패: %s", e)

    async def _fetch_ecos_indicator(
        self, mcp: MCPDataProvider, name: str, config: Dict[str, Any]
    ) -> None:
        """ECOS 지표 수집 (2026-04-11 Sprint 2 수정).

        기존 `ecos_get_indicator`는 Nexus MCP 미등록 도구였음 (R11 silent fail).
        실제로는 지표별 전용 도구(get_gdp/get_m2/get_exchange_rate)
        또는 범용 `ecos_get_stat_data` 사용. config에 tool과 args를 분리 저장.
        """
        tool = config.get("tool", "ecos_get_stat_data")
        args = config.get("args", {})
        try:
            result = await mcp._call_vps_tool(tool, args)
            # MCP 서버 에러 문자열 감지 ({"success": true, "data": "Unknown tool: ..."})
            if isinstance(result, dict) and isinstance(result.get("data"), str):
                logger.warning(
                    "ECOS %s MCP 서버 에러 (%s): %s", name, tool, result["data"]
                )
                return
            value = _extract_numeric_value(result)
            if value is not None:
                self.update_indicator(name, value)
        except Exception as e:
            logger.warning("ECOS %s 수집 실패 (%s): %s", name, tool, e)

    async def _fetch_fred_series(
        self, mcp: MCPDataProvider, name: str, series_id: str
    ) -> None:
        """FRED 시리즈 수집 (2026-04-11 Sprint 2 수정).

        기존 `fred_get_series`는 Nexus MCP 미등록 도구였음 (R11 silent fail).
        실제 이름은 `macro_fred` (nexus-finance MCP 398도구 중 하나).
        """
        try:
            result = await mcp._call_vps_tool(
                "macro_fred",
                {"series_id": series_id, "limit": 2},
            )
            # MCP 서버 에러 문자열 감지
            if isinstance(result, dict) and isinstance(result.get("data"), str):
                logger.warning(
                    "FRED %s MCP 서버 에러 (macro_fred): %s", name, result["data"]
                )
                return
            values = _extract_fred_values(result)
            if values:
                current = values[-1]
                prev = values[-2] if len(values) >= 2 else None
                self.update_indicator(name, current, prev)
        except Exception as e:
            logger.warning("FRED %s 수집 실패 (macro_fred): %s", name, e)

    # ── 시그널 계산 ──────────────────────────────────────────

    @staticmethod
    def _compute_signal(
        name: str,
        value: Optional[float],
        prev_value: Optional[float],
    ) -> int:
        """지표별 시그널 판별 (-1, 0, +1)

        Druckenmiller 프레임: 성장·유동성 상승 = 긍정, 금리·인플레 상승 = 부정
        """
        if value is None or prev_value is None:
            return 0

        change = value - prev_value

        # 변화가 너무 작으면 중립
        if abs(change) < 0.01:
            return 0

        # 지표별 방향성 (양수 변화가 긍정인지 부정인지)
        positive_when_up = {
            "GDP 성장률", "M2 통화량",
        }
        negative_when_up = {
            "기준금리", "CPI (소비자물가)", "미국 기준금리", "미국 CPI",
            "실업률", "원/달러 환율", "유가 (WTI)", "신용스프레드",
        }

        if name in positive_when_up:
            return 1 if change > 0 else -1
        elif name in negative_when_up:
            return -1 if change > 0 else 1
        return 0

    # ── 레짐 판별 ────────────────────────────────────────────

    def classify_regime(self) -> RegimeResult:
        """현재 거시 환경의 레짐을 판별

        알고리즘:
        1. 각 지표의 시그널 합산 → 종합 스코어
        2. 스코어 + 위기 트리거 → 4-state 레짐 결정
        3. 신뢰도 = 유효 지표 수 / 전체 지표 수

        위기 트리거: 신용스프레드 > 500bp OR 3개 이상 지표 급변
        """
        signals = [ind.signal for ind in self._indicators.values() if ind.value is not None]

        positive = sum(1 for s in signals if s > 0)
        negative = sum(1 for s in signals if s < 0)
        neutral = sum(1 for s in signals if s == 0)
        total_score = sum(signals)

        # 유효 지표 비율 → 신뢰도
        total_indicators = len(INDICATOR_DEFINITIONS)
        valid_count = len(signals)
        confidence = valid_count / total_indicators if total_indicators else 0.0

        # 위기 트리거 체크
        is_crisis = self._check_crisis_triggers()

        # 레짐 결정
        if is_crisis:
            regime = Regime.CRISIS
        elif total_score >= 3:
            regime = Regime.EXPANSION
        elif total_score <= -3:
            regime = Regime.CONTRACTION
        elif total_score > 0:
            regime = Regime.RECOVERY
        elif total_score < 0:
            regime = Regime.CONTRACTION
        else:
            # 스코어 0: 이전 레짐 유지, 없으면 RECOVERY
            regime = self._last_regime.regime if self._last_regime else Regime.RECOVERY

        allocation = dict(REGIME_ALLOCATION[regime])

        result = RegimeResult(
            regime=regime,
            confidence=round(confidence, 2),
            score=total_score,
            positive_signals=positive,
            negative_signals=negative,
            neutral_signals=neutral,
            allocation=allocation,
        )
        self._last_regime = result
        self._save()
        return result

    def _check_crisis_triggers(self) -> bool:
        """위기 트리거 체크

        조건 (하나라도 충족 시 위기):
        1. 신용스프레드 > 500bp (금융 위기 수준)
        2. 부정 시그널 ≥ 7 (10개 중 70%)
        """
        spread = self._indicators.get("신용스프레드")
        if spread and spread.value is not None and spread.value > 500:
            return True

        negative_count = sum(
            1 for ind in self._indicators.values()
            if ind.signal < 0
        )
        return negative_count >= 7

    # ── 자산배분 권고 ────────────────────────────────────────

    def recommended_allocation(self) -> Dict[str, float]:
        """현재 레짐 기반 권장 자산배분

        레짐 미판별 시 자동으로 classify_regime() 호출.
        """
        if self._last_regime is None:
            self.classify_regime()
        assert self._last_regime is not None
        return dict(self._last_regime.allocation)

    # ── 대시보드 출력 ────────────────────────────────────────

    def summary(self) -> str:
        """대시보드 전체 요약"""
        lines = [
            "=" * 60,
            "  매크로 레짐 대시보드 (Druckenmiller 스타일)",
            f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "=" * 60,
            "",
            "  지표                      현재값    변화    시그널",
            "  " + "-" * 54,
        ]

        for ind in self._indicators.values():
            val_str = f"{ind.value:.2f}" if ind.value is not None else "N/A"
            chg_str = ""
            if ind.change is not None:
                chg_str = f"{ind.change:+.2f}"
            sig_map = {-1: "▼ 부정", 0: "─ 중립", 1: "▲ 긍정"}
            sig_str = sig_map.get(ind.signal, "?")
            lines.append(
                f"  {ind.name:<22s}  {val_str:>8s} {ind.unit:<4s} {chg_str:>8s}  {sig_str}"
            )

        if self._last_regime:
            lines.append("")
            lines.append(self._last_regime.summary())

        lines.append("=" * 60)
        return "\n".join(lines)

    def indicator_table(self) -> List[Dict[str, Any]]:
        """지표 테이블 (JSON/표 출력용)"""
        return [
            {
                "name": ind.name,
                "value": ind.value,
                "prev_value": ind.prev_value,
                "change": ind.change,
                "change_pct": ind.change_pct,
                "unit": ind.unit,
                "source": ind.source,
                "signal": ind.signal,
                "updated_at": ind.updated_at,
            }
            for ind in self._indicators.values()
        ]

    # ── 영속성 ───────────────────────────────────────────────

    def _save(self) -> None:
        """JSON 파일에 상태 저장"""
        if not self._state_file:
            return
        path = Path(self._state_file)
        path.parent.mkdir(parents=True, exist_ok=True)

        data: Dict[str, Any] = {
            "version": 1,
            "updated_at": datetime.now().isoformat(),
            "indicators": {
                name: {
                    "name": ind.name,
                    "value": ind.value,
                    "prev_value": ind.prev_value,
                    "unit": ind.unit,
                    "source": ind.source,
                    "signal": ind.signal,
                    "updated_at": ind.updated_at,
                }
                for name, ind in self._indicators.items()
            },
        }
        if self._last_regime:
            data["last_regime"] = {
                "regime": self._last_regime.regime.value,
                "confidence": self._last_regime.confidence,
                "score": self._last_regime.score,
            }

        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self, state_file: str) -> None:
        """JSON 파일에서 상태 로드"""
        path = Path(state_file)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for name, item in data.get("indicators", {}).items():
                if name in self._indicators:
                    self._indicators[name] = MacroIndicator(**item)
            logger.info("매크로 지표 %d개 로드 (%s)", len(self._indicators), state_file)
        except Exception as e:
            logger.warning("매크로 지표 로드 실패: %s", e)


# ── 유틸 함수 ────────────────────────────────────────────────

def _extract_numeric_value(result: Dict[str, Any]) -> Optional[float]:
    """MCP 결과에서 숫자 값 추출"""
    if not result:
        return None

    data = result.get("data", result.get("result", result))

    if isinstance(data, (int, float)):
        return float(data)

    if isinstance(data, list) and data:
        last = data[-1]
        if isinstance(last, (int, float)):
            return float(last)
        if isinstance(last, dict):
            for key in ("value", "DATA_VALUE", "data_value"):
                if key in last:
                    try:
                        return float(last[key])
                    except (ValueError, TypeError):
                        continue

    if isinstance(data, dict):
        for key in ("value", "DATA_VALUE", "data_value", "rate"):
            if key in data:
                try:
                    return float(data[key])
                except (ValueError, TypeError):
                    continue

    return None


def _extract_fred_values(result: Dict[str, Any]) -> List[float]:
    """FRED 결과에서 시계열 값 추출"""
    if not result:
        return []

    data = result.get("data", result.get("result", result))

    if isinstance(data, list):
        values = []
        for item in data:
            if isinstance(item, (int, float)):
                values.append(float(item))
            elif isinstance(item, dict):
                for key in ("value", "DATA_VALUE"):
                    if key in item:
                        try:
                            values.append(float(item[key]))
                            break
                        except (ValueError, TypeError):
                            continue
        return values

    return []
