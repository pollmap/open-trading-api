"""Luxon Terminal — Sprint 6 GothamGraph 수동 smoke 샘플.

3가지 ingestor 를 모두 사용해서 실제 HTML 산출물을 생성한다.
브라우저로 `backtester/out/gotham_sample.html` 을 열어 시각 확인.

실행:
    cd backtester
    .venv/Scripts/python.exe samples/gotham_sample.py
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from kis_backtest.luxon.graph import (
    CatalystIngestor,
    CufaIngestor,
    CufaReportDigest,
    GothamGraph,
    Phase1Ingestor,
    render_graph_html,
)
from kis_backtest.luxon.integration.conviction_bridge import OrderProposal
from kis_backtest.luxon.integration.phase1_pipeline import Phase1CheckpointResult
from kis_backtest.portfolio.catalyst_tracker import CatalystType, CatalystTracker
from kis_backtest.portfolio.macro_regime import Regime, RegimeResult


def build_sample_graph() -> GothamGraph:
    """Phase1 + Catalyst + CUFA 3단 ingestion. 반환 그래프는 10+ 노드."""
    graph = GothamGraph()

    # ── 1. Phase1Ingestor: MacroRegime + OrderProposal 2건 ───────────
    phase1 = Phase1Ingestor(graph)

    checkpoint = Phase1CheckpointResult(
        timestamp=datetime(2026, 4, 12, 10, 0, 0),
        fred_series_loaded=10,
        fred_stale_count=0,
        tick_vault_stats={"total_files": 100},
        regime_result=RegimeResult(
            regime=Regime.RECOVERY,
            confidence=0.82,
            score=0.35,
            positive_signals=4,
            negative_signals=1,
            neutral_signals=5,
            allocation={"equity": 0.60, "bond": 0.25, "cash": 0.15},
        ),
        macro_indicator_count=10,
        errors=[],
    )
    regime_id = phase1.ingest_checkpoint(checkpoint)

    phase1.ingest_proposal(
        OrderProposal(
            symbol="005930",
            action="BUY",
            position_pct=0.15,
            conviction=8.5,
            reason="recovery regime + HBM catalyst",
            passed_gates=("gate1", "gate2:recovery", "gate3"),
        ),
        regime_node_id=regime_id,
        sector="반도체",
    )
    phase1.ingest_proposal(
        OrderProposal(
            symbol="000660",
            action="BUY",
            position_pct=0.10,
            conviction=7.2,
            reason="HBM leader, valuation cheap",
            passed_gates=("gate1", "gate2:recovery", "gate3"),
        ),
        regime_node_id=regime_id,
        sector="반도체",
    )

    # ── 2. CatalystIngestor: CatalystTracker 에서 일괄 수집 ──────────
    tracker = CatalystTracker()
    tracker.add(
        symbol="005930",
        name="삼성전자 HBM4 양산",
        catalyst_type=CatalystType.INDUSTRY,
        expected_date="2026-06-15",
        probability=0.7,
        impact=8.0,
        description="SK하이닉스 독점 깨고 HBM4 납품 예상",
        source="DART",
    )
    tracker.add(
        symbol="005930",
        name="Q2 실적 가이던스",
        catalyst_type=CatalystType.EARNINGS,
        expected_date="2026-05-10",
        probability=0.9,
        impact=6.0,
        description="예상치 상회 가능성",
        source="NEWS",
    )
    tracker.add(
        symbol="000660",
        name="SK하이닉스 HBM3e 증산",
        catalyst_type=CatalystType.INDUSTRY,
        expected_date="2026-05-01",
        probability=0.8,
        impact=7.0,
        description="NVIDIA 공급 확대",
        source="NEWS",
    )

    catalyst = CatalystIngestor(graph)
    catalyst.ingest_all(tracker)

    # ── 3. CufaIngestor: 2 종목 digest ────────────────────────────────
    cufa = CufaIngestor(graph)
    cufa.ingest_digest(
        CufaReportDigest(
            symbol="005930",
            ceo_name="한종희",
            key_persons=["경계현", "이재용"],
            sector="반도체",
            themes=["AI", "HBM", "파운드리"],
        )
    )
    cufa.ingest_digest(
        CufaReportDigest(
            symbol="000660",
            ceo_name="곽노정",
            key_persons=["최태원"],
            sector="반도체",
            themes=["HBM", "AI"],
        )
    )

    return graph


def main() -> None:
    graph = build_sample_graph()

    # 각 NodeKind 별 개수 집계
    from kis_backtest.luxon.graph.nodes import NodeKind
    print(f"Total: {graph.node_count} nodes, {graph.edge_count} edges")
    for kind in NodeKind:
        count = len(graph.nodes_by_kind(kind))
        if count:
            print(f"  {kind.value:14s} = {count}")

    # HTML 출력
    out_dir = Path(__file__).resolve().parent.parent / "out"
    out_path = out_dir / "gotham_sample.html"
    render_graph_html(graph, out_path, title="Luxon GothamGraph Sprint 6 Sample")
    print(f"\nHTML written: {out_path}")
    print(f"Open in browser: file:///{out_path.as_posix()}")


if __name__ == "__main__":
    main()
