"""Tests for Phase1Ingestor (Sprint 5 Phase 2).

Coverage:
    - ingest_checkpoint: RegimeResult present/absent branches
    - MacroRegimeNode payload fields (regime, confidence, score, allocation, ...)
    - ingest_proposal: SymbolNode + EventNode + CATALYST_FOR creation
    - Optional regime_node_id → TRIGGERED_BY edge
    - Optional sector → SectorNode + BELONGS_TO edge
    - Symbol idempotency (same symbol ingested twice does not re-add)
    - Error path: unknown regime_node_id raises ValueError

Style:
    - stdlib only, real GothamGraph + Phase1Ingestor instances (no mocks)
    - AAA pattern, one behavior per test
    - Fresh GothamGraph + Phase1Ingestor per test
"""
from __future__ import annotations

from datetime import datetime

import pytest

from kis_backtest.luxon.graph.edges import EdgeKind
from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.ingestors.phase1_ingestor import Phase1Ingestor
from kis_backtest.luxon.graph.nodes import NodeKind
from kis_backtest.luxon.integration.conviction_bridge import OrderProposal
from kis_backtest.luxon.integration.phase1_pipeline import Phase1CheckpointResult
from kis_backtest.portfolio.macro_regime import Regime, RegimeResult


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_regime_result(
    regime: Regime = Regime.RECOVERY,
    confidence: float = 0.85,
    score: float = 0.0,
) -> RegimeResult:
    """Build a synthetic RegimeResult (no real FRED call)."""
    return RegimeResult(
        regime=regime,
        confidence=confidence,
        score=score,
        positive_signals=3,
        negative_signals=2,
        neutral_signals=5,
        allocation={"equity": 0.50, "bond": 0.30, "cash": 0.20},
    )


def _make_checkpoint(
    regime: Regime | None = Regime.RECOVERY,
    ts: datetime | None = None,
) -> Phase1CheckpointResult:
    """Build a synthetic Phase1CheckpointResult."""
    return Phase1CheckpointResult(
        timestamp=ts or datetime(2026, 4, 11, 19, 40, 0),
        fred_series_loaded=10,
        fred_stale_count=1,
        tick_vault_stats={"total_files": 0},
        regime_result=_make_regime_result(regime) if regime else None,
        macro_indicator_count=10,
        errors=[],
    )


def _make_proposal(
    symbol: str = "005930",
    action: str = "BUY",
    position_pct: float = 0.15,
) -> OrderProposal:
    """Build a synthetic OrderProposal (conviction-bridge output shape)."""
    return OrderProposal(
        symbol=symbol,
        action=action,
        position_pct=position_pct,
        conviction=8.0,
        reason="test ingestor fixture",
        passed_gates=("gate1", "gate2:recovery", "gate3"),
    )


# ── Tests ────────────────────────────────────────────────────────────────


def test_ingest_checkpoint_with_regime() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = Phase1Ingestor(graph)
    checkpoint = _make_checkpoint(regime=Regime.RECOVERY)

    # Act
    regime_node_id = ingestor.ingest_checkpoint(checkpoint)

    # Assert — returned id references a MACRO_REGIME node in the graph
    assert regime_node_id is not None
    assert graph.has_node(regime_node_id)
    regime_node = graph.get_node(regime_node_id)
    assert regime_node is not None
    assert regime_node.kind is NodeKind.MACRO_REGIME

    # Payload should carry the classification details (field names lenient —
    # we only assert on the data we control, not the exact key names used by
    # the ingestor, to avoid brittle coupling).
    payload = regime_node.payload
    assert payload  # non-empty
    # At minimum the payload must expose the regime enum somehow (value or name).
    payload_repr = repr(payload).lower()
    assert "recovery" in payload_repr
    # Confidence + allocation should be reachable via the payload values.
    flat_values = list(payload.values())
    assert 0.85 in flat_values or any(
        isinstance(v, dict) and v.get("confidence") == 0.85 for v in flat_values
    ) or payload.get("confidence") == 0.85
    # Allocation dict should be present somewhere in the payload.
    assert any(
        isinstance(v, dict) and "equity" in v for v in flat_values
    ) or "equity" in payload_repr


def test_ingest_checkpoint_none_regime_returns_none() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = Phase1Ingestor(graph)
    checkpoint = _make_checkpoint(regime=None)

    # Act
    regime_node_id = ingestor.ingest_checkpoint(checkpoint)

    # Assert
    assert regime_node_id is None
    assert graph.node_count == 0
    assert graph.edge_count == 0


def test_ingest_proposal_buy_creates_symbol_event_and_edge() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = Phase1Ingestor(graph)
    proposal = _make_proposal(symbol="005930", action="BUY")

    # Act
    event_node_id = ingestor.ingest_proposal(proposal)

    # Assert — event node was created and returned
    assert event_node_id is not None
    event_node = graph.get_node(event_node_id)
    assert event_node is not None
    assert event_node.kind is NodeKind.EVENT

    # Symbol node was also created
    symbol_nodes = graph.nodes_by_kind(NodeKind.SYMBOL)
    assert len(symbol_nodes) == 1
    symbol_node = symbol_nodes[0]
    assert "005930" in symbol_node.node_id

    # CATALYST_FOR edge exists (event → symbol)
    catalyst_edges = graph.edges_by_kind(EdgeKind.CATALYST_FOR)
    assert len(catalyst_edges) == 1
    catalyst = catalyst_edges[0]
    assert catalyst.source_id == event_node_id
    assert catalyst.target_id == symbol_node.node_id

    # No sector or regime edges without explicit parameters
    assert graph.edges_by_kind(EdgeKind.BELONGS_TO) == []
    assert graph.edges_by_kind(EdgeKind.TRIGGERED_BY) == []


def test_ingest_proposal_with_regime_adds_triggered_by_edge() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = Phase1Ingestor(graph)
    checkpoint = _make_checkpoint(regime=Regime.RECOVERY)
    regime_node_id = ingestor.ingest_checkpoint(checkpoint)
    assert regime_node_id is not None  # sanity precondition

    proposal = _make_proposal(symbol="005930", action="BUY")

    # Act
    event_node_id = ingestor.ingest_proposal(
        proposal,
        regime_node_id=regime_node_id,
    )

    # Assert — TRIGGERED_BY edge runs event → macro_regime
    triggered_edges = graph.edges_by_kind(EdgeKind.TRIGGERED_BY)
    assert len(triggered_edges) == 1
    triggered = triggered_edges[0]
    assert triggered.source_id == event_node_id
    assert triggered.target_id == regime_node_id

    # CATALYST_FOR edge still exists alongside TRIGGERED_BY
    catalyst_edges = graph.edges_by_kind(EdgeKind.CATALYST_FOR)
    assert len(catalyst_edges) == 1
    assert catalyst_edges[0].source_id == event_node_id


def test_ingest_proposal_invalid_regime_node_id_raises() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = Phase1Ingestor(graph)
    proposal = _make_proposal(symbol="005930", action="BUY")

    # Act / Assert
    with pytest.raises(ValueError, match="macro_regime:ghost"):
        ingestor.ingest_proposal(
            proposal,
            regime_node_id="macro_regime:ghost",
        )

    # No partial state leaked on failure
    assert graph.nodes_by_kind(NodeKind.EVENT) == []


def test_ingest_proposal_with_sector_adds_sector_and_belongs_to() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = Phase1Ingestor(graph)
    proposal = _make_proposal(symbol="005930", action="BUY")

    # Act
    event_node_id = ingestor.ingest_proposal(
        proposal,
        sector="semiconductor",
    )

    # Assert — sector node added
    sector_nodes = graph.nodes_by_kind(NodeKind.SECTOR)
    assert len(sector_nodes) == 1
    sector_node = sector_nodes[0]
    assert "semiconductor" in sector_node.node_id

    # BELONGS_TO edge: symbol → sector
    belongs_edges = graph.edges_by_kind(EdgeKind.BELONGS_TO)
    assert len(belongs_edges) == 1
    belongs = belongs_edges[0]
    assert belongs.target_id == sector_node.node_id
    # Source of BELONGS_TO must be the symbol created by this proposal
    symbol_nodes = graph.nodes_by_kind(NodeKind.SYMBOL)
    assert len(symbol_nodes) == 1
    assert belongs.source_id == symbol_nodes[0].node_id

    # Event and catalyst edge still created
    assert graph.get_node(event_node_id) is not None
    assert len(graph.edges_by_kind(EdgeKind.CATALYST_FOR)) == 1


def test_ingest_proposal_symbol_idempotent() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = Phase1Ingestor(graph)
    proposal_one = _make_proposal(symbol="005930", action="BUY")
    proposal_two = _make_proposal(symbol="005930", action="BUY")

    # Act — same symbol ingested twice should not duplicate the SymbolNode
    first_event_id = ingestor.ingest_proposal(proposal_one)
    second_event_id = ingestor.ingest_proposal(proposal_two)

    # Assert — exactly one symbol node, but two distinct event nodes
    symbol_nodes = graph.nodes_by_kind(NodeKind.SYMBOL)
    assert len(symbol_nodes) == 1

    event_nodes = graph.nodes_by_kind(NodeKind.EVENT)
    assert len(event_nodes) == 2
    assert first_event_id != second_event_id

    # Two CATALYST_FOR edges, both targeting the same symbol
    catalyst_edges = graph.edges_by_kind(EdgeKind.CATALYST_FOR)
    assert len(catalyst_edges) == 2
    assert {e.target_id for e in catalyst_edges} == {symbol_nodes[0].node_id}
    assert {e.source_id for e in catalyst_edges} == {
        first_event_id,
        second_event_id,
    }
