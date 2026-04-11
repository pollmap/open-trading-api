"""Tests for CufaIngestor + CufaReportDigest (Sprint 6 Phase 2).

Coverage:
    - ingest_digest: creates Symbol + Sector + Person(s) + Theme(s) nodes
    - BELONGS_TO edge (symbol → sector) exactly once per unique pair
    - HOLDS edges (person → symbol) one per distinct person
    - CEO-first ordering in returned person_ids
    - Empty sector branch: no SectorNode, no BELONGS_TO, sector_id == None
    - None ceo_name branch: only key_persons become PersonNodes
    - Dedup: ceo_name duplicated in key_persons collapses to single PersonNode,
      CEO role wins because ceo is processed first (idempotent = second write skipped)
    - Empty themes branch: no ThemeNode, theme_ids == []
    - Idempotency across successive ingest_digest calls on overlapping data
      (same symbol / same sector / same person → no duplicate nodes or edges)

Style:
    - stdlib only, real GothamGraph + CufaIngestor instances (no mocks)
    - AAA pattern, one behavior per test
    - Fresh GothamGraph + CufaIngestor per test
"""
from __future__ import annotations

from kis_backtest.luxon.graph.edges import EdgeKind
from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.ingestors.cufa_ingestor import (
    CufaIngestor,
    CufaReportDigest,
)
from kis_backtest.luxon.graph.nodes import NodeKind, make_node_id


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_digest(
    symbol: str = "005930",
    ceo_name: str | None = "이재용",
    key_persons: list[str] | None = None,
    sector: str = "반도체",
    themes: list[str] | None = None,
) -> CufaReportDigest:
    """Build a synthetic CufaReportDigest (frozen dataclass).

    Lists default to fresh empty lists so individual tests never share
    mutable default state.
    """
    return CufaReportDigest(
        symbol=symbol,
        ceo_name=ceo_name,
        key_persons=list(key_persons) if key_persons is not None else [],
        sector=sector,
        themes=list(themes) if themes is not None else [],
    )


# ── Tests ────────────────────────────────────────────────────────────────


def test_ingest_digest_creates_all_nodes_and_edges() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = CufaIngestor(graph)
    digest = _make_digest(
        symbol="005930",
        ceo_name="이재용",
        key_persons=["한종희", "노태문"],
        sector="반도체",
        themes=["AI", "HBM"],
    )

    # Act
    result = ingestor.ingest_digest(digest)

    # Assert — return schema
    assert result["symbol_id"] == make_node_id(NodeKind.SYMBOL, "005930")
    assert result["symbol_id"] == "symbol:005930"
    assert result["sector_id"] == make_node_id(NodeKind.SECTOR, "반도체")
    assert result["sector_id"] == "sector:반도체"

    person_ids = result["person_ids"]
    assert isinstance(person_ids, list)
    assert len(person_ids) == 3
    # CEO must appear first per dedup-order rule (ceo processed before key_persons)
    assert person_ids[0] == make_node_id(NodeKind.PERSON, "이재용")
    assert person_ids[0] == "person:이재용"
    assert set(person_ids) == {
        "person:이재용",
        "person:한종희",
        "person:노태문",
    }

    theme_ids = result["theme_ids"]
    assert isinstance(theme_ids, list)
    assert len(theme_ids) == 2
    assert set(theme_ids) == {
        make_node_id(NodeKind.THEME, "AI"),
        make_node_id(NodeKind.THEME, "HBM"),
    }

    # Assert — graph state
    symbol_nodes = graph.nodes_by_kind(NodeKind.SYMBOL)
    sector_nodes = graph.nodes_by_kind(NodeKind.SECTOR)
    person_nodes = graph.nodes_by_kind(NodeKind.PERSON)
    theme_nodes = graph.nodes_by_kind(NodeKind.THEME)

    assert len(symbol_nodes) == 1
    assert len(sector_nodes) == 1
    assert len(person_nodes) == 3
    assert len(theme_nodes) == 2
    # Total: 1 + 1 + 3 + 2 == 7
    assert graph.node_count == 7

    # SymbolNode payload sanity
    symbol_node = graph.get_node(result["symbol_id"])
    assert symbol_node is not None
    assert symbol_node.kind is NodeKind.SYMBOL
    assert symbol_node.payload.get("symbol") == "005930"

    # SectorNode payload sanity
    sector_node = graph.get_node(result["sector_id"])
    assert sector_node is not None
    assert sector_node.kind is NodeKind.SECTOR
    assert sector_node.payload.get("name") == "반도체"
    assert sector_node.payload.get("krx_code") is None

    # Edges
    belongs_edges = graph.edges_by_kind(EdgeKind.BELONGS_TO)
    assert len(belongs_edges) == 1
    belongs = belongs_edges[0]
    assert belongs.source_id == result["symbol_id"]
    assert belongs.target_id == result["sector_id"]
    assert belongs.weight == 1.0

    holds_edges = graph.edges_by_kind(EdgeKind.HOLDS)
    assert len(holds_edges) == 3
    # Every HOLDS edge must target the same symbol
    assert {e.target_id for e in holds_edges} == {result["symbol_id"]}
    # Every person_id must own exactly one HOLDS edge
    assert {e.source_id for e in holds_edges} == set(person_ids)
    # Weight sanity
    assert all(e.weight == 1.0 for e in holds_edges)

    # ThemeNodes have NO edges in Sprint 6
    theme_node_ids = {n.node_id for n in theme_nodes}
    for edge in graph.edges_by_kind(EdgeKind.BELONGS_TO):
        assert edge.source_id not in theme_node_ids
        assert edge.target_id not in theme_node_ids
    for edge in graph.edges_by_kind(EdgeKind.HOLDS):
        assert edge.source_id not in theme_node_ids
        assert edge.target_id not in theme_node_ids


def test_ingest_digest_empty_sector_skips_sector_and_belongs_to() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = CufaIngestor(graph)
    digest = _make_digest(
        symbol="005930",
        ceo_name="이재용",
        key_persons=[],
        sector="",
        themes=[],
    )

    # Act
    result = ingestor.ingest_digest(digest)

    # Assert — return schema
    assert result["symbol_id"] == "symbol:005930"
    assert result["sector_id"] is None
    assert result["person_ids"] == ["person:이재용"]
    assert result["theme_ids"] == []

    # Assert — graph state
    assert graph.nodes_by_kind(NodeKind.SECTOR) == []
    assert graph.edges_by_kind(EdgeKind.BELONGS_TO) == []

    # Symbol still exists
    symbol_nodes = graph.nodes_by_kind(NodeKind.SYMBOL)
    assert len(symbol_nodes) == 1
    assert symbol_nodes[0].node_id == "symbol:005930"

    # CEO PersonNode still exists
    person_nodes = graph.nodes_by_kind(NodeKind.PERSON)
    assert len(person_nodes) == 1
    ceo_node = person_nodes[0]
    assert ceo_node.node_id == "person:이재용"
    assert ceo_node.payload.get("role") == "CEO"

    # HOLDS edge (ceo → symbol) still created
    holds_edges = graph.edges_by_kind(EdgeKind.HOLDS)
    assert len(holds_edges) == 1
    assert holds_edges[0].source_id == "person:이재용"
    assert holds_edges[0].target_id == "symbol:005930"


def test_ingest_digest_none_ceo_allows_key_persons() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = CufaIngestor(graph)
    digest = _make_digest(
        symbol="005930",
        ceo_name=None,
        key_persons=["Analyst A", "Analyst B"],
        sector="반도체",
        themes=[],
    )

    # Act
    result = ingestor.ingest_digest(digest)

    # Assert — return schema
    assert len(result["person_ids"]) == 2
    assert set(result["person_ids"]) == {
        make_node_id(NodeKind.PERSON, "Analyst A"),
        make_node_id(NodeKind.PERSON, "Analyst B"),
    }

    # Assert — graph state: no CEO role anywhere
    person_nodes = graph.nodes_by_kind(NodeKind.PERSON)
    assert len(person_nodes) == 2
    for node in person_nodes:
        assert node.payload.get("role") != "CEO"
        assert node.payload.get("role") == "key_person"

    # Two HOLDS edges, both → symbol
    holds_edges = graph.edges_by_kind(EdgeKind.HOLDS)
    assert len(holds_edges) == 2
    assert {e.target_id for e in holds_edges} == {"symbol:005930"}
    assert {e.source_id for e in holds_edges} == {
        "person:Analyst A",
        "person:Analyst B",
    }


def test_ingest_digest_ceo_duplicate_in_key_persons_dedup() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = CufaIngestor(graph)
    digest = _make_digest(
        symbol="005930",
        ceo_name="이재용",
        key_persons=["이재용", "한종희"],
        sector="반도체",
        themes=[],
    )

    # Act
    result = ingestor.ingest_digest(digest)

    # Assert — return schema: CEO first, then the only non-dup key_person
    assert len(result["person_ids"]) == 2
    assert result["person_ids"][0] == "person:이재용"
    assert set(result["person_ids"]) == {"person:이재용", "person:한종희"}

    # Assert — graph state: exactly 2 PersonNodes (not 3)
    person_nodes = graph.nodes_by_kind(NodeKind.PERSON)
    assert len(person_nodes) == 2
    person_ids = {n.node_id for n in person_nodes}
    assert person_ids == {"person:이재용", "person:한종희"}

    # The 이재용 PersonNode must retain role == "CEO" (first-write-wins via
    # idempotent add_node — the second attempt with role="key_person" is skipped)
    ceo_node = graph.get_node("person:이재용")
    assert ceo_node is not None
    assert ceo_node.payload.get("role") == "CEO"

    # The 한종희 PersonNode should be role="key_person"
    key_person_node = graph.get_node("person:한종희")
    assert key_person_node is not None
    assert key_person_node.payload.get("role") == "key_person"

    # Exactly 2 HOLDS edges — no duplicate for the deduped CEO
    holds_edges = graph.edges_by_kind(EdgeKind.HOLDS)
    assert len(holds_edges) == 2
    assert {e.source_id for e in holds_edges} == {
        "person:이재용",
        "person:한종희",
    }
    assert {e.target_id for e in holds_edges} == {"symbol:005930"}


def test_ingest_digest_empty_themes_returns_empty_theme_ids() -> None:
    # Arrange
    graph = GothamGraph()
    ingestor = CufaIngestor(graph)
    digest = _make_digest(
        symbol="005930",
        ceo_name="이재용",
        key_persons=["한종희"],
        sector="반도체",
        themes=[],
    )

    # Act
    result = ingestor.ingest_digest(digest)

    # Assert
    assert result["theme_ids"] == []
    assert graph.nodes_by_kind(NodeKind.THEME) == []

    # Sanity — the rest of the graph is still populated
    assert len(graph.nodes_by_kind(NodeKind.SYMBOL)) == 1
    assert len(graph.nodes_by_kind(NodeKind.SECTOR)) == 1
    assert len(graph.nodes_by_kind(NodeKind.PERSON)) == 2


def test_ingest_digest_idempotent_across_calls() -> None:
    # Arrange — two digests for the same symbol with overlapping sector/person
    graph = GothamGraph()
    ingestor = CufaIngestor(graph)
    digest_a = _make_digest(
        symbol="005930",
        ceo_name="이재용",
        key_persons=["한종희"],
        sector="반도체",
        themes=["AI"],
    )
    digest_b = _make_digest(
        symbol="005930",
        ceo_name="이재용",
        key_persons=["노태문"],
        sector="반도체",
        themes=["HBM"],
    )

    # Act
    result_a = ingestor.ingest_digest(digest_a)
    result_b = ingestor.ingest_digest(digest_b)

    # Assert — same symbol_id / sector_id returned on both calls (idempotent)
    assert result_a["symbol_id"] == result_b["symbol_id"] == "symbol:005930"
    assert result_a["sector_id"] == result_b["sector_id"] == "sector:반도체"

    # Assert — SymbolNode NOT duplicated
    symbol_nodes = graph.nodes_by_kind(NodeKind.SYMBOL)
    assert len(symbol_nodes) == 1

    # Assert — SectorNode NOT duplicated
    sector_nodes = graph.nodes_by_kind(NodeKind.SECTOR)
    assert len(sector_nodes) == 1

    # Assert — 3 distinct PersonNodes (이재용 once + 한종희 + 노태문)
    person_nodes = graph.nodes_by_kind(NodeKind.PERSON)
    assert len(person_nodes) == 3
    assert {n.node_id for n in person_nodes} == {
        "person:이재용",
        "person:한종희",
        "person:노태문",
    }

    # Assert — 2 distinct ThemeNodes (AI + HBM)
    theme_nodes = graph.nodes_by_kind(NodeKind.THEME)
    assert len(theme_nodes) == 2
    assert {n.node_id for n in theme_nodes} == {
        "theme:AI",
        "theme:HBM",
    }

    # Assert — HOLDS edges deduped: 이재용 appears in BOTH digests but the
    # ingestor guards via graph.has_edge(person, symbol, HOLDS) so only one
    # HOLDS edge per person exists. Total HOLDS == 3 (not 4).
    holds_edges = graph.edges_by_kind(EdgeKind.HOLDS)
    assert len(holds_edges) == 3
    assert {e.source_id for e in holds_edges} == {
        "person:이재용",
        "person:한종희",
        "person:노태문",
    }
    assert {e.target_id for e in holds_edges} == {"symbol:005930"}

    # Assert — BELONGS_TO edge deduped: same symbol→sector pair across both
    # calls must NOT create a duplicate. Total BELONGS_TO == 1.
    belongs_edges = graph.edges_by_kind(EdgeKind.BELONGS_TO)
    assert len(belongs_edges) == 1
    assert belongs_edges[0].source_id == "symbol:005930"
    assert belongs_edges[0].target_id == "sector:반도체"

    # Per-call returns — person_ids still reflect each call's own insertion
    # order with CEO first
    assert result_a["person_ids"][0] == "person:이재용"
    assert result_b["person_ids"][0] == "person:이재용"
    assert set(result_a["person_ids"]) == {"person:이재용", "person:한종희"}
    assert set(result_b["person_ids"]) == {"person:이재용", "person:노태문"}
    assert result_a["theme_ids"] == ["theme:AI"]
    assert result_b["theme_ids"] == ["theme:HBM"]
