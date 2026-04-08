"""Ackman-Druckenmiller 통합 엔진 — 바텀업 확신 × 탑다운 매크로

Bill Ackman 철학 (바텀업):
- 카탈리스트 없으면 매수 없다
- 높은 확신 = 집중 투자
- "왜 지금?" 질문에 답할 수 있어야 매수

Stan Druckenmiller 철학 (탑다운):
- 큰 그림이 맞으면 종목은 덜 중요하다
- 레짐 전환 시 포트폴리오 즉시 재구성
- 확장기엔 공격적, 위기엔 현금

통합: catalyst_tracker + macro_regime → 종목별 투자 결정 + 포트폴리오 배분

Usage:
    from kis_backtest.portfolio.ackman_druckenmiller import AckmanDruckenmillerEngine
    from kis_backtest.portfolio.catalyst_tracker import CatalystTracker
    from kis_backtest.portfolio.macro_regime import MacroRegimeDashboard

    tracker = CatalystTracker()
    dashboard = MacroRegimeDashboard()

    engine = AckmanDruckenmillerEngine(tracker, dashboard)

    # 종목 평가
    decision = engine.evaluate_symbol("005930", base_conviction=7.0)
    print(decision.summary())

    # 포트폴리오 평가
    portfolio = engine.evaluate_portfolio(
        symbols=["005930", "035720", "000660"],
        base_convictions={"005930": 8.0, "035720": 6.0, "000660": 5.0},
    )
    print(portfolio.summary())

    # Ackman의 "왜 지금?"
    print(engine.why_now("005930"))
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from kis_backtest.portfolio.catalyst_tracker import CatalystTracker
from kis_backtest.portfolio.macro_regime import MacroRegimeDashboard, Regime

logger = logging.getLogger(__name__)

# 레짐별 주식 비중 조정 배수
REGIME_WEIGHT_MULTIPLIER: Dict[Regime, float] = {
    Regime.EXPANSION: 1.2,
    Regime.CONTRACTION: 0.6,
    Regime.CRISIS: 0.3,
    Regime.RECOVERY: 1.0,
}

# 레짐별 최소 현금 비중
REGIME_CASH_FLOOR: Dict[Regime, float] = {
    Regime.EXPANSION: 0.10,
    Regime.CONTRACTION: 0.30,
    Regime.CRISIS: 0.60,
    Regime.RECOVERY: 0.15,
}

# 기본 종목 비중 (conviction 10 기준 최대)
BASE_WEIGHT_PER_SYMBOL: float = 0.10


@dataclass(frozen=True)
class InvestmentDecision:
    """단일 종목 투자 결정

    Attributes:
        symbol: 종목 코드
        action: 행동 ("buy", "hold", "sell", "skip")
        conviction: 확신도 (1-10)
        catalyst_score: 카탈리스트 종합 스코어
        regime: 현재 매크로 레짐
        regime_weight_adjustment: 레짐 기반 비중 배수 (예: 확장기 1.2x)
        final_weight: 최종 포트폴리오 비중
        reasoning: 결정 근거 목록
    """

    symbol: str
    action: str
    conviction: float
    catalyst_score: float
    regime: str
    regime_weight_adjustment: float
    final_weight: float
    reasoning: List[str] = field(default_factory=list)

    def summary(self) -> str:
        """한줄 요약"""
        action_emoji = {
            "buy": "BUY",
            "hold": "HOLD",
            "sell": "SELL",
            "skip": "SKIP",
        }
        tag = action_emoji.get(self.action, self.action.upper())
        reasons = " | ".join(self.reasoning) if self.reasoning else "N/A"
        return (
            f"[{self.symbol}] {tag} "
            f"conviction={self.conviction:.1f} "
            f"catalyst={self.catalyst_score:.2f} "
            f"regime={self.regime} "
            f"weight={self.final_weight:.2%} "
            f"({reasons})"
        )


@dataclass(frozen=True)
class PortfolioDecision:
    """포트폴리오 전체 투자 결정

    Attributes:
        regime: 현재 매크로 레짐
        regime_confidence: 레짐 판별 신뢰도
        decisions: 종목별 투자 결정 리스트
        total_equity_weight: 총 주식 비중
        cash_weight: 현금 비중
        created_at: 생성 시각
    """

    regime: Regime
    regime_confidence: float
    decisions: List[InvestmentDecision]
    total_equity_weight: float
    cash_weight: float
    created_at: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    def summary(self) -> str:
        """포트폴리오 요약"""
        lines = [
            "=" * 60,
            f"  Ackman-Druckenmiller 포트폴리오 결정",
            f"  레짐: {self.regime.value.upper()} (신뢰도={self.regime_confidence:.0%})",
            f"  주식: {self.total_equity_weight:.1%} | 현금: {self.cash_weight:.1%}",
            f"  생성: {self.created_at}",
            "=" * 60,
        ]
        for d in self.decisions:
            lines.append(f"  {d.summary()}")
        lines.append("=" * 60)
        return "\n".join(lines)


class AckmanDruckenmillerEngine:
    """Ackman-Druckenmiller 통합 엔진

    바텀업(Ackman) 카탈리스트 분석과 탑다운(Druckenmiller) 매크로 레짐을
    결합하여 종목별 투자 결정 및 포트폴리오 배분을 산출한다.
    """

    def __init__(
        self,
        catalyst_tracker: CatalystTracker,
        macro_dashboard: MacroRegimeDashboard,
        data_dir: Optional[str] = None,
    ) -> None:
        self._tracker = catalyst_tracker
        self._dashboard = macro_dashboard
        self._data_dir = data_dir
        self._history: List[Dict[str, Any]] = []

        if data_dir:
            self._load_history()

    # ── 종목 평가 ────────────────────────────────────────────

    def evaluate_symbol(
        self,
        symbol: str,
        base_conviction: float = 5.0,
    ) -> InvestmentDecision:
        """단일 종목 투자 결정

        Ackman + Druckenmiller 통합 로직:
        1. 카탈리스트 스코어 조회
        2. 매크로 레짐 확인
        3. 레짐 비중 조정 적용
        4. 행동 결정 (buy/hold/sell/skip)
        5. 최종 비중 산출

        Args:
            symbol: 종목 코드
            base_conviction: 기본 확신도 (1-10, 기본 5.0)

        Returns:
            InvestmentDecision: 투자 결정
        """
        # 1) 카탈리스트 스코어
        cat_score = self._tracker.score(symbol)
        catalyst_total = cat_score.total

        # 2) 매크로 레짐
        regime_result = self._dashboard.classify_regime()
        regime = regime_result.regime

        # 3) 레짐 비중 조정
        regime_mult = REGIME_WEIGHT_MULTIPLIER.get(regime, 1.0)

        # 4) 행동 결정
        reasoning: List[str] = []
        action = self._determine_action(
            catalyst_total, base_conviction, regime, reasoning
        )

        # 5) 최종 비중 산출
        base_weight = self._conviction_to_weight(base_conviction)
        final_weight = base_weight * regime_mult if action == "buy" else 0.0

        decision = InvestmentDecision(
            symbol=symbol,
            action=action,
            conviction=base_conviction,
            catalyst_score=catalyst_total,
            regime=regime.value,
            regime_weight_adjustment=regime_mult,
            final_weight=round(final_weight, 4),
            reasoning=reasoning,
        )

        logger.info("종목 평가: %s", decision.summary())
        return decision

    # ── 포트폴리오 평가 ──────────────────────────────────────

    def evaluate_portfolio(
        self,
        symbols: List[str],
        base_convictions: Dict[str, float],
    ) -> PortfolioDecision:
        """포트폴리오 전체 투자 결정

        전 종목을 평가하고 비중을 정규화한다.
        레짐별 현금 비중 하한선을 적용.

        Args:
            symbols: 종목 코드 리스트
            base_convictions: 종목별 기본 확신도

        Returns:
            PortfolioDecision: 포트폴리오 결정
        """
        regime_result = self._dashboard.classify_regime()
        regime = regime_result.regime
        cash_floor = REGIME_CASH_FLOOR.get(regime, 0.15)

        # 종목별 평가
        decisions: List[InvestmentDecision] = []
        for symbol in symbols:
            conviction = base_convictions.get(symbol, 5.0)
            decision = self.evaluate_symbol(symbol, conviction)
            decisions.append(decision)

        # 비중 정규화: 합계가 (1 - cash_floor)를 초과하지 않도록
        max_equity = 1.0 - cash_floor
        raw_total = sum(d.final_weight for d in decisions)

        if raw_total > max_equity and raw_total > 0:
            scale = max_equity / raw_total
            normalized: List[InvestmentDecision] = []
            for d in decisions:
                new_weight = round(d.final_weight * scale, 4)
                normalized.append(
                    InvestmentDecision(
                        symbol=d.symbol,
                        action=d.action,
                        conviction=d.conviction,
                        catalyst_score=d.catalyst_score,
                        regime=d.regime,
                        regime_weight_adjustment=d.regime_weight_adjustment,
                        final_weight=new_weight,
                        reasoning=d.reasoning,
                    )
                )
            decisions = normalized

        total_equity = sum(d.final_weight for d in decisions)
        cash_weight = round(1.0 - total_equity, 4)

        portfolio = PortfolioDecision(
            regime=regime,
            regime_confidence=regime_result.confidence,
            decisions=decisions,
            total_equity_weight=round(total_equity, 4),
            cash_weight=cash_weight,
        )

        # 히스토리 저장
        self._append_history(portfolio)

        logger.info(
            "포트폴리오 평가: regime=%s equity=%.1f%% cash=%.1f%%",
            regime.value,
            total_equity * 100,
            cash_weight * 100,
        )
        return portfolio

    # ── Ackman의 "왜 지금?" ───────────────────────────────

    def why_now(self, symbol: str) -> str:
        """Ackman의 "왜 지금?" 질문에 대한 답변

        카탈리스트 스코어, 최상위 카탈리스트, 긴급도, 레짐을 종합하여
        한 문장으로 매수/보류 근거를 제시한다.

        Args:
            symbol: 종목 코드

        Returns:
            str: "왜 지금?" 답변
        """
        cat_score = self._tracker.score(symbol)
        regime_result = self._dashboard.classify_regime()

        top = cat_score.top_catalyst or "none"
        urgency = cat_score.urgency
        regime_str = regime_result.regime.value

        if cat_score.is_actionable:
            action = "BUY"
        else:
            action = "SKIP"

        return (
            f"Symbol {symbol}: "
            f"catalyst_score={cat_score.total:.2f}, "
            f"top_catalyst='{top}', "
            f"urgency='{urgency}', "
            f"regime='{regime_str}' "
            f"→ {action}"
        )

    # ── 내부 로직 ────────────────────────────────────────────

    @staticmethod
    def _determine_action(
        catalyst_score: float,
        conviction: float,
        regime: Regime,
        reasoning: List[str],
    ) -> str:
        """행동 결정 로직

        우선순위:
        1. catalyst_score < 1.0 → "skip" (Ackman: 카탈리스트 없으면 매수 없다)
        2. conviction < 4 → "sell" (확신 부족)
        3. catalyst_score >= 2.0 AND conviction >= 6 AND regime != CRISIS → "buy"
        4. 나머지 → "hold"
        """
        # 규칙 1: 카탈리스트 부재
        if catalyst_score < 1.0:
            reasoning.append("catalyst_score < 1.0: no catalyst = no buy (Ackman)")
            return "skip"

        # 규칙 2: 확신 부족
        if conviction < 4:
            reasoning.append(f"conviction={conviction:.1f} < 4: low conviction sell")
            return "sell"

        # 규칙 3: 매수 조건 충족
        if catalyst_score >= 2.0 and conviction >= 6 and regime != Regime.CRISIS:
            reasoning.append(
                f"catalyst={catalyst_score:.2f}>=2.0, "
                f"conviction={conviction:.1f}>=6, "
                f"regime={regime.value}!=crisis → BUY"
            )
            return "buy"

        # 규칙 4: 기본 보유
        reasoning.append(
            f"catalyst={catalyst_score:.2f}, "
            f"conviction={conviction:.1f}, "
            f"regime={regime.value} → HOLD"
        )
        return "hold"

    @staticmethod
    def _conviction_to_weight(conviction: float) -> float:
        """확신도 → 기본 비중 변환

        conviction 1~10을 0.01~0.10 비중으로 선형 매핑.
        """
        clamped = max(1.0, min(10.0, conviction))
        return round(BASE_WEIGHT_PER_SYMBOL * (clamped / 10.0), 4)

    # ── 영속성 ───────────────────────────────────────────────

    def _history_path(self) -> Optional[Path]:
        """히스토리 파일 경로"""
        if not self._data_dir:
            return None
        return Path(self._data_dir) / "ackman_druckenmiller_history.json"

    def _append_history(self, portfolio: PortfolioDecision) -> None:
        """포트폴리오 결정을 히스토리에 추가"""
        entry = {
            "created_at": portfolio.created_at,
            "regime": portfolio.regime.value,
            "regime_confidence": portfolio.regime_confidence,
            "total_equity_weight": portfolio.total_equity_weight,
            "cash_weight": portfolio.cash_weight,
            "decisions": [
                {
                    "symbol": d.symbol,
                    "action": d.action,
                    "conviction": d.conviction,
                    "catalyst_score": d.catalyst_score,
                    "regime": d.regime,
                    "final_weight": d.final_weight,
                }
                for d in portfolio.decisions
            ],
        }
        self._history.append(entry)
        self._save_history()

    def _save_history(self) -> None:
        """히스토리 JSON 저장"""
        path = self._history_path()
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": 1,
            "updated_at": datetime.now().isoformat(),
            "history": self._history,
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_history(self) -> None:
        """히스토리 JSON 로드"""
        path = self._history_path()
        if not path or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._history = data.get("history", [])
            logger.info(
                "Ackman-Druckenmiller 히스토리 %d건 로드", len(self._history)
            )
        except Exception as e:
            logger.warning("히스토리 로드 실패: %s", e)
