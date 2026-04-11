"""
Luxon Terminal — ConvictionBridge (Sprint 4 STEP 3 / 4E)

3중 게이트 어댑터: `Phase1CheckpointResult` → macro regime → `ConvictionSizer`
→ dry-run `OrderProposal`. Phase 2 GothamGraph 진입 전 Phase 1 마지막 재료.

원칙: 신규 계산 로직 0줄(ConvictionSizer 래핑만), 모든 탈락을 감사 로그로
기록, 4-state regime 전부 매핑(Druckenmiller 회복기 매수 보존), 실 주문 금지.

게이트 흐름은 `ConvictionBridge.propose()` 독스트링과 `_REGIME_ACTION` 표를
정의의 유일 출처로 사용한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from kis_backtest.portfolio.macro_regime import Regime

if TYPE_CHECKING:
    from kis_backtest.luxon.integration.phase1_pipeline import (
        Phase1CheckpointResult,
    )
    from kis_backtest.portfolio.conviction_sizer import ConvictionSizer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action constants — 자유 문자열 대신 고정 상수로 감사 로그 일관성 보장
# ---------------------------------------------------------------------------

ACTION_BUY = "BUY"
ACTION_HOLD = "HOLD"
ACTION_REDUCE = "REDUCE"
ACTION_SELL = "SELL"

# Regime → BUY 경로 포지션 배수 (EXPANSION=완전, RECOVERY=Druckenmiller 회복기).
# CRISIS/CONTRACTION은 BUY 경로에 도달하지 않으므로 포함하지 않는다.
_BUY_MULTIPLIER: dict[Regime, float] = {
    Regime.EXPANSION: 1.0,
    Regime.RECOVERY: 0.8,
}


@dataclass(frozen=True)
class OrderProposal:
    """Dry-run 주문 제안. 3중 게이트 통과/탈락 기록을 포함한 감사 가능 스냅샷.

    Attributes:
        symbol: 종목 코드 (005930 등).
        action: BUY / HOLD / REDUCE / SELL 중 하나.
        position_pct: 최종 포트폴리오 비중 (0.0 ~ max_position_pct).
            HOLD/REDUCE/SELL은 0.0, BUY만 양수.
        conviction: 최종 확신도 (1-10, ConvictionSizer 계산 결과).
            Gate 2 탈락 시에는 입력 base_conviction 그대로.
        reason: 왜 이 결정을 내렸는지 (감사 로그용).
        passed_gates: 통과한 게이트 목록. 순서 보존 tuple.
            예: ("gate1", "gate2:expansion", "gate3")
                ("gate1:partial", "gate2:crisis")
    """
    symbol: str
    action: str
    position_pct: float
    conviction: float
    reason: str
    passed_gates: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_actionable(self) -> bool:
        """BUY 또는 SELL처럼 즉시 실행 가능한 제안인가?"""
        return self.action in (ACTION_BUY, ACTION_SELL)


class ConvictionBridge:
    """3중 게이트 어댑터: Phase1CheckpointResult → OrderProposal.

    ConvictionSizer를 얇게 래핑하며, 내부 state를 매 propose() 호출마다 갱신한다.
    stateless 래퍼는 아니다(set_conviction이 내부 dict에 저장) — 하지만 동일
    symbol을 여러 번 propose()해도 항상 최신 base_conviction으로 덮어쓰므로 안전.

    Args:
        sizer: 외부에서 주입된 ConvictionSizer. 수정하지 않고 호출만 한다.
    """

    def __init__(self, sizer: "ConvictionSizer") -> None:
        self._sizer = sizer

    @property
    def sizer(self) -> "ConvictionSizer":
        return self._sizer

    def propose(
        self,
        checkpoint: "Phase1CheckpointResult",
        symbol: str,
        base_conviction: float,
        total_capital: float,
    ) -> OrderProposal:
        """3중 게이트 통과 여부에 따른 주문 제안.

        이 메서드는 **절대 raise 하지 않는다**. 모든 실패는 HOLD + reason으로
        변환한다. 단 total_capital <= 0 같은 명백한 caller 실수는 ValueError
        허용 — 이건 게이트 문제가 아니라 프로그래밍 에러.

        Args:
            checkpoint: Phase1Pipeline.checkpoint() 결과.
            symbol: 매수/매도 대상 종목 코드.
            base_conviction: CUFA 보고서 또는 수동 확신도 (1-10).
            total_capital: 총 투자 자본 (KRW).

        Returns:
            OrderProposal (항상 반환).
        """
        if total_capital <= 0:
            raise ValueError(f"total_capital must be positive: {total_capital}")

        passed: list[str] = []

        def _mk(
            action: str,
            pct: float,
            conv: float,
            reason: str,
        ) -> OrderProposal:
            """OrderProposal 생성 + 로깅 단축 헬퍼."""
            level = logging.WARNING if action == ACTION_SELL else logging.INFO
            logger.log(level, "ConvictionBridge %s %s: %s", action, symbol, reason)
            return OrderProposal(
                symbol, action, pct, conv, reason, tuple(passed)
            )

        # ── Gate 1: 파이프라인 건강도 ──────────────────────────
        if not checkpoint.success and not checkpoint.partial_success:
            return _mk(
                ACTION_HOLD,
                0.0,
                base_conviction,
                f"gate1: pipeline dead — errors={len(checkpoint.errors)}",
            )
        passed.append("gate1" if checkpoint.success else "gate1:partial")

        # ── Gate 2: 매크로 레짐 매핑 ───────────────────────────
        regime_result = checkpoint.regime_result
        if regime_result is None:
            return _mk(
                ACTION_HOLD,
                0.0,
                base_conviction,
                f"gate2: regime unknown — classify_regime() returned None "
                f"(indicator_count={checkpoint.macro_indicator_count})",
            )

        regime = regime_result.regime
        regime_ctx = (
            f"confidence={regime_result.confidence:.0%}, "
            f"score={regime_result.score:+.1f}"
        )

        if regime is Regime.CRISIS:
            passed.append("gate2:crisis")
            return _mk(
                ACTION_SELL,
                0.0,
                base_conviction,
                f"gate2: CRISIS regime ({regime_ctx}) — liquidate signal",
            )

        if regime is Regime.CONTRACTION:
            passed.append("gate2:contraction")
            return _mk(
                ACTION_REDUCE,
                0.0,
                base_conviction,
                f"gate2: CONTRACTION regime ({regime_ctx}) — "
                f"no new entries, reduce existing",
            )

        # EXPANSION or RECOVERY → BUY 경로. CRISIS/CONTRACTION은 위에서 반환됐고
        # None은 더 위에서 거절됐으므로 regime은 _BUY_MULTIPLIER에 반드시 존재.
        # 미래 Regime enum 확장 시 KeyError로 fail-fast.
        regime_mult = _BUY_MULTIPLIER[regime]
        passed.append(f"gate2:{regime.value}")

        # ── Gate 3: 확신 사이징 ────────────────────────────────
        level = self._sizer.set_conviction(
            symbol=symbol, base_conviction=base_conviction
        )
        position = self._sizer.size_position(
            symbol=symbol, total_capital=total_capital
        )

        if position.weight <= 0.0:
            return _mk(
                ACTION_HOLD,
                0.0,
                level.final_conviction,
                f"gate3: conviction too low — "
                f"final={level.final_conviction:.2f}, "
                f"kelly_raw={position.kelly_raw:.4f} < "
                f"min={self._sizer.min_position_pct:.4f}",
            )

        passed.append("gate3")
        effective_weight = round(position.weight * regime_mult, 6)
        return _mk(
            ACTION_BUY,
            effective_weight,
            level.final_conviction,
            f"all gates passed — regime={regime.value} "
            f"(mult={regime_mult:.2f}), "
            f"conviction={level.final_conviction:.2f}, "
            f"kelly={position.kelly_raw:.4f}, "
            f"weight={position.weight:.4f} → effective={effective_weight:.4f}",
        )


__all__ = [
    "ConvictionBridge",
    "OrderProposal",
    "ACTION_BUY",
    "ACTION_HOLD",
    "ACTION_REDUCE",
    "ACTION_SELL",
]
