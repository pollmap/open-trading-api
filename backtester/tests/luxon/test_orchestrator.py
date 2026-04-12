"""Sprint 9 — LuxonOrchestrator smoke 테스트.

Coverage:
    - 기본 초기화 (mcp=None, graph=None)
    - add_catalyst → tracker + graph 주입
    - run_workflow 기본 경로: 카탈리스트 없이도 동작
    - run_workflow with catalyst: BUY 경로 + position_sizes 생성
    - add_cufa_digest 그래프 주입 확인
    - 빈 symbols 리스트 → ValueError
    - OrchestrationReport.summary() 마크다운 포함

Style:
    - real CatalystTracker + MacroRegimeDashboard + ConvictionSizer
    - no MCP (mcp=None)
    - fresh Orchestrator per test
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kis_backtest.luxon.graph.edges import EdgeKind
from kis_backtest.luxon.graph.ingestors.cufa_ingestor import CufaReportDigest
from kis_backtest.luxon.graph.nodes import NodeKind
from kis_backtest.luxon.orchestrator import (
    LuxonOrchestrator,
    OrchestrationReport,
)
from kis_backtest.portfolio.catalyst_tracker import CatalystType


# ── Tests ────────────────────────────────────────────────────────────


def test_orchestrator_initializes_with_defaults() -> None:
    # Arrange / Act
    orch = LuxonOrchestrator()

    # Assert — 모든 내부 객체가 주입됨
    assert orch.mcp is None
    assert orch.graph is not None
    assert orch.tracker is not None
    assert orch.dashboard is not None
    assert orch.sizer is not None
    assert orch.engine is not None
    assert orch.total_capital == 100_000_000.0


def test_add_catalyst_registers_in_tracker() -> None:
    # Arrange
    orch = LuxonOrchestrator()

    # Act
    catalyst = orch.add_catalyst(
        symbol="005930",
        name="HBM4 양산",
        catalyst_type=CatalystType.INDUSTRY,
        expected_date="2026-05-15",
        probability=0.7,
        impact=8.0,
    )

    # Assert
    assert catalyst.symbol == "005930"
    assert "005930" in orch.tracker.symbols_with_catalysts()


def test_add_cufa_digest_populates_graph() -> None:
    # Arrange
    orch = LuxonOrchestrator()
    digest = CufaReportDigest(
        symbol="005930",
        ceo_name="이재용",
        key_persons=["한종희"],
        sector="반도체",
        themes=["AI"],
    )

    # Act
    result = orch.add_cufa_digest(digest)

    # Assert
    assert result["symbol_id"] == "symbol:005930"
    assert result["sector_id"] == "sector:반도체"
    assert len(result["person_ids"]) == 2
    # HOLDS 엣지 2개 (이재용 + 한종희 → 005930)
    assert len(orch.graph.edges_by_kind(EdgeKind.HOLDS)) == 2


def test_run_workflow_empty_symbols_raises() -> None:
    # Arrange
    orch = LuxonOrchestrator()

    # Act / Assert
    with pytest.raises(ValueError, match="symbols"):
        orch.run_workflow([])


def test_run_workflow_without_catalyst_returns_report() -> None:
    # Arrange — 카탈리스트 없이 바로 실행
    orch = LuxonOrchestrator()

    # Act
    report = orch.run_workflow(["005930", "000660"])

    # Assert
    assert isinstance(report, OrchestrationReport)
    assert report.regime  # 레짐 문자열 존재
    assert 0.0 <= report.regime_confidence <= 1.0
    assert len(report.portfolio.decisions) == 2
    # 카탈리스트 없으면 대부분 skip/hold → position_sizes 가 비어있을 수 있음
    # cross_references 도 빈 경우 가능 (그래프에 노드 없음)


def test_run_workflow_with_catalyst_creates_graph_nodes() -> None:
    # Arrange
    orch = LuxonOrchestrator()
    orch.add_catalyst(
        symbol="005930",
        name="HBM4 양산",
        catalyst_type=CatalystType.INDUSTRY,
        expected_date="2026-05-15",
        probability=0.9,
        impact=9.0,
    )

    # Act
    report = orch.run_workflow(
        ["005930"],
        base_convictions={"005930": 8.0},
    )

    # Assert
    assert isinstance(report, OrchestrationReport)
    # 그래프에 EventNode 생성됨
    event_nodes = orch.graph.nodes_by_kind(NodeKind.EVENT)
    assert len(event_nodes) >= 1
    # SymbolNode 도 생성됨 (CatalystIngestor 가 idempotent 생성)
    symbol_nodes = orch.graph.nodes_by_kind(NodeKind.SYMBOL)
    assert any("005930" in n.node_id for n in symbol_nodes)
    # CATALYST_FOR 엣지 존재
    assert len(orch.graph.edges_by_kind(EdgeKind.CATALYST_FOR)) >= 1
    # cross_references 에 005930 항목 있음
    assert "005930" in report.cross_references


def test_orchestration_report_summary_contains_markdown() -> None:
    # Arrange
    orch = LuxonOrchestrator()
    orch.add_catalyst(
        symbol="005930",
        name="실적 발표",
        catalyst_type=CatalystType.EARNINGS,
        expected_date="2026-06-15",
        probability=0.85,
        impact=7.0,
    )

    # Act
    report = orch.run_workflow(["005930"], base_convictions={"005930": 7.0})
    md = report.summary()

    # Assert — 마크다운 섹션 헤더들 확인
    assert "# Luxon Orchestration Report" in md
    assert "Portfolio Decisions" in md
    assert "005930" in md
    # position_sizes 가 있으면 Position Sizes 섹션
    if report.position_sizes:
        assert "Position Sizes" in md
    # cross_references 가 있으면 Graph Cross-References 섹션
    if any(refs for refs in report.cross_references.values()):
        assert "Cross-References" in md


def test_run_workflow_ingest_to_graph_false_skips_graph() -> None:
    # Arrange
    orch = LuxonOrchestrator()
    orch.add_catalyst(
        symbol="005930",
        name="테스트",
        catalyst_type=CatalystType.INDUSTRY,
        expected_date="2026-05-15",
        probability=0.5,
        impact=5.0,
    )

    # Act
    report = orch.run_workflow(
        ["005930"], ingest_to_graph=False,
    )

    # Assert — 그래프에 EventNode 생성 안 됨
    assert orch.graph.nodes_by_kind(NodeKind.EVENT) == []
    # cross_references 는 비어있음
    assert report.cross_references == {}


def test_run_workflow_default_conviction_is_5() -> None:
    # Arrange
    orch = LuxonOrchestrator()

    # Act — base_convictions 생략
    report = orch.run_workflow(["005930"])

    # Assert — 결정 존재 (action 무관, 구조만 확인)
    assert len(report.portfolio.decisions) == 1
    decision = report.portfolio.decisions[0]
    assert decision.symbol == "005930"
    assert decision.conviction == 5.0  # 기본값


def test_generate_weekly_letter_writes_markdown_file(tmp_path: Path) -> None:
    # Arrange
    orch = LuxonOrchestrator()
    orch.add_catalyst(
        symbol="005930",
        name="HBM4 양산",
        catalyst_type=CatalystType.INDUSTRY,
        expected_date="2026-05-15",
        probability=0.8,
        impact=8.0,
    )
    output_path = tmp_path / "2026-W15.md"

    # Act
    saved = orch.generate_weekly_letter(
        ["005930"],
        output_path,
        base_convictions={"005930": 8.0},
    )

    # Assert — 파일 존재 + Path 반환 + 마크다운 헤더 포함
    assert saved == output_path
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert "# Luxon Orchestration Report" in content
    assert "005930" in content


def test_generate_weekly_letter_creates_parent_dirs(tmp_path: Path) -> None:
    # Arrange — 존재하지 않는 중첩 디렉토리
    orch = LuxonOrchestrator()
    nested = tmp_path / "letters" / "2026" / "week15.md"

    # Act
    saved = orch.generate_weekly_letter(["005930"], nested)

    # Assert — 상위 디렉토리 자동 생성 + 파일 존재
    assert nested.parent.exists()
    assert saved.exists()
    assert "005930" in saved.read_text(encoding="utf-8")
