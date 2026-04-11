"""Smoke tests for the GothamGraph HTML renderer (Sprint 6 Phase 2).

Coverage:
    - render_graph_html writes a self-contained HTML file with node_ids +
      UTF-8 Korean labels + vis-network CDN reference + custom title.
    - Empty graph still renders a valid HTML file with the "0 nodes" header.
    - Unknown highlight_node_id falls back silently (no crash).
    - Nested output directory auto-created.
    - NODE_COLORS dict covers all 6 NodeKind values with hex color strings.

Style:
    - stdlib only, real GothamGraph instance (no mocks).
    - AAA pattern, one behaviour per test.
    - Fresh GothamGraph per test via helper.
    - tmp_path (pytest built-in) for isolated file I/O.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from kis_backtest.luxon.graph.edges import EdgeKind, GraphEdge
from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.nodes import GraphNode, NodeKind, make_node_id
from kis_backtest.luxon.graph.viz.html_renderer import NODE_COLORS, render_graph_html


# ── Helpers ──────────────────────────────────────────────────────────────


def _build_sample_graph() -> GothamGraph:
    """Build a 4-node / 3-edge sample graph with Korean labels.

    Nodes:
        symbol:005930 (삼성전자)
        sector:반도체
        person:이재용
        event:catalyst:005930:test1

    Edges:
        symbol → sector  (BELONGS_TO, weight=1.0)
        person → symbol  (HOLDS,      weight=0.3)
        event  → symbol  (CATALYST_FOR, weight=0.7)
    """
    graph = GothamGraph()
    ts = datetime(2026, 4, 12, 10, 0, 0)

    symbol_node = GraphNode(
        node_id=make_node_id(NodeKind.SYMBOL, "005930"),
        kind=NodeKind.SYMBOL,
        label="삼성전자",
        timestamp=ts,
        payload={"symbol": "005930"},
    )
    sector_node = GraphNode(
        node_id=make_node_id(NodeKind.SECTOR, "반도체"),
        kind=NodeKind.SECTOR,
        label="반도체",
        timestamp=ts,
        payload={"name": "반도체"},
    )
    person_node = GraphNode(
        node_id=make_node_id(NodeKind.PERSON, "이재용"),
        kind=NodeKind.PERSON,
        label="이재용",
        timestamp=ts,
        payload={"name": "이재용", "role": "CEO"},
    )
    event_node = GraphNode(
        node_id=make_node_id(NodeKind.EVENT, "catalyst:005930:test1"),
        kind=NodeKind.EVENT,
        label="HBM 양산",
        timestamp=ts,
        payload={"source": "catalyst", "name": "HBM4 양산"},
    )
    graph.add_node(symbol_node)
    graph.add_node(sector_node)
    graph.add_node(person_node)
    graph.add_node(event_node)

    graph.add_edge(
        GraphEdge(
            source_id=symbol_node.node_id,
            target_id=sector_node.node_id,
            kind=EdgeKind.BELONGS_TO,
            weight=1.0,
        )
    )
    graph.add_edge(
        GraphEdge(
            source_id=person_node.node_id,
            target_id=symbol_node.node_id,
            kind=EdgeKind.HOLDS,
            weight=0.3,
        )
    )
    graph.add_edge(
        GraphEdge(
            source_id=event_node.node_id,
            target_id=symbol_node.node_id,
            kind=EdgeKind.CATALYST_FOR,
            weight=0.7,
        )
    )
    return graph


def _build_minimal_graph_with_edge() -> GothamGraph:
    """Two-node graph with a single BELONGS_TO edge — for the highlight test."""
    graph = GothamGraph()
    ts = datetime(2026, 4, 12, 10, 0, 0)

    symbol_node = GraphNode(
        node_id=make_node_id(NodeKind.SYMBOL, "005930"),
        kind=NodeKind.SYMBOL,
        label="삼성전자",
        timestamp=ts,
        payload={"symbol": "005930"},
    )
    sector_node = GraphNode(
        node_id=make_node_id(NodeKind.SECTOR, "반도체"),
        kind=NodeKind.SECTOR,
        label="반도체",
        timestamp=ts,
        payload={"name": "반도체"},
    )
    graph.add_node(symbol_node)
    graph.add_node(sector_node)
    graph.add_edge(
        GraphEdge(
            source_id=symbol_node.node_id,
            target_id=sector_node.node_id,
            kind=EdgeKind.BELONGS_TO,
            weight=1.0,
        )
    )
    return graph


def _build_single_node_graph() -> GothamGraph:
    """Single-node graph — for the parent-directory-creation test."""
    graph = GothamGraph()
    ts = datetime(2026, 4, 12, 10, 0, 0)
    node = GraphNode(
        node_id=make_node_id(NodeKind.SYMBOL, "005930"),
        kind=NodeKind.SYMBOL,
        label="삼성전자",
        timestamp=ts,
        payload={"symbol": "005930"},
    )
    graph.add_node(node)
    return graph


# ── Tests ────────────────────────────────────────────────────────────────


def test_render_graph_html_creates_file_with_nodes_and_edges(
    tmp_path: Path,
) -> None:
    # Arrange
    graph = _build_sample_graph()
    output_path = tmp_path / "out.html"

    # Act
    render_graph_html(graph, output_path, title="Smoke Test")

    # Assert — file materialised
    assert output_path.exists()
    html = output_path.read_text(encoding="utf-8")
    assert len(html) > 500, (
        f"HTML file suspiciously small ({len(html)} chars) — "
        "template alone should exceed 500 chars."
    )

    # Custom title must appear in the document
    assert "Smoke Test" in html

    # vis-network CDN reference (any unpkg.com vis-network script tag)
    assert "vis-network" in html

    # Node IDs must appear as JS literals. PyVis/jinja2 `tojson` 필터가
    # ensure_ascii=True 로 한글을 \uXXXX 시퀀스로 escape 하므로 HTML 파일
    # 텍스트에는 escape 형태로 literal 저장된다 (브라우저에서 JS 가 decode).
    assert "symbol:005930" in html
    assert "sector:\\ubc18\\ub3c4\\uccb4" in html  # 반도체 (unicode escape)
    assert "person:\\uc774\\uc7ac\\uc6a9" in html  # 이재용 (unicode escape)

    # Closing html tag
    assert "</html>" in html


def test_render_graph_html_empty_graph_still_renders(tmp_path: Path) -> None:
    # Arrange
    graph = GothamGraph()
    output_path = tmp_path / "empty.html"

    # Act — must not raise on an empty graph
    render_graph_html(graph, output_path)

    # Assert
    assert output_path.exists()
    html = output_path.read_text(encoding="utf-8")
    assert len(html) > 0
    assert "</html>" in html

    # Header stats line per contract: "{node_count} nodes · {edge_count} edges"
    assert "0 nodes" in html
    assert "0 edges" in html


def test_render_graph_html_with_unknown_highlight_does_not_crash(
    tmp_path: Path,
) -> None:
    # Arrange
    graph = _build_minimal_graph_with_edge()
    output_path = tmp_path / "out.html"

    # Act — unknown highlight node id must be silently ignored
    render_graph_html(
        graph,
        output_path,
        highlight_node_id="ghost:nonexistent",
    )

    # Assert
    assert output_path.exists()
    html = output_path.read_text(encoding="utf-8")
    assert len(html) > 0
    assert "symbol:005930" in html
    # Korean node_id → unicode-escaped form in PyVis-generated HTML
    assert "sector:\\ubc18\\ub3c4\\uccb4" in html  # 반도체


def test_render_graph_html_creates_parent_directory(tmp_path: Path) -> None:
    # Arrange
    graph = _build_single_node_graph()
    nested_path = tmp_path / "deep" / "nested" / "out.html"
    assert not nested_path.parent.exists()  # precondition

    # Act
    render_graph_html(graph, nested_path)

    # Assert
    assert nested_path.exists()
    assert (tmp_path / "deep" / "nested").is_dir()
    html = nested_path.read_text(encoding="utf-8")
    assert "symbol:005930" in html
    assert "</html>" in html


def test_node_colors_has_all_six_kinds() -> None:
    # Arrange / Act — direct inspection of the exported NODE_COLORS dict
    expected_kinds = {
        NodeKind.SYMBOL,
        NodeKind.SECTOR,
        NodeKind.EVENT,
        NodeKind.THEME,
        NodeKind.MACRO_REGIME,
        NodeKind.PERSON,
    }

    # Assert — coverage of all 6 NodeKind values
    assert set(NODE_COLORS.keys()) == expected_kinds

    # Each colour must be a hex string like "#4a90e2" (7 chars).
    for kind, color in NODE_COLORS.items():
        assert isinstance(color, str), (
            f"NODE_COLORS[{kind}] is not a str: {type(color).__name__}"
        )
        assert color.startswith("#"), (
            f"NODE_COLORS[{kind}] does not start with '#': {color!r}"
        )
        assert len(color) == 7, (
            f"NODE_COLORS[{kind}] is not a 7-char hex color: {color!r}"
        )
