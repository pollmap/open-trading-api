"""
Luxon Terminal — Catalyst → GothamGraph 변환기 (Sprint 6 Phase 2).

CatalystTracker 에서 관리하는 Catalyst 를 GothamGraph EventNode + CATALYST_FOR
엣지로 변환하는 얇은 어댑터. 신규 계산 로직 0줄, 스키마 매핑만 담당.

파이프라인:
    CatalystTracker.list_all_active() → list[Catalyst]
                                   ↓
                     CatalystIngestor.ingest_all()
                                   ↓
                     GothamGraph + EventNode + SymbolNode + CATALYST_FOR 엣지

노드 ID 네임스페이스:
    Phase1 EventNode:   event:{symbol}:{action}:{ts.isoformat()}
    Catalyst EventNode: event:catalyst:{symbol}:{catalyst.id}   ← 이 파일이 생성

엣지 weight 정규화:
    CATALYST_FOR.weight = abs(catalyst.weighted_score) / 10.0   # clamp 0~1
    sign (positive/negative catalyst) 는 meta["sign"] 에 +1 / -1 로 저장
"""
from __future__ import annotations

from datetime import datetime

from kis_backtest.luxon.graph.edges import EdgeKind, GraphEdge
from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.nodes import GraphNode, NodeKind, make_node_id
from kis_backtest.portfolio.catalyst_tracker import Catalyst, CatalystTracker


class CatalystIngestor:
    """Catalyst → GothamGraph 변환.

    Args:
        graph: 타깃 GothamGraph 인스턴스. 수정만 하고 내부 state 를 감싸지 않음.
    """

    def __init__(self, graph: GothamGraph) -> None:
        self._graph = graph

    @property
    def graph(self) -> GothamGraph:
        return self._graph

    def ingest_catalyst(self, catalyst: Catalyst) -> str:
        """Catalyst → EventNode + CATALYST_FOR 엣지 추가.

        절차:
            1. SymbolNode (make_node_id(SYMBOL, catalyst.symbol)) idempotent 생성
            2. EventNode (make_node_id(EVENT, f"catalyst:{catalyst.symbol}:{catalyst.id}"))
               동일 node_id 가 이미 있으면 ValueError (중복 ingest 회피 책임은 caller).
            3. CATALYST_FOR 엣지 (event → symbol)
               weight = min(1.0, abs(catalyst.weighted_score) / 10.0)
               meta = {
                   "sign": +1 or -1,
                   "probability": catalyst.probability,
                   "impact": catalyst.impact,
                   "catalyst_type": catalyst.catalyst_type.value,
                   "source": catalyst.source,
               }

        EventNode payload 필수 필드:
            "source":         "catalyst"         # Phase1 이벤트와 구분하는 태그
            "symbol":         catalyst.symbol
            "name":           catalyst.name
            "catalyst_type":  catalyst.catalyst_type.value
            "expected_date":  catalyst.expected_date
            "probability":    catalyst.probability
            "impact":         catalyst.impact
            "description":    catalyst.description
            "source_tag":     catalyst.source   # "DART", "NEWS", ...
            "weighted_score": catalyst.weighted_score

        EventNode timestamp: datetime.now() (tz-naive, Phase1 과 동일)
        EventNode label:     f"{catalyst.catalyst_type.value.upper()} {catalyst.symbol}: {catalyst.name[:40]}"

        Returns:
            생성된 EventNode 의 node_id.
        """
        now = datetime.now()

        # 1. SymbolNode (idempotent)
        symbol_node_id = make_node_id(NodeKind.SYMBOL, catalyst.symbol)
        if not self._graph.has_node(symbol_node_id):
            symbol_node = GraphNode(
                node_id=symbol_node_id,
                kind=NodeKind.SYMBOL,
                label=catalyst.symbol,
                timestamp=now,
                payload={"symbol": catalyst.symbol},
            )
            self._graph.add_node(symbol_node)

        # 2. EventNode (always new — duplicate raises ValueError inside add_node)
        event_node_id = make_node_id(
            NodeKind.EVENT,
            f"catalyst:{catalyst.symbol}:{catalyst.id}",
        )
        event_label = (
            f"{catalyst.catalyst_type.value.upper()} "
            f"{catalyst.symbol}: {catalyst.name[:40]}"
        )
        event_payload: dict[str, object] = {
            "source": "catalyst",
            "symbol": catalyst.symbol,
            "name": catalyst.name,
            "catalyst_type": catalyst.catalyst_type.value,
            "expected_date": catalyst.expected_date,
            "probability": catalyst.probability,
            "impact": catalyst.impact,
            "description": catalyst.description,
            "source_tag": catalyst.source,
            "weighted_score": catalyst.weighted_score,
        }
        event_node = GraphNode(
            node_id=event_node_id,
            kind=NodeKind.EVENT,
            label=event_label,
            timestamp=now,
            payload=event_payload,
        )
        self._graph.add_node(event_node)

        # 3. CATALYST_FOR edge (event → symbol)
        weighted_score = catalyst.weighted_score
        edge_weight = min(1.0, abs(weighted_score) / 10.0)
        sign = 1 if weighted_score >= 0 else -1
        edge_meta: dict[str, object] = {
            "sign": sign,
            "probability": catalyst.probability,
            "impact": catalyst.impact,
            "catalyst_type": catalyst.catalyst_type.value,
            "source": catalyst.source,
        }
        catalyst_edge = GraphEdge(
            source_id=event_node_id,
            target_id=symbol_node_id,
            kind=EdgeKind.CATALYST_FOR,
            weight=edge_weight,
            timestamp=now,
            meta=edge_meta,
        )
        self._graph.add_edge(catalyst_edge)

        return event_node_id

    def ingest_all(self, tracker: CatalystTracker) -> list[str]:
        """tracker 의 모든 활성 Catalyst 를 ingest.

        tracker.list_all_active() 를 호출하고 각 catalyst 에 대해 ingest_catalyst()
        실행. 이미 동일 EventNode 가 있어 ValueError 가 나면 해당 catalyst 는
        silently skip 하고 계속 진행 (부분 ingest 허용).

        Returns:
            성공적으로 생성된 EventNode node_id 리스트 (skip 된 것 제외).
        """
        created_ids: list[str] = []
        for catalyst in tracker.list_all_active():
            try:
                node_id = self.ingest_catalyst(catalyst)
            except ValueError as exc:
                if "already exists" in str(exc):
                    continue  # 중복 노드 → 정상 skip
                raise  # 다른 ValueError (엣지 실패 등) → 전파
            created_ids.append(node_id)
        return created_ids


__all__ = ["CatalystIngestor"]
