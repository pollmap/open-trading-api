"""GothamGraph 코어 구현 (Sprint 5 Phase 2 MVP).

순수 Python stdlib 기반 속성 그래프. 외부 의존성 0 (networkx/neo4py/py2neo 금지).
"""

from __future__ import annotations

import pickle
from collections import defaultdict
from pathlib import Path

from kis_backtest.luxon.graph.edges import EdgeKind, GraphEdge
from kis_backtest.luxon.graph.nodes import GraphNode, NodeKind


class GothamGraph:
    """순수 Python stdlib 기반 속성 그래프 (Sprint 5 Phase 2 MVP).

    스키마:
        _nodes: dict[node_id → GraphNode]
        _edges: list[GraphEdge]
        _adj_out: dict[node_id → list[GraphEdge]]  # outgoing 인접 캐시
        _adj_in:  dict[node_id → list[GraphEdge]]  # incoming 인접 캐시

    금기:
        - networkx, neo4py, py2neo 등 외부 의존 0
        - 노드/엣지 변형 0 (frozen dataclass 사용)
        - 사이클 탐지 0 (그래프 일반적으로 사이클 허용)
    """

    def __init__(self) -> None:
        self._nodes: dict[str, GraphNode] = {}
        self._edges: list[GraphEdge] = []
        self._adj_out: dict[str, list[GraphEdge]] = defaultdict(list)
        self._adj_in: dict[str, list[GraphEdge]] = defaultdict(list)

    # ── CRUD ──────────────────────────────────────────────

    def add_node(self, node: GraphNode) -> None:
        """노드 추가. 중복 node_id 시 ValueError."""
        if node.node_id in self._nodes:
            raise ValueError(
                f"Duplicate node_id: {node.node_id!r} already exists in graph."
            )
        self._nodes[node.node_id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        """엣지 추가. source_id 또는 target_id가 존재하지 않으면 ValueError."""
        if edge.source_id not in self._nodes:
            raise ValueError(
                f"Edge source_id {edge.source_id!r} not found in graph."
            )
        if edge.target_id not in self._nodes:
            raise ValueError(
                f"Edge target_id {edge.target_id!r} not found in graph."
            )
        self._edges.append(edge)
        self._adj_out[edge.source_id].append(edge)
        self._adj_in[edge.target_id].append(edge)

    def get_node(self, node_id: str) -> GraphNode | None:
        """노드 조회. 없으면 None."""
        return self._nodes.get(node_id)

    def has_node(self, node_id: str) -> bool:
        """노드 존재 여부."""
        return node_id in self._nodes

    def has_edge(
        self,
        source: str,
        target: str,
        kind: EdgeKind | None = None,
    ) -> bool:
        """source→target 엣지 존재 여부. kind 지정 시 해당 kind만 확인."""
        if source not in self._nodes:
            return False
        for edge in self._adj_out.get(source, []):
            if edge.target_id != target:
                continue
            if kind is not None and edge.kind != kind:
                continue
            return True
        return False

    def remove_node(self, node_id: str) -> bool:
        """노드 + 인접 엣지 전부 제거. 없으면 False, 있었으면 True."""
        if node_id not in self._nodes:
            return False

        # 연결된 엣지 전부 제거 (source 또는 target 가 node_id)
        remaining_edges: list[GraphEdge] = []
        for edge in self._edges:
            if edge.source_id == node_id or edge.target_id == node_id:
                continue
            remaining_edges.append(edge)
        self._edges = remaining_edges

        # adj 캐시에서 node_id 관련 항목 제거
        self._adj_out.pop(node_id, None)
        self._adj_in.pop(node_id, None)
        for other_id in list(self._adj_out.keys()):
            self._adj_out[other_id] = [
                e for e in self._adj_out[other_id] if e.target_id != node_id
            ]
        for other_id in list(self._adj_in.keys()):
            self._adj_in[other_id] = [
                e for e in self._adj_in[other_id] if e.source_id != node_id
            ]

        del self._nodes[node_id]
        return True

    # ── Query ─────────────────────────────────────────────

    def nodes_by_kind(self, kind: NodeKind) -> list[GraphNode]:
        """특정 kind 의 노드 리스트. 순서 무관."""
        return [node for node in self._nodes.values() if node.kind == kind]

    def edges_by_kind(self, kind: EdgeKind) -> list[GraphEdge]:
        """특정 kind 의 엣지 리스트. 원본 _edges 순서 유지."""
        return [edge for edge in self._edges if edge.kind == kind]

    def neighbors(
        self,
        node_id: str,
        edge_kind: EdgeKind | None = None,
        direction: str = "out",
    ) -> list[GraphNode]:
        """인접 노드 리스트.

        Args:
            node_id: 시작 노드.
            edge_kind: 엣지 종류 필터 (None = 모든 종류).
            direction: "out" (outgoing) | "in" (incoming) | "both" (양방향 dedup).

        Returns:
            중복 제거된 이웃 노드 리스트. 순서는 insertion 기반.

        Raises:
            ValueError: direction 이 "out"/"in"/"both" 아닌 경우.
            KeyError: node_id 가 그래프에 없는 경우.
        """
        if direction not in ("out", "in", "both"):
            raise ValueError(
                f"Invalid direction {direction!r}. "
                "Expected 'out', 'in', or 'both'."
            )
        if node_id not in self._nodes:
            raise KeyError(f"Node {node_id!r} not found in graph.")

        seen: set[str] = set()
        result: list[GraphNode] = []

        def _collect(edges: list[GraphEdge], pick_target: bool) -> None:
            for edge in edges:
                if edge_kind is not None and edge.kind != edge_kind:
                    continue
                neighbor_id = edge.target_id if pick_target else edge.source_id
                if neighbor_id in seen:
                    continue
                neighbor_node = self._nodes.get(neighbor_id)
                if neighbor_node is None:
                    continue
                seen.add(neighbor_id)
                result.append(neighbor_node)

        if direction in ("out", "both"):
            _collect(self._adj_out.get(node_id, []), pick_target=True)
        if direction in ("in", "both"):
            _collect(self._adj_in.get(node_id, []), pick_target=False)

        return result

    def three_hop(
        self,
        start_id: str,
        kinds_path: tuple[EdgeKind, ...],
    ) -> list[list[GraphNode]]:
        """kinds_path 시퀀스를 따라 경로 탐색 (BFS).

        각 hop 에서 해당 kind 의 outgoing 엣지만 따라간다.
        (역방향이 필요한 경우 caller 가 neighbors(direction="in") 조합.)

        Args:
            start_id: 시작 노드 ID.
            kinds_path: 따라갈 엣지 kind 시퀀스. 길이 1~3 권장.

        Returns:
            경로 리스트. 각 경로는 [start_node, hop1, hop2, ...] 형태의 GraphNode 리스트.
            중복 경로는 허용 (동일 노드 재방문 허용 — 사이클 가능).
            빈 kinds_path 는 [[start_node]] 반환.

        Raises:
            KeyError: start_id 가 없는 경우.
        """
        if start_id not in self._nodes:
            raise KeyError(f"Node {start_id!r} not found in graph.")

        start_node = self._nodes[start_id]
        if not kinds_path:
            return [[start_node]]

        # BFS with path tracking.
        # frontier holds list of paths (each path is list[GraphNode]).
        frontier: list[list[GraphNode]] = [[start_node]]
        for hop_kind in kinds_path:
            next_frontier: list[list[GraphNode]] = []
            for path in frontier:
                current = path[-1]
                for edge in self._adj_out.get(current.node_id, []):
                    if edge.kind != hop_kind:
                        continue
                    neighbor_node = self._nodes.get(edge.target_id)
                    if neighbor_node is None:
                        continue
                    next_frontier.append([*path, neighbor_node])
            frontier = next_frontier
            if not frontier:
                return []

        return frontier

    # ── Serialization ─────────────────────────────────────

    def save(self, path: Path) -> None:
        """pickle 로 저장. self._nodes 와 self._edges 만 저장 (adj 는 재구축 대상).

        부모 디렉터리 없으면 자동 생성.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (self._nodes, self._edges)
        with path.open("wb") as fp:
            pickle.dump(payload, fp)

    @classmethod
    def load(cls, path: Path) -> GothamGraph:
        """pickle 로부터 로드. adj 는 __init__ 후 재구축 (_rebuild_adj)."""
        with path.open("rb") as fp:
            payload = pickle.load(fp)
        nodes, edges = payload
        instance = cls()
        instance._nodes = dict(nodes)
        instance._edges = list(edges)
        instance._rebuild_adj()
        return instance

    # ── Props / dunders ───────────────────────────────────

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def __len__(self) -> int:
        return self.node_count

    def __repr__(self) -> str:
        return (
            f"GothamGraph(nodes={self.node_count}, edges={self.edge_count})"
        )

    # ── Internal ──────────────────────────────────────────

    def _rebuild_adj(self) -> None:
        """self._nodes / self._edges 로부터 adj 캐시 재구축. load() 에서 호출."""
        self._adj_out = defaultdict(list)
        self._adj_in = defaultdict(list)
        for edge in self._edges:
            self._adj_out[edge.source_id].append(edge)
            self._adj_in[edge.target_id].append(edge)


__all__ = ["GothamGraph"]
