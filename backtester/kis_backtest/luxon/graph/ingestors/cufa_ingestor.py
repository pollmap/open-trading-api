"""
Luxon Terminal — CUFA 보고서 → GothamGraph 변환기 (Sprint 6 Phase 2).

CUFA 기업분석 보고서 digest(pre-parsed dataclass)를 GothamGraph 에 쏟아붓는
얇은 어댑터. Sprint 6 는 파서를 포함하지 않음 — 보고서 파싱은 Sprint 7+.

파이프라인:
    [Sprint 7+ parser] → CufaReportDigest
                      ↓
             CufaIngestor.ingest_digest()
                      ↓
    GothamGraph + SymbolNode + SectorNode + PersonNode(s) + ThemeNode(s)
                +  HOLDS (person → symbol) + BELONGS_TO (symbol → sector)

Sprint 6 엣지 스코프:
    - HOLDS:      모든 person → symbol
    - BELONGS_TO: symbol → sector (1개)
    - ThemeNode 는 노드만 (엣지 없음) — Sprint 7 에서 CORRELATED or 별도 엣지 타입.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from kis_backtest.luxon.graph.edges import EdgeKind, GraphEdge
from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.nodes import GraphNode, NodeKind, make_node_id


@dataclass(frozen=True)
class CufaReportDigest:
    """CUFA 보고서 pre-parsed 추출체 (Sprint 6 MVP).

    Attributes:
        symbol: 종목 코드 (예: "005930").
        ceo_name: 대표 인물 이름. None 허용.
        key_persons: 기타 주요 인물 목록 (경영진/투자자/애널리스트 등).
            ceo_name 과 중복 불필요 — 호출자가 이미 제외해서 넘긴다고 가정.
        sector: KRX 섹터/산업 이름 (예: "반도체"). 반드시 1개.
        themes: 테마 이름 리스트 (예: ["AI", "HBM"]). 빈 리스트 허용.
    """
    symbol: str
    ceo_name: str | None
    key_persons: list[str] = field(default_factory=list)
    sector: str = ""
    themes: list[str] = field(default_factory=list)


class CufaIngestor:
    """CUFA digest → GothamGraph 변환.

    Args:
        graph: 타깃 GothamGraph 인스턴스. 수정만 하고 내부 state 를 감싸지 않음.
    """

    def __init__(self, graph: GothamGraph) -> None:
        self._graph = graph

    @property
    def graph(self) -> GothamGraph:
        return self._graph

    def ingest_digest(self, digest: CufaReportDigest) -> dict[str, object]:
        """Digest → SymbolNode + SectorNode + PersonNode(s) + ThemeNode(s) + 엣지.

        절차:
            1. SymbolNode (make_node_id(SYMBOL, digest.symbol)) idempotent 생성.
               payload: {"symbol": digest.symbol}

            2. SectorNode (make_node_id(SECTOR, digest.sector)) idempotent 생성.
               digest.sector 가 빈 문자열이면 스킵 (SectorNode + BELONGS_TO 모두 생성 안 함).
               payload: {"name": digest.sector, "krx_code": None}

            3. BELONGS_TO 엣지: symbol → sector (digest.sector 가 있을 때만). weight=1.0.

            4. PersonNode 리스트:
               - ceo_name 가 주어졌으면 PersonNode 생성 + HOLDS 엣지.
                 payload: {"name": ceo_name, "role": "CEO", "organization": ""}
               - key_persons 각각도 PersonNode + HOLDS 엣지.
                 payload: {"name": person_name, "role": "key_person", "organization": ""}
               모든 PersonNode 는 idempotent (make_node_id(PERSON, name) 으로 체크).
               HOLDS 엣지는 동일 (person, symbol) 쌍에 대해 중복 생성 금지 —
               graph.has_edge(person_id, symbol_id, EdgeKind.HOLDS) 로 확인 후 스킵.
               HOLDS weight: 1.0 (Sprint 6 는 보유 비중 정보 없음)

            5. ThemeNode 리스트 (digest.themes 각각):
               make_node_id(THEME, theme) idempotent 생성. 엣지 없음.
               payload: {"name": theme, "description": ""}

        Timestamp: 함수 최상단에서 datetime.now() 한 번 호출 후 모든 node/edge 에 재사용.

        Returns:
            {
                "symbol_id":  str,                 # 항상 존재
                "sector_id":  str | None,          # 섹터가 비어있으면 None
                "person_ids": list[str],           # ceo + key_persons (중복 제거된 최종 PersonNode ID들)
                "theme_ids":  list[str],           # 생성된 ThemeNode ID들
            }
        """
        now = datetime.now()

        # 1. SymbolNode (idempotent)
        symbol_node_id = make_node_id(NodeKind.SYMBOL, digest.symbol)
        if not self._graph.has_node(symbol_node_id):
            symbol_node = GraphNode(
                node_id=symbol_node_id,
                kind=NodeKind.SYMBOL,
                label=digest.symbol,
                timestamp=now,
                payload={"symbol": digest.symbol},
            )
            self._graph.add_node(symbol_node)

        # 2. SectorNode + 3. BELONGS_TO 엣지 (digest.sector 가 있을 때만)
        sector_node_id: str | None = None
        if digest.sector:
            sector_node_id = make_node_id(NodeKind.SECTOR, digest.sector)
            if not self._graph.has_node(sector_node_id):
                sector_node = GraphNode(
                    node_id=sector_node_id,
                    kind=NodeKind.SECTOR,
                    label=digest.sector,
                    timestamp=now,
                    payload={"name": digest.sector, "krx_code": None},
                )
                self._graph.add_node(sector_node)
            if not self._graph.has_edge(
                symbol_node_id, sector_node_id, EdgeKind.BELONGS_TO
            ):
                belongs_edge = GraphEdge(
                    source_id=symbol_node_id,
                    target_id=sector_node_id,
                    kind=EdgeKind.BELONGS_TO,
                    weight=1.0,
                    timestamp=now,
                )
                self._graph.add_edge(belongs_edge)

        # 4. PersonNode 리스트 (ceo_name + key_persons, dedup 순서 보존)
        person_ids: list[str] = []
        seen_person_ids: set[str] = set()

        def _ingest_person(name: str, role: str) -> None:
            person_node_id = make_node_id(NodeKind.PERSON, name)
            if not self._graph.has_node(person_node_id):
                person_node = GraphNode(
                    node_id=person_node_id,
                    kind=NodeKind.PERSON,
                    label=name,
                    timestamp=now,
                    payload={
                        "name": name,
                        "role": role,
                        "organization": "",
                    },
                )
                self._graph.add_node(person_node)
            if not self._graph.has_edge(
                person_node_id, symbol_node_id, EdgeKind.HOLDS
            ):
                holds_edge = GraphEdge(
                    source_id=person_node_id,
                    target_id=symbol_node_id,
                    kind=EdgeKind.HOLDS,
                    weight=1.0,
                    timestamp=now,
                )
                self._graph.add_edge(holds_edge)
            if person_node_id not in seen_person_ids:
                seen_person_ids.add(person_node_id)
                person_ids.append(person_node_id)

        if digest.ceo_name is not None:
            _ingest_person(digest.ceo_name, "CEO")
        for key_person in digest.key_persons:
            _ingest_person(key_person, "key_person")

        # 5. ThemeNode 리스트 (엣지 없음, idempotent)
        theme_ids: list[str] = []
        seen_theme_ids: set[str] = set()
        for theme in digest.themes:
            theme_node_id = make_node_id(NodeKind.THEME, theme)
            if not self._graph.has_node(theme_node_id):
                theme_node = GraphNode(
                    node_id=theme_node_id,
                    kind=NodeKind.THEME,
                    label=theme,
                    timestamp=now,
                    payload={"name": theme, "description": ""},
                )
                self._graph.add_node(theme_node)
            if theme_node_id not in seen_theme_ids:
                seen_theme_ids.add(theme_node_id)
                theme_ids.append(theme_node_id)

        return {
            "symbol_id": symbol_node_id,
            "sector_id": sector_node_id,
            "person_ids": person_ids,
            "theme_ids": theme_ids,
        }


__all__ = ["CufaReportDigest", "CufaIngestor"]
