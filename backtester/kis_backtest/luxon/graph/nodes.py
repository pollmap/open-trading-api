"""
Luxon Terminal — GothamGraph 노드 스키마 (Sprint 5 Phase 2).

6 노드 타입 (마스터 플랜 §5.5.1 Palantir Gotham Ontology Layer):
    SYMBOL, SECTOR, EVENT, THEME, MACRO_REGIME, PERSON

설계 원칙:
    - frozen dataclass — 불변성 보장, pickle 안전
    - node_id 표준 포맷: f"{kind.value}:{key}" (make_node_id 헬퍼 강제)
    - payload는 kind-specific dict — 타입 안정성보다 스키마 진화 유연성 우선
    - stdlib only (networkx/neo4j 금지)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class NodeKind(str, Enum):
    """GothamGraph 노드 타입 카탈로그 — 6종 확정 (Sprint 5, 변경 비용 큼)."""
    SYMBOL = "symbol"           # 종목 (005930, NVDA, BTC-USD)
    SECTOR = "sector"           # 섹터 (반도체, 이차전지)
    EVENT = "event"             # 이벤트 (공시, 결정, 카탈리스트)
    THEME = "theme"             # 테마 (AI, 우주, 탄소중립)
    MACRO_REGIME = "macro_regime"  # 매크로 레짐 (EXPANSION, RECOVERY, ...)
    PERSON = "person"           # 인물 (대표, 애널리스트)


@dataclass(frozen=True)
class GraphNode:
    """불변 속성 노드. pickle-safe, hash-safe (node_id 기반).

    Attributes:
        node_id: 고유 ID. 권장 포맷 f"{kind}:{key}" — make_node_id() 헬퍼 사용.
        kind: NodeKind enum.
        label: 표시용 이름 (한국어/영어 무관).
        timestamp: 노드 생성/업데이트 시각.
        payload: kind-specific 딕셔너리. 스키마는 docs/luxon/gotham_graph_spec.md 참조.
    """
    node_id: str
    kind: NodeKind
    label: str
    timestamp: datetime
    payload: dict[str, Any] = field(default_factory=dict)


def make_node_id(kind: NodeKind, key: str) -> str:
    """표준 node_id 포맷터.

    Args:
        kind: NodeKind enum.
        key: 노드 고유 key (종목코드, 섹터명, 이벤트 해시 등).

    Returns:
        f"{kind.value}:{key}"

    Example:
        >>> make_node_id(NodeKind.SYMBOL, "005930")
        'symbol:005930'
    """
    return f"{kind.value}:{key}"


__all__ = ["NodeKind", "GraphNode", "make_node_id"]
