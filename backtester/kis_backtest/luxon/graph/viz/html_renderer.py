"""
Luxon Terminal — GothamGraph HTML 시각화 (Sprint 6 Phase 2, PyVis backend).

PyVis(https://pyvis.readthedocs.io) 를 얇게 래핑. 직접 vis-network HTML/JS 를
조립하는 대신 battle-tested 라이브러리에 위임하고, 프로젝트별 스타일
(다크 테마, NodeKind 팔레트, 헤더 stats)만 후처리로 주입한다.

팔레트 (NodeKind → color hex):
    SYMBOL       → #4a90e2 (파랑)
    SECTOR       → #50c878 (녹색)
    EVENT        → #f5a623 (주황)
    THEME        → #9b59b6 (보라)
    MACRO_REGIME → #e74c3c (빨강)
    PERSON       → #7f8c8d (회색)
"""
from __future__ import annotations

import html as html_mod
from pathlib import Path

from pyvis.network import Network

from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.nodes import GraphNode, NodeKind

# NodeKind → color hex 팔레트
NODE_COLORS: dict[NodeKind, str] = {
    NodeKind.SYMBOL:       "#4a90e2",
    NodeKind.SECTOR:       "#50c878",
    NodeKind.EVENT:        "#f5a623",
    NodeKind.THEME:        "#9b59b6",
    NodeKind.MACRO_REGIME: "#e74c3c",
    NodeKind.PERSON:       "#7f8c8d",
}

# 하이라이트 모드에서 바깥 노드에 적용되는 fade 색
FADE_COLOR = "#2d2d2d"

_DARK_BODY_STYLE = (
    "background:#1a1a1a;color:#e0e0e0;"
    "font-family:system-ui,-apple-system,sans-serif;margin:0;padding:0;"
)


def render_graph_html(
    graph: GothamGraph,
    output_path: Path,
    title: str = "Luxon GothamGraph",
    highlight_node_id: str | None = None,
) -> None:
    """GothamGraph → self-contained HTML file (PyVis / vis-network CDN).

    Args:
        graph: 렌더링할 GothamGraph.
        output_path: 출력 HTML 경로. 부모 디렉터리 없으면 자동 생성.
        title: <h1> 및 <title> 에 쓰일 제목.
        highlight_node_id: 주어지면 해당 노드 + 3-hop outgoing 이웃을 강조하고
            나머지 노드는 FADE_COLOR 로 흐리게 처리. 그래프에 없으면 silently ignore.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 빈 그래프는 PyVis 렌더링이 불안정하므로 최소 HTML 을 직접 생성.
    if graph.node_count == 0:
        output_path.write_text(
            _empty_graph_html(title), encoding="utf-8",
        )
        return

    highlighted = _collect_three_hop_ids(graph, highlight_node_id)

    net = Network(
        height="800px",
        width="100%",
        bgcolor="#1a1a1a",
        font_color="#e0e0e0",
        directed=True,
        cdn_resources="remote",
        notebook=False,
    )
    net.toggle_physics(True)

    for node in graph._nodes.values():
        color = NODE_COLORS.get(node.kind, "#888888")
        if highlighted and node.node_id not in highlighted:
            color = FADE_COLOR
        net.add_node(
            node.node_id,
            label=node.label,
            color=color,
            title=_build_tooltip(node),
            group=node.kind.value,
        )

    for edge in graph._edges:
        width = max(1.0, min(5.0, edge.weight * 5.0))
        net.add_edge(
            edge.source_id,
            edge.target_id,
            title=edge.kind.value,
            width=width,
        )

    # PyVis 0.3.x: generate_html() 이 HTML 문자열 반환. 부작용 없음.
    body_html = net.generate_html(notebook=False)

    # 헤더 (title + stats) 를 <body> 직후에 주입.
    header = _build_header(title, graph.node_count, graph.edge_count)
    if "<body>" in body_html:
        body_html = body_html.replace("<body>", f"<body>{header}", 1)
    else:
        # PyVis 템플릿에 <body> 없는 edge case — 끝에 붙임.
        body_html = header + body_html

    # <title> 을 사용자 title 로 교체.
    escaped_title = html_mod.escape(title)
    if "<title>" in body_html and "</title>" in body_html:
        import re
        body_html = re.sub(
            r"<title>.*?</title>",
            f"<title>{escaped_title}</title>",
            body_html,
            count=1,
        )

    output_path.write_text(body_html, encoding="utf-8")


# ── Helpers ─────────────────────────────────────────────────────────


def _collect_three_hop_ids(
    graph: GothamGraph, start_id: str | None,
) -> set[str]:
    """3-hop outgoing BFS (모든 edge kind). start_id 가 없거나 미존재면 빈 set.

    빈 set 반환 시 caller 는 하이라이트를 적용하지 않는다.
    """
    if start_id is None or not graph.has_node(start_id):
        return set()
    visited: set[str] = {start_id}
    frontier: set[str] = {start_id}
    for _ in range(3):
        next_frontier: set[str] = set()
        for nid in frontier:
            for edge in graph._adj_out.get(nid, []):
                if edge.target_id not in visited:
                    visited.add(edge.target_id)
                    next_frontier.add(edge.target_id)
        frontier = next_frontier
        if not frontier:
            break
    return visited


def _build_tooltip(node: GraphNode) -> str:
    """GraphNode payload → vis-network tooltip HTML. 모든 값 html.escape."""
    parts: list[str] = [
        f"<b>{html_mod.escape(node.node_id)}</b>",
        f"kind: {html_mod.escape(node.kind.value)}",
    ]
    for k, v in node.payload.items():
        parts.append(
            f"{html_mod.escape(str(k))}: {html_mod.escape(str(v))}"
        )
    return "<br>".join(parts)


def _build_header(title: str, node_count: int, edge_count: int) -> str:
    """다크 테마 헤더 HTML 조각 (title + stats line)."""
    safe_title = html_mod.escape(title)
    return (
        f'<div style="{_DARK_BODY_STYLE}padding:1rem 1.5rem;">'
        f'<h1 style="margin:0;color:#e0e0e0;font-size:1.5rem;">{safe_title}</h1>'
        f'<p style="margin:0.25rem 0 0;color:#888;font-size:0.9rem;">'
        f"{node_count} nodes · {edge_count} edges"
        f"</p></div>"
    )


def _empty_graph_html(title: str) -> str:
    """Empty graph 용 최소 HTML. vis-network CDN reference 포함."""
    safe_title = html_mod.escape(title)
    return (
        "<!DOCTYPE html>"
        '<html lang="ko"><head>'
        '<meta charset="utf-8">'
        f"<title>{safe_title}</title>"
        "</head>"
        f'<body style="{_DARK_BODY_STYLE}padding:2rem;">'
        f'<h1 style="margin:0;color:#e0e0e0;">{safe_title}</h1>'
        '<p style="color:#888;margin:0.25rem 0 0;">0 nodes · 0 edges</p>'
        '<p style="color:#666;margin-top:2rem;">(no nodes — empty graph)</p>'
        "<!-- vis-network CDN placeholder: "
        "https://unpkg.com/vis-network/standalone/umd/vis-network.min.js -->"
        "</body></html>"
    )


__all__ = ["render_graph_html", "NODE_COLORS"]
