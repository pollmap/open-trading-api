"""
Luxon Terminal — Phase1 → GothamGraph 변환기 (Sprint 5 Phase 2).

Phase 1 산출물(Phase1CheckpointResult, OrderProposal)을 GothamGraph 노드/엣지로
변환하는 얇은 어댑터. 새로운 계산 로직 0줄, 스키마 매핑만 담당.

파이프라인:
    Phase1Pipeline.checkpoint() → Phase1CheckpointResult
                               ↓
                    Phase1Ingestor.ingest_checkpoint()
                               ↓
                    GothamGraph + MacroRegimeNode

    ConvictionBridge.propose() → OrderProposal
                              ↓
                    Phase1Ingestor.ingest_proposal()
                              ↓
                    GothamGraph + SymbolNode + EventNode + 엣지 (+ 옵션 SectorNode)
"""
from __future__ import annotations

from datetime import datetime

from kis_backtest.luxon.graph.edges import EdgeKind, GraphEdge
from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.nodes import GraphNode, NodeKind, make_node_id
from kis_backtest.luxon.integration.conviction_bridge import OrderProposal
from kis_backtest.luxon.integration.phase1_pipeline import Phase1CheckpointResult


class Phase1Ingestor:
    """Phase1 artifacts → GothamGraph 변환.

    Args:
        graph: 타깃 GothamGraph 인스턴스. 수정만 하고 내부 state 를 감싸지 않음.
    """

    def __init__(self, graph: GothamGraph) -> None:
        self._graph = graph

    @property
    def graph(self) -> GothamGraph:
        return self._graph

    def ingest_checkpoint(
        self,
        checkpoint: Phase1CheckpointResult,
    ) -> str | None:
        """Phase1CheckpointResult → MacroRegimeNode 추가.

        regime_result=None 이면 노드 추가 없이 None 반환 (silent skip).
        이미 동일 node_id 가 있으면 ValueError — caller 가 중복 ingest 회피.

        node_id 포맷:
            make_node_id(NodeKind.MACRO_REGIME, f"{regime.value}:{ts.isoformat()}")

        payload:
            regime, confidence, score, positive/negative/neutral_signals,
            allocation, macro_indicator_count, fred_series_loaded

        Returns:
            생성된 MacroRegimeNode 의 node_id. regime_result=None 이면 None.
        """
        regime_result = checkpoint.regime_result
        if regime_result is None:
            return None

        ts = checkpoint.timestamp
        node_id = make_node_id(
            NodeKind.MACRO_REGIME,
            f"{regime_result.regime.value}:{ts.isoformat()}",
        )
        label = f"{regime_result.regime.value} @ {ts.isoformat()}"
        payload: dict[str, object] = {
            "regime": regime_result.regime.value,
            "confidence": regime_result.confidence,
            "score": regime_result.score,
            "positive_signals": regime_result.positive_signals,
            "negative_signals": regime_result.negative_signals,
            "neutral_signals": regime_result.neutral_signals,
            "allocation": dict(regime_result.allocation),
            "macro_indicator_count": checkpoint.macro_indicator_count,
            "fred_series_loaded": checkpoint.fred_series_loaded,
        }
        node = GraphNode(
            node_id=node_id,
            kind=NodeKind.MACRO_REGIME,
            label=label,
            timestamp=ts,
            payload=payload,
        )
        self._graph.add_node(node)
        return node_id

    def ingest_proposal(
        self,
        proposal: OrderProposal,
        regime_node_id: str | None = None,
        sector: str | None = None,
    ) -> str:
        """OrderProposal → SymbolNode + EventNode + 엣지들 추가.

        절차는 모듈 docstring 참고.

        Args:
            proposal: ConvictionBridge.propose() 결과.
            regime_node_id: 선택. 연결할 MacroRegimeNode.
            sector: 선택. 섹터 이름 (KRX 분류).

        Returns:
            생성된 EventNode 의 node_id.

        Raises:
            ValueError: regime_node_id 가 주어졌는데 그래프에 존재하지 않음.
        """
        if regime_node_id is not None and not self._graph.has_node(regime_node_id):
            raise ValueError(
                f"regime_node_id not found in graph: {regime_node_id}"
            )

        now = datetime.now()

        # 1. SymbolNode (idempotent)
        symbol_node_id = make_node_id(NodeKind.SYMBOL, proposal.symbol)
        if not self._graph.has_node(symbol_node_id):
            symbol_node = GraphNode(
                node_id=symbol_node_id,
                kind=NodeKind.SYMBOL,
                label=proposal.symbol,
                timestamp=now,
                payload={"symbol": proposal.symbol},
            )
            self._graph.add_node(symbol_node)

        # 2. EventNode (always new)
        event_node_id = make_node_id(
            NodeKind.EVENT,
            f"{proposal.symbol}:{proposal.action}:{now.isoformat()}",
        )
        event_label = f"{proposal.action} {proposal.symbol}"
        event_payload: dict[str, object] = {
            "action": proposal.action,
            "symbol": proposal.symbol,
            "position_pct": proposal.position_pct,
            "conviction": proposal.conviction,
            "reason": proposal.reason,
            "passed_gates": list(proposal.passed_gates),
        }
        event_node = GraphNode(
            node_id=event_node_id,
            kind=NodeKind.EVENT,
            label=event_label,
            timestamp=now,
            payload=event_payload,
        )
        self._graph.add_node(event_node)

        # 3. Edge: EventNode → SymbolNode (CATALYST_FOR)
        catalyst_weight = max(proposal.position_pct, 0.01)
        catalyst_edge = GraphEdge(
            source_id=event_node_id,
            target_id=symbol_node_id,
            kind=EdgeKind.CATALYST_FOR,
            weight=catalyst_weight,
            timestamp=now,
        )
        self._graph.add_edge(catalyst_edge)

        # 4. Edge: EventNode → MacroRegimeNode (TRIGGERED_BY), optional
        if regime_node_id is not None:
            triggered_edge = GraphEdge(
                source_id=event_node_id,
                target_id=regime_node_id,
                kind=EdgeKind.TRIGGERED_BY,
                weight=1.0,
                timestamp=now,
            )
            self._graph.add_edge(triggered_edge)

        # 5. SectorNode + BELONGS_TO edge, optional
        if sector is not None:
            sector_node_id = make_node_id(NodeKind.SECTOR, sector)
            if not self._graph.has_node(sector_node_id):
                sector_node = GraphNode(
                    node_id=sector_node_id,
                    kind=NodeKind.SECTOR,
                    label=sector,
                    timestamp=now,
                    payload={"name": sector},
                )
                self._graph.add_node(sector_node)
            if not self._graph.has_edge(
                symbol_node_id, sector_node_id, EdgeKind.BELONGS_TO,
            ):
                belongs_edge = GraphEdge(
                    source_id=symbol_node_id,
                    target_id=sector_node_id,
                    kind=EdgeKind.BELONGS_TO,
                    weight=1.0,
                    timestamp=now,
                )
                self._graph.add_edge(belongs_edge)

        return event_node_id
