"""UniverseBuilder + MCPDataProvider bug fix 테스트

Phase D: 유니버스 빌더 + get_returns_dict/get_bl_weights/get_hrp_weights 테스트
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
from kis_backtest.portfolio.universe_builder import (
    DEFAULT_ETFS,
    DEFAULT_SECTORS,
    SectorDef,
    StockInfo,
    UniverseBuilder,
    UniverseResult,
    _compute_screening_score,
)


# ============================================================
# D-1: get_returns_dict bug fix
# ============================================================

class TestGetReturnsDictFixed:
    @pytest.fixture
    def provider(self):
        return MCPDataProvider(vps_host="test.local")

    @pytest.mark.asyncio
    async def test_returns_dict_passes_start_date(self, provider):
        """start_date/end_date가 get_stock_returns에 정확히 전달되는지 확인"""
        mock_returns = [0.01, -0.005, 0.008, 0.003, -0.002] * 50

        with patch.object(provider, "get_stock_returns", new_callable=AsyncMock) as mock:
            mock.return_value = mock_returns
            result = await provider.get_returns_dict(
                ["005930", "000660"],
                start_date="20210101",
                end_date="20260405",
            )

        # start_date/end_date가 keyword로 전달되었는지 확인
        assert mock.call_count == 2
        for call in mock.call_args_list:
            assert call.kwargs.get("start_date") == "20210101"
            assert call.kwargs.get("end_date") == "20260405"

        assert "005930" in result
        assert "000660" in result
        assert len(result["005930"]) == 250

    @pytest.mark.asyncio
    async def test_returns_dict_no_period_param(self, provider):
        """period 파라미터가 제거되었는지 확인 (버그 원인)"""
        import inspect
        sig = inspect.signature(provider.get_returns_dict)
        param_names = list(sig.parameters.keys())
        assert "period" not in param_names
        assert "start_date" in param_names
        assert "end_date" in param_names

    @pytest.mark.asyncio
    async def test_returns_dict_concurrent_limit(self, provider):
        """max_concurrent 세마포어 동작 확인"""
        call_count = 0

        async def _slow_fetch(ticker, start_date=None, end_date=None):
            nonlocal call_count
            call_count += 1
            return [0.01] * 100

        with patch.object(provider, "get_stock_returns", side_effect=_slow_fetch):
            result = await provider.get_returns_dict(
                [f"00{i:04d}" for i in range(10)],
                max_concurrent=3,
            )

        assert call_count == 10
        assert len(result) == 10

    @pytest.mark.asyncio
    async def test_returns_dict_partial_failure(self, provider):
        """일부 종목 실패 시 나머지는 정상 반환"""
        async def _partial(ticker, start_date=None, end_date=None):
            if ticker == "FAIL":
                raise RuntimeError("connection error")
            return [0.01] * 50

        with patch.object(provider, "get_stock_returns", side_effect=_partial):
            result = await provider.get_returns_dict(["005930", "FAIL", "000660"])

        assert "005930" in result
        assert "000660" in result
        assert "FAIL" not in result


# ============================================================
# D-2: get_bl_weights fix (series_list/names 필수)
# ============================================================

class TestBLWeightsFixed:
    @pytest.fixture
    def provider(self):
        return MCPDataProvider(vps_host="test.local")

    @pytest.mark.asyncio
    async def test_bl_weights_sends_series_list(self, provider):
        """series_list와 names가 MCP에 전달되는지 확인"""
        returns_dict = {
            "005930": [0.01, -0.005, 0.008] * 20,
            "000660": [0.015, -0.01, 0.003] * 20,
        }
        mock_result = {
            "success": True,
            "data": {"optimal_weights": {"005930": 0.6, "000660": 0.4}},
        }

        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            weights = await provider.get_bl_weights(returns_dict)

        call_args = mock.call_args[0]
        assert call_args[0] == "portadv_black_litterman"
        tool_args = call_args[1]
        assert "series_list" in tool_args
        assert "names" in tool_args
        assert len(tool_args["series_list"]) == 2
        assert set(tool_args["names"]) == {"005930", "000660"}

        assert weights["005930"] == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_bl_weights_insufficient_data(self, provider):
        """30일 미만 데이터 → 빈 dict 반환"""
        returns_dict = {"005930": [0.01] * 10}
        weights = await provider.get_bl_weights(returns_dict)
        assert weights == {}

    @pytest.mark.asyncio
    async def test_bl_weights_with_views_and_caps(self, provider):
        """views와 market_caps가 올바르게 전달되는지"""
        returns_dict = {"A": [0.01] * 50, "B": [0.02] * 50}
        views = [{"asset": "A", "return": 0.12, "confidence": 0.8}]
        caps = {"A": 100000, "B": 50000}

        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = {"success": True, "data": {"optimal_weights": {"A": 0.7, "B": 0.3}}}
            await provider.get_bl_weights(returns_dict, views=views, market_caps=caps, risk_free_rate=0.025)

        tool_args = mock.call_args[0][1]
        assert tool_args["views"] == views
        assert tool_args["market_caps"] == [100000, 50000]
        assert tool_args["risk_free_rate"] == 0.025


# ============================================================
# D-3: get_hrp_weights
# ============================================================

class TestHRPWeights:
    @pytest.fixture
    def provider(self):
        return MCPDataProvider(vps_host="test.local")

    @pytest.mark.asyncio
    async def test_hrp_weights_calls_portadv_hrp(self, provider):
        """portadv_hrp MCP 도구가 호출되는지"""
        returns_dict = {"A": [0.01] * 60, "B": [-0.005] * 60, "C": [0.008] * 60}
        mock_result = {
            "success": True,
            "data": {"optimal_weights": {"A": 0.4, "B": 0.3, "C": 0.3}},
        }

        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            weights = await provider.get_hrp_weights(returns_dict)

        assert mock.call_args[0][0] == "portadv_hrp"
        assert len(mock.call_args[0][1]["series_list"]) == 3
        assert weights["A"] == pytest.approx(0.4)

    @pytest.mark.asyncio
    async def test_hrp_weights_aligns_lengths(self, provider):
        """서로 다른 길이의 수익률 → 최소 공통 길이로 정렬"""
        returns_dict = {"A": [0.01] * 100, "B": [0.02] * 60}

        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = {"success": True, "data": {"optimal_weights": {"A": 0.5, "B": 0.5}}}
            await provider.get_hrp_weights(returns_dict)

        series = mock.call_args[0][1]["series_list"]
        assert len(series[0]) == 60  # 최소 길이로 자름
        assert len(series[1]) == 60


# ============================================================
# D-4: search_stocks
# ============================================================

class TestSearchStocks:
    @pytest.fixture
    def provider(self):
        return MCPDataProvider(vps_host="test.local")

    @pytest.mark.asyncio
    async def test_search_stocks_parses_list(self, provider):
        """list 형태 결과 파싱"""
        mock_result = {
            "data": [
                {"ticker": "000720", "name": "현대건설", "market": "KOSPI"},
                {"ticker": "028260", "name": "삼성물산", "market": "KOSPI"},
            ]
        }

        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            stocks = await provider.search_stocks("건설")

        assert len(stocks) == 2
        assert stocks[0]["ticker"] == "000720"
        assert stocks[0]["name"] == "현대건설"

    @pytest.mark.asyncio
    async def test_search_stocks_alternative_keys(self, provider):
        """stock_code/stock_name 키도 지원"""
        mock_result = {
            "data": [{"stock_code": "005930", "stock_name": "삼성전자"}]
        }

        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            stocks = await provider.search_stocks("삼성")

        assert stocks[0]["ticker"] == "005930"

    @pytest.mark.asyncio
    async def test_search_stocks_failure(self, provider):
        """MCP 실패 시 빈 리스트"""
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.side_effect = RuntimeError("timeout")
            stocks = await provider.search_stocks("건설")

        assert stocks == []


# ============================================================
# D-5: UniverseBuilder
# ============================================================

class TestUniverseBuilder:
    @pytest.fixture
    def mock_provider(self):
        provider = MagicMock(spec=MCPDataProvider)

        # search_stocks → 키워드별 종목 반환
        async def _search(keyword):
            sector_map = {
                "건설": [
                    {"ticker": "000720", "name": "현대건설", "market": "KOSPI"},
                    {"ticker": "028260", "name": "삼성물산", "market": "KOSPI"},
                    {"ticker": "034730", "name": "SK", "market": "KOSPI"},
                ],
                "반도체": [
                    {"ticker": "000660", "name": "SK하이닉스", "market": "KOSPI"},
                    {"ticker": "058470", "name": "리노공업", "market": "KOSDAQ"},
                ],
            }
            return sector_map.get(keyword, [])

        provider.search_stocks = AsyncMock(side_effect=_search)

        # get_dart_financials → 재무비율 반환
        financial_map = {
            "000720": {"roe": 5.5, "opm": 6.2, "dte": 180.0},
            "028260": {"roe": 8.2, "opm": 7.5, "dte": 120.0},
            "034730": {"roe": 3.1, "opm": 4.0, "dte": 200.0},
            "000660": {"roe": 35.6, "opm": 22.3, "dte": 45.0},
            "058470": {"roe": 12.0, "opm": 15.0, "dte": 60.0},
        }

        async def _dart(ticker, report_type="CFS"):
            return financial_map.get(ticker, {})

        provider.get_dart_financials = AsyncMock(side_effect=_dart)
        provider.save_result = MagicMock(return_value=Path("/tmp/test.json"))

        return provider

    @pytest.mark.asyncio
    async def test_build_universe_2sectors(self, mock_provider):
        """2개 섹터 × 2종목 = 4종목 + ETF 3개"""
        builder = UniverseBuilder(
            mock_provider,
            sectors=[
                SectorDef("건설", ["건설"], top_n=2),
                SectorDef("반도체", ["반도체"], top_n=2),
            ],
        )
        universe = await builder.build()

        assert universe.stock_count == 4
        assert universe.total_count == 7  # 4 + 3 ETF

        # 건설: 삼성물산(ROE 8.2) > 현대건설(ROE 5.5) > SK(ROE 3.1)
        assert "028260" in universe.stocks  # 삼성물산 (1등)
        assert "000720" in universe.stocks  # 현대건설 (2등)

        # 반도체: SK하이닉스(ROE 35.6) > 리노공업(ROE 12.0)
        assert "000660" in universe.stocks
        assert "058470" in universe.stocks

    @pytest.mark.asyncio
    async def test_build_handles_search_failure(self, mock_provider):
        """검색 실패한 섹터는 빈 결과"""
        builder = UniverseBuilder(
            mock_provider,
            sectors=[SectorDef("없는섹터", ["xyzxyz"], top_n=2)],
        )
        universe = await builder.build()
        assert universe.stock_count == 0
        assert universe.total_count == 3  # ETF만

    def test_to_factor_scores(self, mock_provider):
        """UniverseResult → factor_scores 변환"""
        result = UniverseResult(
            stocks={
                "000720": {"name": "현대건설", "sector": "건설", "score": 0.65, "market": "KOSPI"},
            },
            etfs=DEFAULT_ETFS,
        )
        builder = UniverseBuilder(mock_provider)
        scores = builder.to_factor_scores(result)

        assert scores["000720"]["name"] == "현대건설"
        assert scores["000720"]["score"] == 0.65
        assert scores["000720"]["sector"] == "건설"

        # ETF는 점수 0.5
        assert scores["148070"]["score"] == 0.5
        assert scores["148070"]["sector"] == "bond"

    def test_universe_result_serialization(self):
        """UniverseResult to_dict"""
        result = UniverseResult(
            stocks={"000720": {"name": "현대건설", "sector": "건설", "score": 0.65}},
            etfs={"148070": {"name": "KOSEF 국고채10년", "asset_class": "bond"}},
            screening_log=["test log"],
        )
        d = result.to_dict()
        assert d["stock_count"] == 1
        assert d["total_count"] == 2
        # JSON 직렬화 가능한지 확인
        json_str = json.dumps(d, ensure_ascii=False)
        assert "현대건설" in json_str


# ============================================================
# 스크리닝 점수 유닛 테스트
# ============================================================

class TestScreeningScore:
    def test_high_roe_high_score(self):
        """ROE 30%, OPM 25%, DTE 0% → 만점"""
        score = _compute_screening_score(30.0, 25.0, 0.0)
        assert score == pytest.approx(1.0)

    def test_zero_metrics(self):
        """ROE 0%, OPM 0%, DTE 300% → 0점"""
        score = _compute_screening_score(0.0, 0.0, 300.0)
        assert score == pytest.approx(0.0)

    def test_balanced(self):
        """중간값 테스트"""
        score = _compute_screening_score(15.0, 12.5, 150.0)
        # ROE: 15/30=0.5 × 0.4 = 0.2
        # OPM: 12.5/25=0.5 × 0.3 = 0.15
        # DTE: (1 - 150/300) = 0.5 × 0.3 = 0.15
        assert score == pytest.approx(0.5)


# ============================================================
# Sync 래퍼 존재 확인
# ============================================================

class TestSyncWrappers:
    def test_new_sync_methods_exist(self):
        """Phase D에서 추가된 sync 메서드 존재 확인"""
        provider = MCPDataProvider()
        new_methods = [
            "get_hrp_weights_sync",
            "search_stocks_sync",
        ]
        for method_name in new_methods:
            assert hasattr(provider, method_name), f"Missing: {method_name}"

    def test_returns_dict_sync_signature(self):
        """get_returns_dict_sync에 start_date/end_date 파라미터 존재"""
        import inspect
        sig = inspect.signature(MCPDataProvider.get_returns_dict_sync)
        params = list(sig.parameters.keys())
        assert "start_date" in params
        assert "end_date" in params
        assert "period" not in params
