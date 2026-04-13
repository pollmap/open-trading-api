"""
TASignalIngestor 유닛 테스트.

MCP 없이 FakeMCPTA 로 RSI/MACD/Bollinger 신호 → GothamGraph + CatalystTracker 주입 검증.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.ingestors.ta_signal_ingestor import (
    TASignal,
    TASignalIngestor,
)
from kis_backtest.luxon.graph.nodes import NodeKind
from kis_backtest.portfolio.catalyst_tracker import CatalystTracker, CatalystType


# ── Fake MCP (TA 도구 전용) ─────────────────────────────────────────


class FakeMCPTA:
    """ta_rsi / ta_macd / ta_bollinger 응답을 주입할 수 있는 Fake."""

    def __init__(
        self,
        rsi_data: dict | None = None,
        macd_data: dict | None = None,
        bb_data: dict | None = None,
        raise_on: str | None = None,  # "rsi" | "macd" | "bollinger" | "all"
    ) -> None:
        self._rsi = rsi_data or {}
        self._macd = macd_data or {}
        self._bb = bb_data or {}
        self._raise_on = raise_on
        self.calls: list[tuple[str, dict]] = []

    async def _call_vps_tool(self, tool_name: str, arguments: dict | None = None) -> Any:
        self.calls.append((tool_name, arguments or {}))
        if self._raise_on in ("all", tool_name.replace("ta_", "")):
            raise RuntimeError(f"Fake error: {tool_name}")
        if tool_name == "ta_rsi":
            return self._rsi
        if tool_name == "ta_macd":
            return self._macd
        if tool_name == "ta_bollinger":
            return self._bb
        return {}


# ── Fixture ──────────────────────────────────────────────────────────


@pytest.fixture
def graph() -> GothamGraph:
    return GothamGraph()


@pytest.fixture
def tracker() -> CatalystTracker:
    return CatalystTracker()


@pytest.fixture
def ingestor(graph: GothamGraph, tracker: CatalystTracker) -> TASignalIngestor:
    return TASignalIngestor(graph, tracker)


# ── 파서 단위 테스트 ──────────────────────────────────────────────────


class TestParseRSI:
    def test_oversold(self):
        sigs = TASignalIngestor._parse_rsi({"rsi": 25.0})
        assert len(sigs) == 1
        assert sigs[0].impact == pytest.approx(6.0)
        assert sigs[0].probability == pytest.approx(0.70)
        assert sigs[0].source == "RSI"

    def test_overbought(self):
        sigs = TASignalIngestor._parse_rsi({"RSI": 75.0})  # 대소문자 변형
        assert len(sigs) == 1
        assert sigs[0].impact == pytest.approx(-6.0)

    def test_neutral(self):
        sigs = TASignalIngestor._parse_rsi({"rsi": 50.0})
        assert sigs == []

    def test_list_fallback(self):
        """data가 list 형식일 때 최신 값 사용."""
        sigs = TASignalIngestor._parse_rsi([{"rsi": 28.0}, {"rsi": 25.5}])
        assert len(sigs) == 1
        assert sigs[0].impact > 0  # oversold → bullish

    def test_missing_key(self):
        sigs = TASignalIngestor._parse_rsi({"unknown_key": 20.0})
        assert sigs == []


class TestParseMACD:
    def test_golden_cross(self):
        sigs = TASignalIngestor._parse_macd({"macd": 0.5, "signal": 0.3})
        assert len(sigs) == 1
        assert sigs[0].impact == pytest.approx(5.0)
        assert "골든" in sigs[0].name

    def test_dead_cross(self):
        sigs = TASignalIngestor._parse_macd({"macd": -0.5, "signal_line": -0.3})
        assert len(sigs) == 1
        assert sigs[0].impact == pytest.approx(-5.0)

    def test_macd_positive_but_below_signal(self):
        """MACD > 0 이지만 signal 위에 없으면 골든 크로스 아님."""
        sigs = TASignalIngestor._parse_macd({"macd": 0.2, "signal": 0.5})
        assert sigs == []

    def test_missing_data(self):
        sigs = TASignalIngestor._parse_macd({"macd": 0.3})
        assert sigs == []


class TestParseBollinger:
    def test_below_lower(self):
        sigs = TASignalIngestor._parse_bollinger(
            {"close": 95.0, "upper": 120.0, "lower": 100.0}
        )
        assert len(sigs) == 1
        assert sigs[0].impact > 0  # bullish

    def test_above_upper(self):
        sigs = TASignalIngestor._parse_bollinger(
            {"price": 130.0, "upperband": 120.0, "lowerband": 100.0}
        )
        assert len(sigs) == 1
        assert sigs[0].impact < 0  # bearish

    def test_inside_band(self):
        sigs = TASignalIngestor._parse_bollinger(
            {"close": 110.0, "upper": 120.0, "lower": 100.0}
        )
        assert sigs == []

    def test_missing_price(self):
        sigs = TASignalIngestor._parse_bollinger({"upper": 120.0, "lower": 100.0})
        assert sigs == []


# ── inject (그래프 + 트래커) 검증 ──────────────────────────────────────


class TestInject:
    def test_symbol_node_created(self, ingestor: TASignalIngestor, graph: GothamGraph):
        sig = TASignal(name="TestSig", description="test", impact=5.0, probability=0.6, source="RSI")
        ingestor._inject("005930", [sig])
        sym_ids = [n.node_id for n in graph.all_nodes if n.kind == NodeKind.SYMBOL]
        assert any("005930" in nid for nid in sym_ids)

    def test_event_node_created(self, ingestor: TASignalIngestor, graph: GothamGraph):
        sig = TASignal(name="RSI과매도", description="rsi<30", impact=6.0, probability=0.70, source="RSI")
        ingestor._inject("005930", [sig])
        event_ids = [n.node_id for n in graph.all_nodes if n.kind == NodeKind.EVENT]
        assert len(event_ids) == 1

    def test_catalyst_registered(self, ingestor: TASignalIngestor, tracker: CatalystTracker):
        sig = TASignal(name="MACD골든", description="golden", impact=5.0, probability=0.60, source="MACD")
        ingestor._inject("000660", [sig])
        cats = tracker.list_by_symbol("000660")
        assert len(cats) == 1
        assert cats[0].catalyst_type == CatalystType.TECHNICAL

    def test_idempotent_node(self, ingestor: TASignalIngestor, graph: GothamGraph):
        """동일 신호 두 번 주입해도 EVENT 노드는 1개 (멱등)."""
        sig = TASignal(name="TestSig", description="test", impact=5.0, probability=0.6, source="RSI")
        ingestor._inject("005930", [sig])
        ingestor._inject("005930", [sig])  # 중복
        event_nodes = [n for n in graph.all_nodes if n.kind == NodeKind.EVENT]
        assert len(event_nodes) == 1

    def test_edge_weight_proportional(self, ingestor: TASignalIngestor, graph: GothamGraph):
        """impact=10 → weight=1.0, impact=5 → weight=0.5."""
        sig_high = TASignal(name="High", description="h", impact=10.0, probability=0.7, source="RSI")
        sig_low = TASignal(name="Low", description="l", impact=5.0, probability=0.6, source="MACD")
        ingestor._inject("005930", [sig_high])
        ingestor._inject("000660", [sig_low])
        edges = graph.all_edges
        weights = {e.weight for e in edges}
        assert 1.0 in weights
        assert 0.5 in weights


# ── ingest async / sync 통합 ─────────────────────────────────────────


class TestIngestIntegration:
    def test_ingest_rsi_oversold(self, ingestor: TASignalIngestor, graph: GothamGraph, tracker: CatalystTracker):
        mcp = FakeMCPTA(rsi_data={"rsi": 22.0})
        result = asyncio.run(ingestor.ingest(mcp, ["005930"]))
        assert "005930" in result
        assert len(result["005930"]) >= 1
        assert result["005930"][0].source == "RSI"

    def test_ingest_neutral_no_signals(self, ingestor: TASignalIngestor):
        mcp = FakeMCPTA(
            rsi_data={"rsi": 50.0},
            macd_data={"macd": 0.1, "signal": 0.15},   # not golden cross
            bb_data={"close": 110.0, "upper": 120.0, "lower": 100.0},
        )
        result = asyncio.run(ingestor.ingest(mcp, ["005930"]))
        # 중립이면 result에 해당 종목 없음 (신호 없음)
        assert "005930" not in result

    def test_ingest_multiple_symbols(self, ingestor: TASignalIngestor):
        mcp = FakeMCPTA(rsi_data={"rsi": 25.0})
        result = asyncio.run(ingestor.ingest(mcp, ["005930", "000660"]))
        assert "005930" in result
        assert "000660" in result

    def test_ingest_mcp_error_graceful(self, ingestor: TASignalIngestor):
        """MCP 전체 오류 → 빈 결과, 예외 전파 안 함."""
        mcp = FakeMCPTA(raise_on="all")
        result = asyncio.run(ingestor.ingest(mcp, ["005930"]))
        assert result == {}

    def test_ingest_sync_wrapper(self, ingestor: TASignalIngestor):
        mcp = FakeMCPTA(rsi_data={"rsi": 28.0})
        result = ingestor.ingest_sync(mcp, ["005930"])
        assert isinstance(result, dict)
        assert "005930" in result

    def test_all_three_signals(self, ingestor: TASignalIngestor, tracker: CatalystTracker):
        """RSI+MACD+Bollinger 동시 신호 → catalyst 3개 등록."""
        mcp = FakeMCPTA(
            rsi_data={"rsi": 22.0},
            macd_data={"macd": 0.8, "signal": 0.5},       # golden cross
            bb_data={"close": 95.0, "upper": 120.0, "lower": 100.0},  # below lower
        )
        result = asyncio.run(ingestor.ingest(mcp, ["005930"]))
        assert len(result["005930"]) == 3
        cats = tracker.list_by_symbol("005930")
        assert len(cats) == 3
        types = {c.catalyst_type for c in cats}
        assert types == {CatalystType.TECHNICAL}
