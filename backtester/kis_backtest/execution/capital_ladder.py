"""자본 래더 (점진적 자본 배포)

모의투자 → 소액 → 중간 → 전액 순으로 자본을 점진적으로 늘리며,
각 단계에서 Sharpe/MDD/기간 조건을 충족해야 다음 단계로 승격.

참고: Van Tharp position sizing, Nassim Taleb 바벨 전략

Usage:
    from kis_backtest.execution.capital_ladder import CapitalLadder, LadderConfig

    ladder = CapitalLadder(total_capital=10_000_000)
    print(ladder.current_stage)           # Stage.PAPER
    print(ladder.deployed_capital)        # 0 (페이퍼)

    # 매일 equity 업데이트
    ladder.update(equity=10_050_000)

    # 승격 체크
    if ladder.can_promote():
        ladder.promote()
        print(ladder.current_stage)       # Stage.SEED
        print(ladder.deployed_capital)    # 1_000_000 (10%)
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


class Stage(IntEnum):
    """자본 배포 단계"""
    PAPER = 0      # 모의투자 (0%)
    SEED = 1       # 시드 (10%)
    GROWTH = 2     # 성장 (30%)
    SCALE = 3      # 스케일 (60%)
    FULL = 4       # 전액 (100%)


@dataclass(frozen=True)
class StageConfig:
    """단계별 설정"""
    stage: Stage
    capital_pct: float      # 투입 비율
    min_days: int           # 최소 운용 일수
    min_sharpe: float       # 최소 Sharpe (연율)
    max_dd: float           # 최대 드로다운 (음수)
    label: str              # 표시 이름


# 기본 래더 단계 설정
DEFAULT_STAGES: List[StageConfig] = [
    StageConfig(Stage.PAPER, 0.00, 20, 0.0,  -0.15, "모의투자"),
    StageConfig(Stage.SEED,  0.10, 20, 0.2,  -0.10, "시드 10%"),
    StageConfig(Stage.GROWTH, 0.30, 15, 0.3, -0.08, "성장 30%"),
    StageConfig(Stage.SCALE, 0.60, 10, 0.4,  -0.07, "스케일 60%"),
    StageConfig(Stage.FULL,  1.00,  0, 0.0,   0.00, "전액 100%"),
]


@dataclass(frozen=True)
class LadderConfig:
    """래더 전체 설정"""
    total_capital: float = 10_000_000
    stages: List[StageConfig] = field(default_factory=lambda: list(DEFAULT_STAGES))
    auto_demote: bool = True          # MDD 초과 시 자동 강등
    demote_dd_multiplier: float = 1.5  # 승격 MDD × 1.5 초과 시 강등
    state_file: Optional[str] = None   # 상태 저장 파일 경로


@dataclass
class StageHistory:
    """단계 전환 히스토리"""
    stage: Stage
    action: str           # "promote" | "demote" | "init"
    timestamp: str
    reason: str
    sharpe: float = 0.0
    max_dd: float = 0.0
    days_in_stage: int = 0


@dataclass
class DailyEquity:
    """일별 자산 기록"""
    date: str
    equity: float
    daily_return: float = 0.0


@dataclass
class LadderStatus:
    """현재 래더 상태"""
    stage: Stage
    stage_label: str
    capital_pct: float
    deployed_capital: float
    days_in_stage: int
    current_sharpe: float
    current_dd: float
    can_promote: bool
    promote_blockers: List[str]
    history: List[StageHistory]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage.name,
            "stage_label": self.stage_label,
            "capital_pct": f"{self.capital_pct:.0%}",
            "deployed_capital": f"{self.deployed_capital:,.0f}",
            "days_in_stage": self.days_in_stage,
            "current_sharpe": round(self.current_sharpe, 3),
            "current_dd": f"{self.current_dd:.1%}",
            "can_promote": self.can_promote,
            "promote_blockers": self.promote_blockers,
            "history": [
                {
                    "stage": h.stage.name,
                    "action": h.action,
                    "timestamp": h.timestamp,
                    "reason": h.reason,
                }
                for h in self.history
            ],
        }


class CapitalLadder:
    """자본 래더 관리자

    모의투자(0%) → 시드(10%) → 성장(30%) → 스케일(60%) → 전액(100%)
    각 단계에서 Sharpe/MDD/기간 조건을 충족해야 승격.
    MDD 초과 시 자동 강등.
    """

    def __init__(self, config: Optional[LadderConfig] = None):
        self._config = config or LadderConfig()
        self._stage_idx: int = 0
        self._equity_history: List[DailyEquity] = []
        self._stage_start_idx: int = 0  # 현재 단계 시작 인덱스
        self._history: List[StageHistory] = []
        self._peak_equity: float = self._config.total_capital

        # 초기 히스토리
        self._history.append(StageHistory(
            stage=Stage.PAPER,
            action="init",
            timestamp=datetime.now().isoformat(),
            reason="래더 초기화",
        ))

        # 상태 파일에서 복원
        if self._config.state_file:
            self._load_state()

    # ── Properties ────────────────────────────────────────

    @property
    def current_stage(self) -> Stage:
        return self._config.stages[self._stage_idx].stage

    @property
    def current_stage_config(self) -> StageConfig:
        return self._config.stages[self._stage_idx]

    @property
    def deployed_capital(self) -> float:
        return self._config.total_capital * self.current_stage_config.capital_pct

    @property
    def days_in_stage(self) -> int:
        return len(self._equity_history) - self._stage_start_idx

    @property
    def stage_equity(self) -> List[float]:
        """현재 단계의 equity 시계열"""
        return [e.equity for e in self._equity_history[self._stage_start_idx:]]

    @property
    def stage_returns(self) -> List[float]:
        """현재 단계의 일간 수익률"""
        return [e.daily_return for e in self._equity_history[self._stage_start_idx:]
                if e.daily_return is not None]

    # ── 핵심 메서드 ────────────────────────────────────────

    def update(self, equity: float, dt: Optional[str] = None) -> Optional[str]:
        """일일 equity 업데이트

        Args:
            equity: 오늘 총 자산
            dt: 날짜 (없으면 오늘)

        Returns:
            이벤트 메시지 (승격/강등 시) 또는 None
        """
        dt_str = dt or date.today().isoformat()

        # 일간 수익률 계산
        if self._equity_history:
            prev = self._equity_history[-1].equity
            daily_ret = (equity - prev) / prev if prev > 0 else 0.0
        else:
            daily_ret = 0.0

        self._equity_history.append(DailyEquity(
            date=dt_str, equity=equity, daily_return=daily_ret,
        ))

        # peak 업데이트
        self._peak_equity = max(self._peak_equity, equity)

        # 자동 강등 체크
        if self._config.auto_demote and self._stage_idx > 0:
            event = self._check_auto_demote(equity)
            if event:
                return event

        return None

    def can_promote(self) -> tuple[bool, List[str]]:
        """승격 가능 여부 + 차단 사유"""
        if self._stage_idx >= len(self._config.stages) - 1:
            return False, ["이미 최고 단계"]

        cfg = self.current_stage_config
        blockers: List[str] = []

        # 최소 일수
        if self.days_in_stage < cfg.min_days:
            blockers.append(
                f"기간 부족: {self.days_in_stage}/{cfg.min_days}일"
            )

        # Sharpe 체크
        current_sharpe = self._calc_sharpe()
        if current_sharpe < cfg.min_sharpe:
            blockers.append(
                f"Sharpe 부족: {current_sharpe:.3f} < {cfg.min_sharpe}"
            )

        # MDD 체크
        current_dd = self._calc_max_dd()
        if current_dd < cfg.max_dd:
            blockers.append(
                f"MDD 초과: {current_dd:.1%} < {cfg.max_dd:.1%}"
            )

        return len(blockers) == 0, blockers

    def promote(self, force: bool = False) -> str:
        """다음 단계로 승격

        Args:
            force: True면 조건 무시하고 강제 승격

        Returns:
            승격 결과 메시지
        """
        if self._stage_idx >= len(self._config.stages) - 1:
            return "이미 최고 단계 (FULL)"

        ok, blockers = self.can_promote()
        if not ok and not force:
            return f"승격 불가: {'; '.join(blockers)}"

        old_stage = self.current_stage
        sharpe = self._calc_sharpe()
        dd = self._calc_max_dd()

        self._stage_idx += 1
        self._stage_start_idx = len(self._equity_history)
        self._peak_equity = self._equity_history[-1].equity if self._equity_history else self._config.total_capital

        new_stage = self.current_stage
        reason = "강제 승격" if force else f"조건 충족 (Sharpe={sharpe:.3f}, DD={dd:.1%})"

        self._history.append(StageHistory(
            stage=new_stage,
            action="promote",
            timestamp=datetime.now().isoformat(),
            reason=reason,
            sharpe=sharpe,
            max_dd=dd,
            days_in_stage=self.days_in_stage,
        ))

        self._save_state()

        msg = (
            f"승격: {old_stage.name}({self._config.stages[self._stage_idx - 1].label}) "
            f"→ {new_stage.name}({self.current_stage_config.label}) "
            f"| 배포 자본: {self.deployed_capital:,.0f}원"
        )
        logger.info(msg)
        return msg

    def demote(self, reason: str = "수동 강등") -> str:
        """이전 단계로 강등"""
        if self._stage_idx <= 0:
            return "이미 최저 단계 (PAPER)"

        old_stage = self.current_stage
        sharpe = self._calc_sharpe()
        dd = self._calc_max_dd()

        self._stage_idx -= 1
        self._stage_start_idx = len(self._equity_history)
        self._peak_equity = self._equity_history[-1].equity if self._equity_history else self._config.total_capital

        new_stage = self.current_stage

        self._history.append(StageHistory(
            stage=new_stage,
            action="demote",
            timestamp=datetime.now().isoformat(),
            reason=reason,
            sharpe=sharpe,
            max_dd=dd,
            days_in_stage=self.days_in_stage,
        ))

        self._save_state()

        msg = (
            f"강등: {old_stage.name} → {new_stage.name}({self.current_stage_config.label}) "
            f"| 사유: {reason}"
        )
        logger.warning(msg)
        return msg

    def status(self) -> LadderStatus:
        """현재 래더 상태 조회"""
        ok, blockers = self.can_promote()
        return LadderStatus(
            stage=self.current_stage,
            stage_label=self.current_stage_config.label,
            capital_pct=self.current_stage_config.capital_pct,
            deployed_capital=self.deployed_capital,
            days_in_stage=self.days_in_stage,
            current_sharpe=self._calc_sharpe(),
            current_dd=self._calc_max_dd(),
            can_promote=ok,
            promote_blockers=blockers,
            history=self._history,
        )

    def get_pipeline_capital(self) -> float:
        """PipelineConfig.total_capital에 전달할 값

        현재 단계에 맞는 투입 자본을 반환.
        QuantPipeline과 연동 시 사용.
        """
        return self.deployed_capital

    # ── 내부 계산 ────────────────────────────────────────

    def _calc_sharpe(self) -> float:
        """현재 단계 Sharpe"""
        rets = self.stage_returns
        if len(rets) < 5:
            return 0.0
        n = len(rets)
        mean_r = sum(rets) / n
        var_r = sum((r - mean_r) ** 2 for r in rets) / max(n - 1, 1)
        std_r = math.sqrt(var_r) if var_r > 0 else 1e-10
        return (mean_r / std_r) * math.sqrt(252)

    def _calc_max_dd(self) -> float:
        """현재 단계 최대 드로다운"""
        equities = self.stage_equity
        if len(equities) < 2:
            return 0.0
        peak = equities[0]
        max_dd = 0.0
        for eq in equities:
            peak = max(peak, eq)
            dd = (eq - peak) / peak if peak > 0 else 0.0
            max_dd = min(max_dd, dd)
        return max_dd

    def _check_auto_demote(self, equity: float) -> Optional[str]:
        """MDD 초과 시 자동 강등"""
        cfg = self.current_stage_config
        if cfg.max_dd >= 0:
            return None

        current_dd = (equity - self._peak_equity) / self._peak_equity if self._peak_equity > 0 else 0.0
        demote_threshold = cfg.max_dd * self._config.demote_dd_multiplier

        if current_dd < demote_threshold:
            return self.demote(
                f"자동 강등: DD {current_dd:.1%} < 한도 {demote_threshold:.1%}"
            )
        return None

    # ── 상태 저장/복원 ────────────────────────────────────

    def _save_state(self) -> None:
        """상태를 파일에 저장"""
        if not self._config.state_file:
            return
        state = {
            "stage_idx": self._stage_idx,
            "stage_start_idx": self._stage_start_idx,
            "peak_equity": self._peak_equity,
            "equity_history": [
                {"date": e.date, "equity": e.equity, "daily_return": e.daily_return}
                for e in self._equity_history[-500:]  # 최근 500일만
            ],
            "history": [
                {
                    "stage": h.stage.value,
                    "action": h.action,
                    "timestamp": h.timestamp,
                    "reason": h.reason,
                    "sharpe": h.sharpe,
                    "max_dd": h.max_dd,
                    "days_in_stage": h.days_in_stage,
                }
                for h in self._history
            ],
            "saved_at": datetime.now().isoformat(),
        }
        path = Path(self._config.state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("래더 상태 저장: %s", path)

    def _load_state(self) -> None:
        """파일에서 상태 복원"""
        if not self._config.state_file:
            return
        path = Path(self._config.state_file)
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            self._stage_idx = state["stage_idx"]
            self._stage_start_idx = state["stage_start_idx"]
            self._peak_equity = state["peak_equity"]
            self._equity_history = [
                DailyEquity(**e) for e in state.get("equity_history", [])
            ]
            self._history = [
                StageHistory(stage=Stage(h["stage"]), **{k: v for k, v in h.items() if k != "stage"})
                for h in state.get("history", [])
            ]
            logger.info("래더 상태 복원: stage=%s, %d일 히스토리",
                        self.current_stage.name, len(self._equity_history))
        except Exception as e:
            logger.warning("래더 상태 복원 실패: %s", e)
