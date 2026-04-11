"""
Luxon Terminal — TickVault 기반 종목 상관관계 ingestor (Sprint 7 Phase 2).

pandas.DataFrame.corr() 재사용. 직접 pearson 구현 0줄.
CatalystIngestor/CufaIngestor 와 동일한 adapter 패턴 (내부 state 감싸지 않음).

파이프라인:
    GothamGraph 섹터 노드 → BELONGS_TO in-edge → SYMBOL 노드 리스트
                                ↓
                  TickVault.load_day × lookback_days
                                ↓
                    각 일자 마지막 tick.last = 일별 종가
                                ↓
                pandas DataFrame (index=date, columns=symbol)
                                ↓
                    .pct_change() → 일별 수익률
                                ↓
                        .corr() → pearson 상관
                                ↓
              |rho| >= min_abs_corr 쌍 → CORRELATED 엣지 (양방향)

엣지 규약 (spec 4.1):
    CORRELATED 는 양방향. 내부적으로 두 directed edge 로 저장.
    weight = |rho| (0~1)
    meta = {"rho": float, "lookback_days": int}
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from kis_backtest.luxon.graph.edges import EdgeKind, GraphEdge
from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.nodes import NodeKind, make_node_id
from kis_backtest.luxon.stream.schema import Exchange
from kis_backtest.luxon.stream.tick_vault import TickVault


class CorrelatedIngestor:
    """TickVault 수익률 → CORRELATED 엣지 자동 생성.

    Args:
        graph: 타깃 GothamGraph 인스턴스.
        tick_vault: 가격 데이터 소스 TickVault.
        exchange: 기본 거래소 (KIS 한국 시장).
    """

    def __init__(
        self,
        graph: GothamGraph,
        tick_vault: TickVault,
        exchange: Exchange = Exchange.KIS,
    ) -> None:
        self._graph = graph
        self._tick_vault = tick_vault
        self._exchange = exchange

    @property
    def graph(self) -> GothamGraph:
        return self._graph

    def ingest_sector(
        self,
        sector_name: str,
        end_date: date | None = None,
        lookback_days: int = 30,
        min_abs_corr: float = 0.3,
    ) -> list[tuple[str, str, float]]:
        """섹터에 속한 symbol 쌍의 pearson 상관계수 → CORRELATED 엣지.

        Args:
            sector_name: 섹터 이름 (예: "반도체"). 그래프에 SectorNode 가 없으면
                빈 리스트 반환.
            end_date: 상관 계산 종료일 (포함). None 이면 date.today().
            lookback_days: 과거 며칠 데이터를 사용할지. 기본 30.
            min_abs_corr: 이 임계치 미만의 |rho| 는 엣지 생성 안 함. 기본 0.3.

        Returns:
            생성된 (symbol_a_id, symbol_b_id, rho) 튜플 리스트. rho 는 원본
            (음수 포함). 엣지가 이미 있으면 해당 쌍은 리스트에 포함되지만
            edge 재추가 안 함 (idempotent).
        """
        end = end_date or date.today()

        sector_id = make_node_id(NodeKind.SECTOR, sector_name)
        if not self._graph.has_node(sector_id):
            return []

        # 섹터에 속한 SYMBOL 노드 (in-edge: symbol → sector)
        symbol_nodes = self._graph.neighbors(
            sector_id, EdgeKind.BELONGS_TO, direction="in"
        )
        if len(symbol_nodes) < 2:
            return []

        # lookback_days 범위 날짜 리스트 (과거 → 현재 순)
        dates = [end - timedelta(days=i) for i in range(lookback_days)]
        dates.sort()

        # 각 symbol 의 일별 종가 수집 (일 없는 날은 dict 에 누락 → NaN)
        price_data: dict[str, dict[date, float]] = {}
        for node in symbol_nodes:
            symbol_code = node.payload.get("symbol") or node.label
            daily_close: dict[date, float] = {}
            for day in dates:
                ticks = self._tick_vault.load_day(
                    self._exchange, symbol_code, day,
                )
                if ticks:
                    # 해당 날 마지막 tick 의 last price = 일별 종가
                    daily_close[day] = ticks[-1].last
            if len(daily_close) >= 2:
                price_data[node.node_id] = daily_close

        if len(price_data) < 2:
            return []

        # pandas DataFrame (columns=node_id, index=date)
        df = pd.DataFrame(price_data).sort_index()

        # 수익률 및 상관행렬
        returns = df.pct_change().dropna(how="all")
        if len(returns) < 2:
            return []

        corr_matrix = returns.corr()

        # 상삼각 (i<j) 만 순회해 중복 제거, |rho| >= threshold 필터
        generated: list[tuple[str, str, float]] = []
        node_ids = list(corr_matrix.columns)
        ts = datetime.now()

        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                a, b = node_ids[i], node_ids[j]
                rho = corr_matrix.iat[i, j]
                if pd.isna(rho):
                    continue
                if abs(rho) < min_abs_corr:
                    continue

                # CORRELATED 는 양방향 (spec 4.1) — 두 directed edge 저장
                weight = float(abs(rho))
                meta = {
                    "rho": float(rho),
                    "lookback_days": lookback_days,
                }
                if not self._graph.has_edge(a, b, EdgeKind.CORRELATED):
                    self._graph.add_edge(GraphEdge(
                        source_id=a,
                        target_id=b,
                        kind=EdgeKind.CORRELATED,
                        weight=weight,
                        timestamp=ts,
                        meta=meta,
                    ))
                if not self._graph.has_edge(b, a, EdgeKind.CORRELATED):
                    self._graph.add_edge(GraphEdge(
                        source_id=b,
                        target_id=a,
                        kind=EdgeKind.CORRELATED,
                        weight=weight,
                        timestamp=ts,
                        meta=meta,
                    ))
                generated.append((a, b, float(rho)))

        return generated


__all__ = ["CorrelatedIngestor"]
