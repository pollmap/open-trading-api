"""Sprint 7 — CorrelatedIngestor 단위 테스트.

Coverage:
    - ingest_sector: 정상 경로 (양의 상관) → CORRELATED 엣지 양방향 생성
    - 존재하지 않는 섹터 → empty
    - 섹터에 symbol 1개 → empty
    - 임계치 미달 → 엣지 없음
    - 음의 상관관계 → edge weight 는 |rho|, meta 에 원본 rho 음수
    - TickVault 비어있음 → graceful empty
    - 멱등 (같은 sector 두 번 호출 → 엣지 중복 없음)

Style:
    - 실 TickVault + 실 GothamGraph (no mocks)
    - tmp_path 로 벨리드 TickVault root 격리
    - AAA pattern, fresh fixtures per test
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from kis_backtest.luxon.graph.edges import EdgeKind, GraphEdge
from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.ingestors.correlated_ingestor import (
    CorrelatedIngestor,
)
from kis_backtest.luxon.graph.nodes import GraphNode, NodeKind, make_node_id
from kis_backtest.luxon.stream.schema import Exchange, TickPoint
from kis_backtest.luxon.stream.tick_vault import TickVault


# ── Helpers ──────────────────────────────────────────────────────────


def _add_sector_with_symbols(
    graph: GothamGraph, sector: str, symbols: list[str],
) -> None:
    """섹터 + SYMBOL + BELONGS_TO 세팅 헬퍼."""
    ts = datetime(2026, 4, 12)
    sector_id = make_node_id(NodeKind.SECTOR, sector)
    graph.add_node(
        GraphNode(sector_id, NodeKind.SECTOR, sector, ts, {"name": sector})
    )
    for sym in symbols:
        sym_id = make_node_id(NodeKind.SYMBOL, sym)
        graph.add_node(
            GraphNode(sym_id, NodeKind.SYMBOL, sym, ts, {"symbol": sym})
        )
        graph.add_edge(
            GraphEdge(sym_id, sector_id, EdgeKind.BELONGS_TO, 1.0)
        )


def _insert_daily_ticks(
    vault: TickVault,
    symbol: str,
    prices: list[float],
    end_day: date,
) -> None:
    """synthetic 일별 tick 삽입. prices[-1] 가 end_day, prices[0] 이 가장 과거."""
    for i, price in enumerate(reversed(prices)):
        day = end_day - timedelta(days=i)
        tick = TickPoint(
            timestamp=datetime.combine(day, datetime.min.time()).replace(
                hour=15, minute=30,
            ),
            symbol=symbol,
            exchange=Exchange.KIS,
            last=price,
            volume=1000.0,
        )
        vault.append(tick)
    vault.flush_all()


# ── Tests ────────────────────────────────────────────────────────────


def test_ingest_sector_creates_bidirectional_correlated_edges(
    tmp_path: Path,
) -> None:
    # Arrange — 두 종목 동일 변화 패턴 (corr ≈ 1.0)
    graph = GothamGraph()
    _add_sector_with_symbols(graph, "반도체", ["005930", "000660"])

    vault = TickVault(root_dir=tmp_path)
    end_day = date(2026, 4, 12)
    prices = [100.0, 102.0, 104.0, 103.0, 105.0, 107.0, 108.0, 110.0, 112.0, 115.0]
    _insert_daily_ticks(vault, "005930", prices, end_day)
    _insert_daily_ticks(vault, "000660", [p * 1.5 for p in prices], end_day)

    ingestor = CorrelatedIngestor(graph, vault)

    # Act
    generated = ingestor.ingest_sector(
        "반도체", end_date=end_day, lookback_days=15,
    )

    # Assert — 쌍 1개 + 양방향 엣지 2개
    assert len(generated) == 1
    correlated_edges = graph.edges_by_kind(EdgeKind.CORRELATED)
    assert len(correlated_edges) == 2

    # weight = |rho|, meta 에 rho / lookback 있음
    for edge in correlated_edges:
        assert 0.0 < edge.weight <= 1.0
        assert "rho" in edge.meta
        assert "lookback_days" in edge.meta
        assert edge.meta["lookback_days"] == 15

    # 상관계수는 실제 데이터가 대체로 같은 방향이므로 양수 기대
    _, _, rho = generated[0]
    assert rho > 0.5


def test_ingest_sector_unknown_sector_returns_empty(tmp_path: Path) -> None:
    # Arrange — 그래프에 섹터 노드 없음
    graph = GothamGraph()
    vault = TickVault(root_dir=tmp_path)
    ingestor = CorrelatedIngestor(graph, vault)

    # Act
    generated = ingestor.ingest_sector("없는섹터")

    # Assert
    assert generated == []
    assert graph.edges_by_kind(EdgeKind.CORRELATED) == []


def test_ingest_sector_single_symbol_returns_empty(tmp_path: Path) -> None:
    # Arrange
    graph = GothamGraph()
    _add_sector_with_symbols(graph, "반도체", ["005930"])
    vault = TickVault(root_dir=tmp_path)
    ingestor = CorrelatedIngestor(graph, vault)

    # Act
    generated = ingestor.ingest_sector("반도체")

    # Assert — 쌍 구성 불가
    assert generated == []
    assert graph.edges_by_kind(EdgeKind.CORRELATED) == []


def test_ingest_sector_below_threshold_no_edge(tmp_path: Path) -> None:
    # Arrange — 상관관계는 존재할 수 있으나 threshold 0.99 로 차단
    graph = GothamGraph()
    _add_sector_with_symbols(graph, "반도체", ["005930", "000660"])

    vault = TickVault(root_dir=tmp_path)
    end_day = date(2026, 4, 12)
    prices_a = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    # 의도적으로 상관 낮게: 노이즈 패턴
    prices_b = [200.0, 201.0, 199.0, 202.0, 198.0, 203.0]
    _insert_daily_ticks(vault, "005930", prices_a, end_day)
    _insert_daily_ticks(vault, "000660", prices_b, end_day)

    ingestor = CorrelatedIngestor(graph, vault)

    # Act — threshold 0.99 (거의 완벽 상관만 허용)
    generated = ingestor.ingest_sector(
        "반도체", end_date=end_day, lookback_days=10, min_abs_corr=0.99,
    )

    # Assert
    assert generated == []
    assert graph.edges_by_kind(EdgeKind.CORRELATED) == []


def test_ingest_sector_negative_correlation_preserved_in_meta(
    tmp_path: Path,
) -> None:
    # Arrange — 완전 역방향 (corr ≈ -1.0)
    graph = GothamGraph()
    _add_sector_with_symbols(graph, "반도체", ["005930", "000660"])

    vault = TickVault(root_dir=tmp_path)
    end_day = date(2026, 4, 12)
    # zig-zag pattern — 수익률이 실제로 반대 방향으로 움직여야 음의 corr
    # (단조증가/감소 시리즈는 수익률이 거의 상수라 corr → 1.0)
    prices_a = [100.0, 110.0, 100.0, 110.0, 100.0, 110.0, 100.0, 110.0]
    prices_b = [200.0, 180.0, 200.0, 180.0, 200.0, 180.0, 200.0, 180.0]
    _insert_daily_ticks(vault, "005930", prices_a, end_day)
    _insert_daily_ticks(vault, "000660", prices_b, end_day)

    ingestor = CorrelatedIngestor(graph, vault)

    # Act
    generated = ingestor.ingest_sector(
        "반도체", end_date=end_day, lookback_days=10,
    )

    # Assert — 엣지는 생성됨
    assert len(generated) == 1
    _, _, rho = generated[0]
    assert rho < 0  # 음의 상관 원본 보존

    # weight 는 |rho| → 양수
    correlated_edges = graph.edges_by_kind(EdgeKind.CORRELATED)
    for edge in correlated_edges:
        assert edge.weight > 0
        assert edge.meta["rho"] < 0


def test_ingest_sector_empty_tick_vault(tmp_path: Path) -> None:
    # Arrange — 그래프는 준비됐지만 TickVault 비어있음
    graph = GothamGraph()
    _add_sector_with_symbols(graph, "반도체", ["005930", "000660"])

    vault = TickVault(root_dir=tmp_path)
    ingestor = CorrelatedIngestor(graph, vault)

    # Act
    generated = ingestor.ingest_sector("반도체")

    # Assert — graceful empty
    assert generated == []
    assert graph.edges_by_kind(EdgeKind.CORRELATED) == []


def test_ingest_sector_idempotent_across_calls(tmp_path: Path) -> None:
    # Arrange
    graph = GothamGraph()
    _add_sector_with_symbols(graph, "반도체", ["005930", "000660"])

    vault = TickVault(root_dir=tmp_path)
    end_day = date(2026, 4, 12)
    prices = [100.0, 102.0, 104.0, 103.0, 105.0, 107.0, 108.0, 110.0]
    _insert_daily_ticks(vault, "005930", prices, end_day)
    _insert_daily_ticks(vault, "000660", [p * 1.5 for p in prices], end_day)

    ingestor = CorrelatedIngestor(graph, vault)

    # Act — 같은 sector 두 번 호출
    ingestor.ingest_sector(
        "반도체", end_date=end_day, lookback_days=10,
    )
    first_edge_count = len(graph.edges_by_kind(EdgeKind.CORRELATED))
    ingestor.ingest_sector(
        "반도체", end_date=end_day, lookback_days=10,
    )
    second_edge_count = len(graph.edges_by_kind(EdgeKind.CORRELATED))

    # Assert — 중복 엣지 생성 안 됨
    assert first_edge_count == second_edge_count
    assert first_edge_count > 0  # 실제로 생성은 됐어야 함
