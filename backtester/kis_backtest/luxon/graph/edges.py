"""
Luxon Terminal — GothamGraph 엣지 스키마 (Sprint 5 Phase 2).

5 엣지 타입 (마스터 플랜 §5.5.1 Palantir Gotham Ontology Layer):
    BELONGS_TO, CATALYST_FOR, HOLDS, CORRELATED, TRIGGERED_BY

설계 원칙:
    - frozen dataclass — 불변성 보장, pickle 안전
    - weight는 정규화된 0.0~1.0 (엣지 kind별 해석은 spec 참조)
    - meta는 엣지별 보조 정보 딕셔너리 (audit log, 출처 tag 등)
    - stdlib only
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class EdgeKind(str, Enum):
    """GothamGraph 엣지 타입 — 5종 확정 (Sprint 5, 변경 비용 큼).

    방향성 규약:
        BELONGS_TO:   symbol → sector
        CATALYST_FOR: event → symbol
        HOLDS:        person → symbol
        CORRELATED:   symbol ↔ symbol (방향 모호, 그래프상 out-edge로 저장)
        TRIGGERED_BY: event → macro_regime
    """
    BELONGS_TO = "belongs_to"       # 소속 (symbol → sector)
    CATALYST_FOR = "catalyst_for"   # 카탈리스트 (event → symbol)
    HOLDS = "holds"                 # 지분 보유 (person → symbol)
    CORRELATED = "correlated"       # 상관관계 (symbol ↔ symbol)
    TRIGGERED_BY = "triggered_by"   # 레짐 트리거 (event → macro_regime)


@dataclass(frozen=True)
class GraphEdge:
    """불변 속성 엣지. 방향성 엣지로 저장되며, 양방향 해석은 caller 책임.

    Attributes:
        source_id: 출발 노드 ID.
        target_id: 도착 노드 ID.
        kind: EdgeKind enum.
        weight: 엣지 강도 (0.0~1.0, 해석은 kind별 — spec 참조).
        timestamp: 엣지 생성 시각 (옵션, 시간축 쿼리용).
        meta: 엣지별 보조 정보 딕셔너리 (예: catalyst score, 출처 tag).
    """
    source_id: str
    target_id: str
    kind: EdgeKind
    weight: float = 1.0
    timestamp: datetime | None = None
    meta: dict[str, Any] = field(default_factory=dict)


__all__ = ["EdgeKind", "GraphEdge"]
