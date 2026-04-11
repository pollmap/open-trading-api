"""Tests for CatalystIngestor (Sprint 6 Phase 2).

Coverage:
    - ingest_catalyst: SymbolNode + EventNode + CATALYST_FOR edge creation
    - EventNode payload schema (source, symbol, name, catalyst_type, probability,
      impact, description, source_tag, weighted_score, expected_date)
    - CATALYST_FOR edge weight normalization in [0, 1]
    - CATALYST_FOR edge meta sign (+1 / -1) based on impact polarity
    - Duplicate catalyst raises ValueError (via GothamGraph.add_node)
    - ingest_all(tracker): silent skip on ValueError, unique symbols, no partial damage

Style:
    - stdlib + pytest + real GothamGraph / Catalyst / CatalystTracker instances
    - AAA pattern, one behavior per test, fresh graph + ingestor per test
    - No mocks, no file I/O (CatalystTracker() with no state_file)
"""
from __future__ import annotations

import pytest

from kis_backtest.luxon.graph.edges import EdgeKind
from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.ingestors.catalyst_ingestor import CatalystIngestor
from kis_backtest.luxon.graph.nodes import NodeKind
from kis_backtest.portfolio.catalyst_tracker import (
    Catalyst,
    CatalystTracker,
    CatalystType,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_catalyst(
    symbol: str = "005930",
    name: str = "테스트 카탈리스트",
    catalyst_type: CatalystType = CatalystType.INDUSTRY,
    expected_date: str = "2026-06-15",  # near future, non-zero time_weight
    probability: float = 0.7,
    impact: float = 8.0,
    description: str = "test fixture",
    source: str = "test",
) -> Catalyst:
    """Build a synthetic Catalyst directly (no tracker.add(), avoids file I/O).

    Note: Using the frozen dataclass constructor bypasses the probability/impact
    range validation in CatalystTracker.add(); that's fine here because our
    fixtures stay inside valid bounds.
    """
    return Catalyst(
        symbol=symbol,
        name=name,
        catalyst_type=catalyst_type,
        expected_date=expected_date,
        probability=probability,
        impact=impact,
        description=description,
        source=source,
    )


# ── Tests ────────────────────────────────────────────────────────────────


def test_ingest_catalyst_creates_symbol_event_and_edge() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = CatalystIngestor(graph)
    catalyst = _make_catalyst(
        symbol="005930",
        name="HBM4 양산",
        catalyst_type=CatalystType.INDUSTRY,
        expected_date="2026-06-15",
        probability=0.7,
        impact=8.0,
    )

    # Act
    event_id = ingestor.ingest_catalyst(catalyst)

    # Assert — event id format contract
    assert event_id.startswith("event:catalyst:005930:")

    # Event node exists and has the right kind
    event_node = graph.get_node(event_id)
    assert event_node is not None
    assert event_node.kind is NodeKind.EVENT

    # Symbol node exists at the standard id and has the right kind
    symbol_id = "symbol:005930"
    symbol_node = graph.get_node(symbol_id)
    assert symbol_node is not None
    assert symbol_node.kind is NodeKind.SYMBOL

    # Exactly one CATALYST_FOR edge: event → symbol
    catalyst_edges = graph.edges_by_kind(EdgeKind.CATALYST_FOR)
    assert len(catalyst_edges) == 1
    edge = catalyst_edges[0]
    assert edge.source_id == event_id
    assert edge.target_id == symbol_id

    # Out-of-scope edges must remain empty
    assert graph.edges_by_kind(EdgeKind.BELONGS_TO) == []
    assert graph.edges_by_kind(EdgeKind.TRIGGERED_BY) == []


def test_ingest_catalyst_payload_contents() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = CatalystIngestor(graph)
    catalyst = _make_catalyst(
        symbol="005930",
        name="HBM4 양산 시작",
        catalyst_type=CatalystType.INDUSTRY,
        expected_date="2026-06-15",
        probability=0.7,
        impact=8.0,
        description="SK하이닉스 독점 깨고 HBM4 납품 시작 예상",
        source="DART",
    )

    # Act
    event_id = ingestor.ingest_catalyst(catalyst)

    # Assert — every required payload key is present with the right value
    event_node = graph.get_node(event_id)
    assert event_node is not None
    payload = event_node.payload

    assert payload["source"] == "catalyst"
    assert payload["symbol"] == "005930"
    assert payload["name"] == "HBM4 양산 시작"
    assert payload["catalyst_type"] == CatalystType.INDUSTRY.value  # "industry"
    assert payload["expected_date"] == "2026-06-15"
    assert payload["probability"] == 0.7
    assert payload["impact"] == 8.0
    assert payload["description"] == "SK하이닉스 독점 깨고 HBM4 납품 시작 예상"
    assert payload["source_tag"] == "DART"

    # weighted_score must be a float and non-negative in magnitude
    assert isinstance(payload["weighted_score"], float)
    assert abs(payload["weighted_score"]) >= 0.0


def test_ingest_catalyst_edge_weight_normalized() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = CatalystIngestor(graph)

    high_score = _make_catalyst(
        symbol="005930",
        name="HBM4 대량 양산",
        catalyst_type=CatalystType.INDUSTRY,
        expected_date="2026-06-15",
        probability=1.0,
        impact=10.0,  # |weighted_score| ≤ 10 before normalization
    )
    zero_score = _make_catalyst(
        symbol="005930",
        name="확률 0 카탈리스트",
        catalyst_type=CatalystType.EARNINGS,
        expected_date="2026-06-15",
        probability=0.0,  # forces weighted_score = 0 regardless of time_weight
        impact=5.0,
    )

    # Act
    high_event_id = ingestor.ingest_catalyst(high_score)
    zero_event_id = ingestor.ingest_catalyst(zero_score)

    # Assert — every CATALYST_FOR edge weight must be inside [0.0, 1.0]
    catalyst_edges = graph.edges_by_kind(EdgeKind.CATALYST_FOR)
    assert len(catalyst_edges) == 2
    for edge in catalyst_edges:
        assert 0.0 <= edge.weight <= 1.0

    # Find the edges by their source_id (event_id)
    high_edge = next(e for e in catalyst_edges if e.source_id == high_event_id)
    zero_edge = next(e for e in catalyst_edges if e.source_id == zero_event_id)

    # Zero-probability catalyst → weight must collapse to 0.0
    assert zero_edge.weight == 0.0

    # High-score positive catalyst → sign is +1
    assert high_edge.meta["sign"] == 1

    # Zero-score catalyst → default sign is +1 (impact is positive)
    assert zero_edge.meta["sign"] == 1


def test_ingest_catalyst_negative_impact_sign() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = CatalystIngestor(graph)
    catalyst = _make_catalyst(
        symbol="005930",
        name="규제 강화 악재",
        catalyst_type=CatalystType.REGULATION,
        expected_date="2026-06-15",
        probability=0.6,
        impact=-5.0,  # negative → sign must be -1
    )

    # Act
    event_id = ingestor.ingest_catalyst(catalyst)

    # Assert
    catalyst_edges = graph.edges_by_kind(EdgeKind.CATALYST_FOR)
    assert len(catalyst_edges) == 1
    edge = catalyst_edges[0]
    assert edge.source_id == event_id
    assert edge.meta["sign"] == -1
    # Weight stays inside the normalized range regardless of impact polarity
    assert 0.0 <= edge.weight <= 1.0


def test_ingest_catalyst_duplicate_raises() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = CatalystIngestor(graph)
    catalyst = _make_catalyst(
        symbol="005930",
        name="중복 카탈리스트",
        catalyst_type=CatalystType.INDUSTRY,
        expected_date="2026-06-15",
        probability=0.7,
        impact=8.0,
    )

    # Act — first ingestion succeeds
    first_event_id = ingestor.ingest_catalyst(catalyst)
    assert graph.get_node(first_event_id) is not None

    # Act / Assert — second ingestion of the same Catalyst raises ValueError
    with pytest.raises(ValueError):
        ingestor.ingest_catalyst(catalyst)

    # Symbol node must still exist exactly once (idempotent)
    symbol_nodes = graph.nodes_by_kind(NodeKind.SYMBOL)
    assert len(symbol_nodes) == 1
    assert symbol_nodes[0].node_id == "symbol:005930"


def test_ingest_all_via_tracker_silent_skip_on_duplicate() -> None:
    # Arrange — fresh tracker with no state_file (no disk I/O)
    tracker = CatalystTracker()
    tracker.add(
        symbol="005930",
        name="삼성 HBM4 양산",
        catalyst_type=CatalystType.INDUSTRY,
        expected_date="2026-06-15",
        probability=0.7,
        impact=8.0,
    )
    tracker.add(
        symbol="005930",
        name="삼성 실적 서프라이즈",
        catalyst_type=CatalystType.EARNINGS,
        expected_date="2026-07-01",
        probability=0.6,
        impact=5.0,
    )
    tracker.add(
        symbol="000660",
        name="SK하이닉스 HBM3e 양산",
        catalyst_type=CatalystType.INDUSTRY,
        expected_date="2026-06-20",
        probability=0.8,
        impact=7.0,
    )

    active_catalysts = tracker.list_all_active()
    active_count = len(active_catalysts)
    assert active_count == 3  # sanity — fixture setup

    graph = GothamGraph()
    ingestor = CatalystIngestor(graph)

    # Pre-insert exactly ONE of the active catalysts to force a duplicate later
    pre_inserted = active_catalysts[0]
    pre_inserted_event_id = ingestor.ingest_catalyst(pre_inserted)
    assert graph.get_node(pre_inserted_event_id) is not None

    # Act — ingest_all should silently skip the duplicate and ingest the rest
    created_ids = ingestor.ingest_all(tracker)

    # Assert — returned list contains only the NEWLY created ids
    assert len(created_ids) == active_count - 1

    # All returned ids must reference actual EventNodes in the graph
    for event_id in created_ids:
        node = graph.get_node(event_id)
        assert node is not None
        assert node.kind is NodeKind.EVENT

    # Total EventNodes equals active_count (pre-inserted + freshly ingested,
    # no partial duplicate damage)
    event_nodes = graph.nodes_by_kind(NodeKind.EVENT)
    assert len(event_nodes) == active_count

    # Symbol nodes must be unique per symbol (idempotent): 005930 + 000660
    symbol_nodes = graph.nodes_by_kind(NodeKind.SYMBOL)
    symbol_ids = {node.node_id for node in symbol_nodes}
    assert symbol_ids == {"symbol:005930", "symbol:000660"}

    # CATALYST_FOR edge count must equal total event nodes
    catalyst_edges = graph.edges_by_kind(EdgeKind.CATALYST_FOR)
    assert len(catalyst_edges) == active_count


def test_ingestor_graph_property_exposes_graph() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = CatalystIngestor(graph)

    # Act / Assert — .graph property returns the exact GothamGraph instance
    assert ingestor.graph is graph


def test_ingest_all_empty_tracker_returns_empty_list() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = CatalystIngestor(graph)
    empty_tracker = CatalystTracker()

    # Act
    created_ids = ingestor.ingest_all(empty_tracker)

    # Assert
    assert created_ids == []
    assert graph.node_count == 0
    assert graph.edge_count == 0
