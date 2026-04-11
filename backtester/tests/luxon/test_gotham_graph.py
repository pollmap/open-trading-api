"""Tests for GothamGraph core (Sprint 5 Phase 2).

Coverage:
    - NodeKind / EdgeKind enum formatting via make_node_id
    - Node CRUD: add/get/duplicate/remove (+ incident edges)
    - Edge validation: both endpoints must exist
    - Query: has_edge filter, nodes_by_kind, edges_by_kind
    - Traversal: neighbors (out/in/both), three_hop linear & empty path
    - Error paths: invalid direction, missing node
    - Persistence: pickle save/load round trip (tmp_path)

Style:
    - stdlib only, real classes (no mocking)
    - AAA pattern, one behavior per test
    - Each test gets a fresh GothamGraph (no shared state)
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from kis_backtest.luxon.graph.edges import EdgeKind, GraphEdge
from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.nodes import GraphNode, NodeKind, make_node_id


# ── Helpers ──────────────────────────────────────────────────────────────

def _node(
    kind: NodeKind,
    key: str,
    label: str | None = None,
    ts: datetime | None = None,
    payload: dict[str, Any] | None = None,
) -> GraphNode:
    """Build a GraphNode with sensible defaults for terse test setup."""
    return GraphNode(
        node_id=make_node_id(kind, key),
        kind=kind,
        label=label or key,
        timestamp=ts or datetime(2026, 4, 11, 19, 0, 0),
        payload=payload or {},
    )


def _edge(
    source: str,
    target: str,
    kind: EdgeKind,
    weight: float = 1.0,
) -> GraphEdge:
    """Build a GraphEdge with sensible defaults for terse test setup."""
    return GraphEdge(
        source_id=source,
        target_id=target,
        kind=kind,
        weight=weight,
    )


# ── Tests ────────────────────────────────────────────────────────────────


def test_make_node_id_format() -> None:
    # Arrange / Act
    symbol_id = make_node_id(NodeKind.SYMBOL, "005930")
    regime_id = make_node_id(NodeKind.MACRO_REGIME, "recovery-2026")

    # Assert
    assert symbol_id == "symbol:005930"
    assert regime_id == "macro_regime:recovery-2026"


def test_add_node_and_get() -> None:
    # Arrange
    graph = GothamGraph()
    node = _node(NodeKind.SYMBOL, "005930", label="Samsung Electronics")

    # Act
    graph.add_node(node)

    # Assert
    assert graph.has_node("symbol:005930") is True
    fetched = graph.get_node("symbol:005930")
    assert fetched is not None
    assert fetched.node_id == "symbol:005930"
    assert fetched.kind is NodeKind.SYMBOL
    assert fetched.label == "Samsung Electronics"
    assert graph.node_count == 1
    assert len(graph) == 1


def test_add_duplicate_node_raises() -> None:
    # Arrange
    graph = GothamGraph()
    node = _node(NodeKind.SYMBOL, "005930")
    graph.add_node(node)

    # Act / Assert
    with pytest.raises(ValueError, match="symbol:005930"):
        graph.add_node(_node(NodeKind.SYMBOL, "005930", label="duplicate"))


def test_add_edge_requires_both_endpoints() -> None:
    # Arrange
    graph = GothamGraph()
    symbol = _node(NodeKind.SYMBOL, "005930")
    sector = _node(NodeKind.SECTOR, "semiconductor")
    graph.add_node(symbol)
    graph.add_node(sector)

    # Act / Assert — missing target
    with pytest.raises(ValueError, match="target_id"):
        graph.add_edge(
            _edge("symbol:005930", "sector:unknown", EdgeKind.BELONGS_TO)
        )

    # Act / Assert — missing source
    with pytest.raises(ValueError, match="source_id"):
        graph.add_edge(
            _edge("symbol:unknown", "sector:semiconductor", EdgeKind.BELONGS_TO)
        )

    # Valid edge still works after failed attempts
    graph.add_edge(
        _edge("symbol:005930", "sector:semiconductor", EdgeKind.BELONGS_TO)
    )
    assert graph.edge_count == 1


def test_has_edge_with_and_without_kind_filter() -> None:
    # Arrange
    graph = GothamGraph()
    graph.add_node(_node(NodeKind.SYMBOL, "005930"))
    graph.add_node(_node(NodeKind.SECTOR, "semiconductor"))
    graph.add_edge(
        _edge("symbol:005930", "sector:semiconductor", EdgeKind.BELONGS_TO)
    )

    # Act / Assert — no kind filter
    assert graph.has_edge("symbol:005930", "sector:semiconductor") is True

    # Act / Assert — matching kind filter
    assert (
        graph.has_edge(
            "symbol:005930",
            "sector:semiconductor",
            kind=EdgeKind.BELONGS_TO,
        )
        is True
    )

    # Act / Assert — non-matching kind filter
    assert (
        graph.has_edge(
            "symbol:005930",
            "sector:semiconductor",
            kind=EdgeKind.CATALYST_FOR,
        )
        is False
    )

    # Act / Assert — unknown source
    assert graph.has_edge("symbol:ghost", "sector:semiconductor") is False


def test_remove_node_removes_incident_edges() -> None:
    # Arrange
    graph = GothamGraph()
    graph.add_node(_node(NodeKind.SYMBOL, "005930"))
    graph.add_node(_node(NodeKind.SECTOR, "semiconductor"))
    graph.add_node(_node(NodeKind.EVENT, "evt-001"))
    graph.add_edge(
        _edge("symbol:005930", "sector:semiconductor", EdgeKind.BELONGS_TO)
    )
    graph.add_edge(
        _edge("event:evt-001", "symbol:005930", EdgeKind.CATALYST_FOR)
    )
    assert graph.edge_count == 2

    # Act
    removed = graph.remove_node("symbol:005930")

    # Assert
    assert removed is True
    assert graph.has_node("symbol:005930") is False
    assert graph.edge_count == 0  # both edges touched symbol:005930
    # sector & event still present
    assert graph.has_node("sector:semiconductor") is True
    assert graph.has_node("event:evt-001") is True
    # neighbors from the surviving nodes no longer reference symbol
    assert graph.neighbors("event:evt-001", direction="out") == []


def test_remove_missing_node_returns_false() -> None:
    # Arrange
    graph = GothamGraph()
    graph.add_node(_node(NodeKind.SYMBOL, "005930"))

    # Act
    removed = graph.remove_node("symbol:ghost")

    # Assert
    assert removed is False
    assert graph.node_count == 1


def test_nodes_by_kind_and_edges_by_kind() -> None:
    # Arrange
    graph = GothamGraph()
    graph.add_node(_node(NodeKind.SYMBOL, "005930"))
    graph.add_node(_node(NodeKind.SYMBOL, "000660"))
    graph.add_node(_node(NodeKind.SECTOR, "semiconductor"))
    graph.add_node(_node(NodeKind.EVENT, "evt-001"))
    graph.add_edge(
        _edge("symbol:005930", "sector:semiconductor", EdgeKind.BELONGS_TO)
    )
    graph.add_edge(
        _edge("symbol:000660", "sector:semiconductor", EdgeKind.BELONGS_TO)
    )
    graph.add_edge(
        _edge("event:evt-001", "symbol:005930", EdgeKind.CATALYST_FOR)
    )

    # Act
    symbol_nodes = graph.nodes_by_kind(NodeKind.SYMBOL)
    sector_nodes = graph.nodes_by_kind(NodeKind.SECTOR)
    theme_nodes = graph.nodes_by_kind(NodeKind.THEME)
    belongs_edges = graph.edges_by_kind(EdgeKind.BELONGS_TO)
    catalyst_edges = graph.edges_by_kind(EdgeKind.CATALYST_FOR)
    holds_edges = graph.edges_by_kind(EdgeKind.HOLDS)

    # Assert
    assert {n.node_id for n in symbol_nodes} == {
        "symbol:005930",
        "symbol:000660",
    }
    assert len(sector_nodes) == 1
    assert sector_nodes[0].node_id == "sector:semiconductor"
    assert theme_nodes == []
    assert len(belongs_edges) == 2
    assert all(e.kind is EdgeKind.BELONGS_TO for e in belongs_edges)
    assert len(catalyst_edges) == 1
    assert catalyst_edges[0].source_id == "event:evt-001"
    assert holds_edges == []


def test_neighbors_out_in_both() -> None:
    # Arrange — a small directed graph:
    #   symbol:A --BELONGS_TO--> sector:S
    #   event:E --CATALYST_FOR--> symbol:A
    graph = GothamGraph()
    graph.add_node(_node(NodeKind.SYMBOL, "A"))
    graph.add_node(_node(NodeKind.SECTOR, "S"))
    graph.add_node(_node(NodeKind.EVENT, "E"))
    graph.add_edge(_edge("symbol:A", "sector:S", EdgeKind.BELONGS_TO))
    graph.add_edge(_edge("event:E", "symbol:A", EdgeKind.CATALYST_FOR))

    # Act / Assert — out-neighbors of symbol:A
    out_neighbors = graph.neighbors("symbol:A", direction="out")
    assert [n.node_id for n in out_neighbors] == ["sector:S"]

    # Act / Assert — in-neighbors of symbol:A
    in_neighbors = graph.neighbors("symbol:A", direction="in")
    assert [n.node_id for n in in_neighbors] == ["event:E"]

    # Act / Assert — both directions (dedup)
    both_neighbors = graph.neighbors("symbol:A", direction="both")
    both_ids = [n.node_id for n in both_neighbors]
    assert set(both_ids) == {"sector:S", "event:E"}
    # Dedup check: no duplicates even if both sides share an endpoint
    assert len(both_ids) == len(set(both_ids))

    # Act / Assert — edge-kind filter narrows the result
    filtered = graph.neighbors(
        "symbol:A",
        edge_kind=EdgeKind.BELONGS_TO,
        direction="out",
    )
    assert [n.node_id for n in filtered] == ["sector:S"]

    filtered_catalyst = graph.neighbors(
        "symbol:A",
        edge_kind=EdgeKind.CATALYST_FOR,
        direction="out",
    )
    assert filtered_catalyst == []


def test_neighbors_invalid_direction_raises() -> None:
    # Arrange
    graph = GothamGraph()
    graph.add_node(_node(NodeKind.SYMBOL, "A"))

    # Act / Assert
    with pytest.raises(ValueError, match="direction"):
        graph.neighbors("symbol:A", direction="sideways")


def test_neighbors_missing_node_raises() -> None:
    # Arrange
    graph = GothamGraph()
    graph.add_node(_node(NodeKind.SYMBOL, "A"))

    # Act / Assert
    with pytest.raises(KeyError, match="symbol:ghost"):
        graph.neighbors("symbol:ghost", direction="out")


def test_three_hop_linear_path() -> None:
    # Arrange — linear chain:
    #   event:E --CATALYST_FOR--> symbol:A --BELONGS_TO--> sector:S
    graph = GothamGraph()
    graph.add_node(_node(NodeKind.EVENT, "E"))
    graph.add_node(_node(NodeKind.SYMBOL, "A"))
    graph.add_node(_node(NodeKind.SECTOR, "S"))
    graph.add_edge(_edge("event:E", "symbol:A", EdgeKind.CATALYST_FOR))
    graph.add_edge(_edge("symbol:A", "sector:S", EdgeKind.BELONGS_TO))

    # Act
    paths = graph.three_hop(
        "event:E",
        kinds_path=(EdgeKind.CATALYST_FOR, EdgeKind.BELONGS_TO),
    )

    # Assert
    assert len(paths) == 1
    path = paths[0]
    assert [n.node_id for n in path] == [
        "event:E",
        "symbol:A",
        "sector:S",
    ]

    # Act / Assert — kinds_path that does not match yields no paths
    no_paths = graph.three_hop(
        "event:E",
        kinds_path=(EdgeKind.BELONGS_TO, EdgeKind.CATALYST_FOR),
    )
    assert no_paths == []


def test_three_hop_empty_path_returns_start() -> None:
    # Arrange
    graph = GothamGraph()
    start = _node(NodeKind.SYMBOL, "A")
    graph.add_node(start)

    # Act
    paths = graph.three_hop("symbol:A", kinds_path=())

    # Assert
    assert len(paths) == 1
    assert len(paths[0]) == 1
    assert paths[0][0].node_id == "symbol:A"

    # Missing start → KeyError
    with pytest.raises(KeyError, match="symbol:ghost"):
        graph.three_hop("symbol:ghost", kinds_path=())


def test_pickle_save_load_round_trip(tmp_path: Path) -> None:
    # Arrange
    graph = GothamGraph()
    payload = {"market_cap": 500_000_000_000, "currency": "KRW"}
    graph.add_node(
        _node(
            NodeKind.SYMBOL,
            "005930",
            label="Samsung Electronics",
            ts=datetime(2026, 4, 11, 12, 0, 0),
            payload=payload,
        )
    )
    graph.add_node(_node(NodeKind.SECTOR, "semiconductor"))
    graph.add_edge(
        _edge(
            "symbol:005930",
            "sector:semiconductor",
            EdgeKind.BELONGS_TO,
            weight=0.9,
        )
    )

    # Act
    save_path = tmp_path / "gotham.pkl"
    graph.save(save_path)
    loaded = GothamGraph.load(save_path)

    # Assert — counts preserved
    assert loaded.node_count == graph.node_count == 2
    assert loaded.edge_count == graph.edge_count == 1

    # Assert — sample node content preserved
    loaded_symbol = loaded.get_node("symbol:005930")
    assert loaded_symbol is not None
    assert loaded_symbol.label == "Samsung Electronics"
    assert loaded_symbol.kind is NodeKind.SYMBOL
    assert loaded_symbol.payload == payload
    assert loaded_symbol.timestamp == datetime(2026, 4, 11, 12, 0, 0)

    # Assert — edge still traversable after load (adj cache rebuilt)
    assert loaded.has_edge(
        "symbol:005930",
        "sector:semiconductor",
        kind=EdgeKind.BELONGS_TO,
    )
    neighbors = loaded.neighbors("symbol:005930", direction="out")
    assert [n.node_id for n in neighbors] == ["sector:semiconductor"]
