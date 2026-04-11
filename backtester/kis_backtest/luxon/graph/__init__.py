"""Luxon Terminal — GothamGraph ontology layer (Phase 2 Sprint 5~6).

6 노드 / 5 엣지 속성 그래프 + ingestor 3종 + HTML 시각화 (PyVis).
상세 설계는 `docs/luxon/gotham_graph_spec.md` 참조.
"""
from kis_backtest.luxon.graph.edges import EdgeKind, GraphEdge
from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.ingestors.catalyst_ingestor import CatalystIngestor
from kis_backtest.luxon.graph.ingestors.correlated_ingestor import (
    CorrelatedIngestor,
)
from kis_backtest.luxon.graph.ingestors.cufa_ingestor import (
    CufaIngestor,
    CufaReportDigest,
)
from kis_backtest.luxon.graph.ingestors.phase1_ingestor import Phase1Ingestor
from kis_backtest.luxon.graph.nodes import GraphNode, NodeKind, make_node_id
from kis_backtest.luxon.graph.viz.html_renderer import (
    NODE_COLORS,
    render_graph_html,
)

__all__ = [
    "GothamGraph",
    "GraphNode",
    "GraphEdge",
    "NodeKind",
    "EdgeKind",
    "make_node_id",
    "Phase1Ingestor",
    "CatalystIngestor",
    "CorrelatedIngestor",
    "CufaIngestor",
    "CufaReportDigest",
    "render_graph_html",
    "NODE_COLORS",
]
