"""
Luxon Terminal — 1인 헤지펀드 통합 오케스트레이터 (Sprint 9).

기존 portfolio/ 17 모듈 + Nexus MCP 162 도구 + luxon/graph GothamGraph 를
얇게 조합하는 쉘. 신규 계산 로직 0줄, dispatching 만 담당.

이 모듈의 존재 이유:
    - Sprint 5~8 은 GothamGraph "지식 그래프 레이어" 만 만들었고
    - portfolio/ 에 이미 Ackman-Druckenmiller / ConvictionSizer /
      CatalystTracker / MacroRegimeDashboard / ConvictionSizer 등이 있고
    - Nexus MCP 162 분석 도구가 외부 서비스로 돌고 있음
    - 이것들을 사용자가 "한 줄 호출" 로 연결할 진입점이 없었음

Workflow:
    1. refresh_macro(mcp)                    ← 선택. MCP 로 매크로 지표 갱신
    2. add_catalyst(...) 반복                 ← 선택. CatalystTracker 에 등록
    3. run_workflow(symbols, convictions)    ← 메인
        a) AckmanDruckenmillerEngine.evaluate_portfolio
        b) ConvictionSizer.set_conviction + size_position
        c) CatalystIngestor → GothamGraph (멱등)
        d) 1-hop in-neighbor cross-reference
    4. report.summary() → markdown

Phase 2 마감. 이 파일이 Sprint 5~8 의 "출구" 이자 Phase 3+ 의 "입구" 역할.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# MacroRegime 상태 파일 기본 경로 — MCP 오프라인 시 캐시 폴백용
_MACRO_STATE_FILE = str(Path.home() / ".luxon" / "macro_regime_state.json")

from kis_backtest.luxon.graph.edges import EdgeKind
from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.ingestors.catalyst_ingestor import CatalystIngestor
from kis_backtest.luxon.graph.ingestors.cufa_ingestor import (
    CufaIngestor,
    CufaReportDigest,
)
from kis_backtest.luxon.graph.nodes import NodeKind, make_node_id
from kis_backtest.luxon.stream.tick_vault import TickVault
from kis_backtest.portfolio.ackman_druckenmiller import (
    AckmanDruckenmillerEngine,
    InvestmentDecision,
    PortfolioDecision,
)
from kis_backtest.portfolio.catalyst_tracker import (
    Catalyst,
    CatalystTracker,
    CatalystType,
)
from kis_backtest.portfolio.conviction_sizer import ConvictionSizer, PositionSize
from kis_backtest.portfolio.macro_regime import MacroRegimeDashboard


# ── Report dataclass ─────────────────────────────────────────────────


@dataclass(frozen=True)
class OrchestrationReport:
    """run_workflow 산출물. 직렬화 가능한 얇은 리포트.

    Attributes:
        regime: 매크로 레짐 문자열 (예: "expansion").
        regime_confidence: 레짐 신뢰도 (0-1).
        portfolio: PortfolioDecision (기존 dataclass 그대로 참조).
        position_sizes: 종목별 PositionSize 리스트.
        cross_references: 각 symbol 의 GothamGraph 3-hop in-neighbor 라벨 리스트.
        generated_at: 생성 시각 (ISO).
    """
    regime: str
    regime_confidence: float
    portfolio: PortfolioDecision
    position_sizes: list[PositionSize]
    cross_references: dict[str, list[str]] = field(default_factory=dict)
    generated_at: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )

    def summary(self) -> str:
        """마크다운 요약. 로그/콘솔 출력용."""
        lines: list[str] = [
            f"# Luxon Orchestration Report — {self.generated_at}",
            f"Regime: **{self.regime}** (confidence {self.regime_confidence:.0%})",
            "",
            "## Portfolio Decisions",
            f"- total_equity_weight: {self.portfolio.total_equity_weight:.1%}",
            f"- cash_weight: {self.portfolio.cash_weight:.1%}",
        ]
        for d in self.portfolio.decisions:
            lines.append(
                f"  · [{d.symbol}] {d.action.upper()} "
                f"weight={d.final_weight:.2%} "
                f"catalyst={d.catalyst_score:.2f}"
            )

        if self.position_sizes:
            lines.extend(["", "## Position Sizes (Half-Kelly)"])
            for pos in self.position_sizes:
                lines.append(
                    f"  · {pos.symbol}: weight={pos.weight:.2%}, "
                    f"amount={pos.amount:,.0f} KRW"
                )

        if self.cross_references:
            lines.extend(["", "## Graph Cross-References (1-hop in-neighbors)"])
            for sym, refs in self.cross_references.items():
                if refs:
                    lines.append(f"  · {sym} ← {', '.join(refs[:8])}")

        return "\n".join(lines)


# ── Orchestrator ─────────────────────────────────────────────────────


class LuxonOrchestrator:
    """Luxon Terminal 1인 헤지펀드 shell.

    Args:
        mcp: MCPDataProvider 인스턴스 (선택). 없으면 매크로/팩터 fetch 스킵.
        graph: GothamGraph 인스턴스 (선택). 없으면 새로 생성.
        tick_vault: TickVault 인스턴스 (선택). 지금은 직접 안 씀.
        tracker: CatalystTracker (선택). 없으면 빈 tracker 생성.
        dashboard: MacroRegimeDashboard (선택). 없으면 빈 dashboard 생성.
        sizer: ConvictionSizer (선택). 없으면 기본값.
        total_capital: 총 투자 자본 (KRW). 기본 1억.
    """

    def __init__(
        self,
        mcp: Any | None = None,
        graph: GothamGraph | None = None,
        tick_vault: TickVault | None = None,
        tracker: CatalystTracker | None = None,
        dashboard: MacroRegimeDashboard | None = None,
        sizer: ConvictionSizer | None = None,
        total_capital: float = 100_000_000.0,
    ) -> None:
        self.mcp = mcp
        self.graph = graph if graph is not None else GothamGraph()
        self.tick_vault = tick_vault
        self.tracker = tracker if tracker is not None else CatalystTracker()
        self.dashboard = (
            dashboard if dashboard is not None
            else MacroRegimeDashboard(state_file=_MACRO_STATE_FILE)
        )
        self.sizer = sizer if sizer is not None else ConvictionSizer()
        self.total_capital = total_capital

        # 이미 있는 Ackman+Druckenmiller 엔진 재사용
        self.engine = AckmanDruckenmillerEngine(
            catalyst_tracker=self.tracker,
            macro_dashboard=self.dashboard,
        )

        # Graph ingestor (멱등하게 호출 가능)
        self._catalyst_ingestor = CatalystIngestor(self.graph)
        self._cufa_ingestor = CufaIngestor(self.graph)

    # ── 등록 helpers ──────────────────────────────────────────

    def add_catalyst(
        self,
        symbol: str,
        name: str,
        catalyst_type: str | CatalystType,
        expected_date: str,
        probability: float,
        impact: float,
        description: str = "",
        source: str = "",
    ) -> Catalyst:
        """CatalystTracker 에 카탈리스트 등록 (얇은 위임)."""
        return self.tracker.add(
            symbol=symbol,
            name=name,
            catalyst_type=catalyst_type,
            expected_date=expected_date,
            probability=probability,
            impact=impact,
            description=description,
            source=source,
        )

    def add_cufa_digest(self, digest: CufaReportDigest) -> dict[str, Any]:
        """CUFA digest 를 그래프에 주입."""
        return self._cufa_ingestor.ingest_digest(digest)

    async def refresh_macro(self) -> None:
        """MCP 에서 매크로 지표 갱신. mcp 없으면 no-op."""
        if self.mcp is None:
            return
        await self.dashboard.fetch_indicators(self.mcp)

    # ── 메인 워크플로우 ────────────────────────────────────────

    def run_workflow(
        self,
        symbols: list[str],
        base_convictions: dict[str, float] | None = None,
        *,
        ingest_to_graph: bool = True,
    ) -> OrchestrationReport:
        """메인 1인 헤지펀드 워크플로우.

        Args:
            symbols: 분석 대상 종목 리스트.
            base_convictions: 종목별 기본 확신도 (1-10). None 이면 5.0.
            ingest_to_graph: True 면 CatalystTracker 전체를 GothamGraph 에
                주입 + 3-hop cross-reference 수집.

        Returns:
            OrchestrationReport.
        """
        if not symbols:
            raise ValueError("symbols 비어있음 — 최소 1개 필요")

        convictions = (
            base_convictions
            if base_convictions is not None
            else {s: 5.0 for s in symbols}
        )

        # 1. Ackman + Druckenmiller 평가 (기존 engine 재사용)
        portfolio: PortfolioDecision = self.engine.evaluate_portfolio(
            symbols, convictions,
        )

        # 2. Conviction sizing (Half-Kelly)
        position_sizes: list[PositionSize] = []
        for decision in portfolio.decisions:
            cat_score = self.tracker.score(decision.symbol).total
            self.sizer.set_conviction(
                symbol=decision.symbol,
                base_conviction=decision.conviction,
                catalyst_score=cat_score,
            )
            if decision.action == "buy":
                try:
                    pos = self.sizer.size_position(
                        decision.symbol, self.total_capital,
                    )
                    if pos.weight > 0:
                        position_sizes.append(pos)
                except (KeyError, ValueError):
                    # 확신도 미설정 또는 자본 0 → 스킵
                    continue

        # 3. GothamGraph 주입 + 3-hop cross-reference
        cross_references: dict[str, list[str]] = {}
        if ingest_to_graph:
            # Catalyst → EventNode (멱등하게 호출)
            self._catalyst_ingestor.ingest_all(self.tracker)

            # 각 symbol 의 in-edge (events, persons, etc.) 수집
            for sym in symbols:
                sym_id = make_node_id(NodeKind.SYMBOL, sym)
                if not self.graph.has_node(sym_id):
                    cross_references[sym] = []
                    continue
                try:
                    neighbors = self.graph.neighbors(
                        sym_id, direction="in",
                    )
                    cross_references[sym] = [n.label for n in neighbors]
                except KeyError:
                    cross_references[sym] = []

        return OrchestrationReport(
            regime=portfolio.regime.value,
            regime_confidence=portfolio.regime_confidence,
            portfolio=portfolio,
            position_sizes=position_sizes,
            cross_references=cross_references,
        )

    # ── 리포트 저장 helper ────────────────────────────────────

    def generate_weekly_letter(
        self,
        symbols: list[str],
        output_path: str | Path,
        base_convictions: dict[str, float] | None = None,
        *,
        ingest_to_graph: bool = True,
    ) -> Path:
        """주간 워크플로우 결과를 마크다운 파일로 저장.

        `run_workflow` 호출 후 `OrchestrationReport.summary()` 를 그대로
        지정 경로에 저장한다. 백테스트 성과 리포트 (`investor_letter.py`)
        와 달리 "현재 시점 투자 의사결정 스냅샷" 으로 설계되어 수익률/샤프
        등 사후 지표는 포함하지 않는다.

        Args:
            symbols: 분석 종목 리스트.
            output_path: 저장할 .md 파일 경로. 상위 디렉토리가 없으면 생성.
            base_convictions: 종목별 기본 확신도 (1-10). None 이면 5.0.
            ingest_to_graph: run_workflow 와 동일.

        Returns:
            저장된 파일의 `Path`.
        """
        report = self.run_workflow(
            symbols,
            base_convictions=base_convictions,
            ingest_to_graph=ingest_to_graph,
        )
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.summary(), encoding="utf-8")
        return path


    # ── 주문 실행 ─────────────────────────────────────────

    # ── 백테스트 / 검증 ────────────────────────────────────

    def backtest(
        self,
        report: OrchestrationReport,
        returns_dict: dict[str, list[float]] | None = None,
        equity_curve: list[float] | None = None,
        backtest_sharpe: float | None = None,
        backtest_max_dd: float | None = None,
    ) -> Any:
        """분석 결과를 리스크 파이프라인에 통과.

        QuantPipeline 의 변동성 타겟팅, DD 체크, Kelly 조정을 적용해
        OrchestrationReport 의 비중이 실전에 적합한지 검증한다.

        Args:
            report: run_workflow 결과.
            returns_dict: 종목별 일간 수익률.
            equity_curve: 자산 곡선 (DD 체크용).
            backtest_sharpe: 백테스트 Sharpe.
            backtest_max_dd: 백테스트 MaxDD.

        Returns:
            PipelineResult (core.pipeline).
        """
        from .backtest_bridge import BacktestBridge

        bridge = BacktestBridge(mcp_provider=self.mcp)
        return bridge.run_risk_pipeline(
            report,
            returns_dict=returns_dict,
            equity_curve=equity_curve,
            backtest_sharpe=backtest_sharpe,
            backtest_max_dd=backtest_max_dd,
        )

    def validate(
        self,
        report: OrchestrationReport,
        returns_dict: dict[str, list[float]],
        n_folds: int = 5,
        min_sharpe: float = 0.3,
    ) -> Any:
        """Walk-Forward OOS 검증.

        position_sizes 비중으로 포트폴리오 수익률을 합성한 뒤
        N-fold IS/OOS 교차 검증을 수행한다.

        Args:
            report: run_workflow 결과.
            returns_dict: 종목별 일간 수익률 (최소 60일).
            n_folds: 폴드 수 (기본 5).
            min_sharpe: 최소 OOS Sharpe (기본 0.3).

        Returns:
            WFResult (core.walk_forward).
        """
        from .backtest_bridge import BacktestBridge

        bridge = BacktestBridge(mcp_provider=self.mcp)
        return bridge.validate_oos(
            report,
            returns_dict=returns_dict,
            n_folds=n_folds,
            min_sharpe=min_sharpe,
        )

    # ── 복기 ─────────────────────────────────────────────

    def schedule_review(
        self,
        brokerage: Any = None,
        vault_path: str | Path | None = None,
        kill_conditions: list[Any] | None = None,
    ) -> dict[str, Any]:
        """ReviewScheduler 연결. should_run 체크 → 일/주간 복기 자동 실행.

        brokerage 미설정 시 RuntimeError.
        Vault 기본 경로: ~/obsidian-vault/ (없으면 로컬 out/).

        Returns:
            dict: {daily: DailySnapshot|None, weekly: WeeklyReport|None}
        """
        if brokerage is None:
            raise RuntimeError(
                "schedule_review: brokerage 필수. "
                "KISBrokerageProvider 전달하세요."
            )
        from kis_backtest.execution.review_scheduler import ReviewScheduler
        from kis_backtest.execution.vault_writer import VaultWriter
        from kis_backtest.portfolio.review_engine import ReviewEngine

        vault_dir = Path(vault_path) if vault_path else (
            Path.home() / "obsidian-vault"
        )
        if not vault_dir.exists():
            vault_dir = Path(__file__).resolve().parent.parent.parent / "out"

        review_engine = ReviewEngine(
            initial_capital=self.total_capital,
        )
        vault_writer = VaultWriter(vault_root=vault_dir)
        scheduler = ReviewScheduler(
            brokerage=brokerage,
            review_engine=review_engine,
            vault_writer=vault_writer,
            kill_conditions=kill_conditions,
            initial_capital=self.total_capital,
        )

        result: dict[str, Any] = {"daily": None, "weekly": None}
        if scheduler.should_run_daily():
            result["daily"] = scheduler.run_daily()
        if scheduler.should_run_weekly():
            result["weekly"] = scheduler.run_weekly()
        return result

    # ── 주문 실행 ─────────────────────────────────────────

    def execute_decisions(
        self,
        report: OrchestrationReport,
        *,
        brokerage: Any = None,
        price_provider: Any = None,
        mode: str = "paper",
        dry_run: bool = True,
        total_capital: float | None = None,
    ) -> Any:
        """분석 결과를 실제 주문으로 실행.

        Args:
            report: run_workflow 결과.
            brokerage: BrokerageProvider (KISBrokerageProvider 등).
            price_provider: PriceProvider (현재가 조회).
            mode: "paper" (모의투자) 또는 "prod" (실전).
            dry_run: True 면 주문 계획만 (기본), False 면 실제 주문.
            total_capital: 투입 자본. None 이면 self.total_capital.

        Returns:
            ExecutionReport.

        Raises:
            RuntimeError: brokerage 또는 price_provider 미설정.
        """
        if brokerage is None or price_provider is None:
            raise RuntimeError(
                "execute_decisions 호출 시 brokerage + price_provider 필수. "
                "KISBrokerageProvider 생성 후 전달하세요."
            )
        from .executor_bridge import ExecutorBridge

        bridge = ExecutorBridge(
            brokerage=brokerage,
            price_provider=price_provider,
            mode=mode,
        )
        capital = total_capital or self.total_capital
        order = bridge.build_order(report, total_capital=capital)
        return bridge.execute(order, dry_run=dry_run)

    def run_and_execute(
        self,
        symbols: list[str],
        base_convictions: dict[str, float] | None = None,
        *,
        brokerage: Any = None,
        price_provider: Any = None,
        mode: str = "paper",
        dry_run: bool = True,
    ) -> tuple[OrchestrationReport, Any]:
        """run_workflow + execute_decisions 결합. 분석→실행 한 줄."""
        report = self.run_workflow(symbols, base_convictions=base_convictions)
        exec_report = self.execute_decisions(
            report,
            brokerage=brokerage,
            price_provider=price_provider,
            mode=mode,
            dry_run=dry_run,
        )
        return report, exec_report


__all__ = ["LuxonOrchestrator", "OrchestrationReport"]
