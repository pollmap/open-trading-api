"""MCPDataProvider 단위 테스트

MCP 서버 없이 mock으로 모든 데이터 페칭 로직 검증.
실제 MCP 호출 테스트는 test_e2e_mcp_backtest.py (@pytest.mark.integration).
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider, _CacheEntry


# ============================================================
# 캐시
# ============================================================

class TestCacheEntry:
    def test_valid_within_ttl(self):
        entry = _CacheEntry(42, ttl=10)
        assert entry.is_valid is True
        assert entry.value == 42

    def test_expired_after_ttl(self):
        entry = _CacheEntry(42, ttl=0)
        # TTL 0 → 즉시 만료
        time.sleep(0.01)
        assert entry.is_valid is False


class TestCacheManagement:
    def test_clear_cache(self):
        provider = MCPDataProvider()
        provider._cache["test"] = _CacheEntry("val", ttl=100)
        count = provider.clear_cache()
        assert count == 1
        assert len(provider._cache) == 0

    def test_cache_stats(self):
        provider = MCPDataProvider()
        provider._cache["valid"] = _CacheEntry("v", ttl=9999)
        provider._cache["expired"] = _CacheEntry("e", ttl=0)
        time.sleep(0.01)
        stats = provider.cache_stats()
        assert stats["total"] == 2
        assert stats["valid"] == 1
        assert stats["expired"] == 1


# ============================================================
# 기준금리
# ============================================================

class TestRiskFreeRate:
    @pytest.fixture
    def provider(self):
        return MCPDataProvider(vps_host="test.local", vps_token="fake")

    def test_parse_ecos_rate_direct_number(self):
        """숫자 직접 반환 (2.75 → 0.0275)"""
        result = {"data": 2.75}
        assert MCPDataProvider._parse_ecos_rate(result) == pytest.approx(0.0275)

    def test_parse_ecos_rate_dict_with_rate_key(self):
        """dict에 rate 키"""
        result = {"data": {"rate": 2.75}}
        assert MCPDataProvider._parse_ecos_rate(result) == pytest.approx(0.0275)

    def test_parse_ecos_rate_dict_with_data_value(self):
        """ECOS 실제 응답 형태 (DATA_VALUE)"""
        result = {"data": {"DATA_VALUE": "2.75"}}
        assert MCPDataProvider._parse_ecos_rate(result) == pytest.approx(0.0275)

    def test_parse_ecos_rate_list_last_entry(self):
        """리스트 → 마지막 값"""
        result = {"data": [{"value": 3.0}, {"value": 2.75}]}
        assert MCPDataProvider._parse_ecos_rate(result) == pytest.approx(0.0275)

    def test_parse_ecos_rate_already_decimal(self):
        """이미 소수점 (0.0275 → 그대로)"""
        result = {"data": 0.0275}
        assert MCPDataProvider._parse_ecos_rate(result) == pytest.approx(0.0275)

    def test_parse_ecos_rate_empty(self):
        assert MCPDataProvider._parse_ecos_rate({}) is None
        assert MCPDataProvider._parse_ecos_rate(None) is None

    @pytest.mark.asyncio
    async def test_get_risk_free_rate_success(self, provider):
        """정상 조회 → 캐시 저장"""
        mock_result = {"success": True, "data": {"rate": 2.75}}
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            rate = await provider.get_risk_free_rate()
        assert rate == pytest.approx(0.0275)
        assert provider._get_cached("risk_free_rate") == pytest.approx(0.0275)

    @pytest.mark.asyncio
    async def test_get_risk_free_rate_cached(self, provider):
        """캐시 히트 → MCP 호출 안 함"""
        provider._set_cached("risk_free_rate", 0.03)
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            rate = await provider.get_risk_free_rate()
        mock.assert_not_called()
        assert rate == pytest.approx(0.03)

    @pytest.mark.asyncio
    async def test_get_risk_free_rate_fallback(self, provider):
        """MCP 실패 → fallback 0.035"""
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.side_effect = RuntimeError("connection refused")
            rate = await provider.get_risk_free_rate()
        assert rate == pytest.approx(0.035)

    def test_get_risk_free_rate_sync(self, provider):
        """동기 래퍼 작동 확인"""
        provider._set_cached("risk_free_rate", 0.025)
        rate = provider.get_risk_free_rate_sync()
        assert rate == pytest.approx(0.025)


# ============================================================
# 벤치마크 수익률
# ============================================================

class TestBenchmarkReturns:
    @pytest.fixture
    def provider(self):
        return MCPDataProvider(vps_host="test.local")

    @pytest.mark.asyncio
    async def test_get_benchmark_success(self, provider):
        mock_result = {
            "success": True,
            "data": [
                {"date": "2026-01-02", "close": 10000},
                {"date": "2026-01-03", "close": 10100},
                {"date": "2026-01-04", "close": 10050},
            ],
        }
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            returns = await provider.get_benchmark_returns()
        assert len(returns) == 2
        assert returns[0] == pytest.approx(0.01)  # 10000 → 10100
        assert returns[1] == pytest.approx(-0.00495, abs=0.001)  # 10100 → 10050

    @pytest.mark.asyncio
    async def test_get_benchmark_fallback(self, provider):
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("timeout")
            returns = await provider.get_benchmark_returns()
        assert returns == []


# ============================================================
# 팩터 스코어
# ============================================================

class TestFactorScores:
    @pytest.fixture
    def provider(self):
        return MCPDataProvider(vps_host="test.local")

    @pytest.mark.asyncio
    async def test_get_factor_scores_success(self, provider):
        mock_result = {
            "success": True,
            "data": {
                "scores": [
                    {"ticker": "005930", "name": "삼성전자", "score": 0.82, "sector": "IT"},
                    {"ticker": "000660", "name": "SK하이닉스", "score": 0.75, "sector": "IT"},
                ],
            },
        }
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            scores = await provider.get_factor_scores(["005930", "000660"])
        assert "005930" in scores
        assert scores["005930"]["score"] == pytest.approx(0.82)
        assert scores["000660"]["name"] == "SK하이닉스"

    @pytest.mark.asyncio
    async def test_get_factor_scores_fallback(self, provider):
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.side_effect = RuntimeError("error")
            scores = await provider.get_factor_scores(["005930"])
        assert scores == {}


# ============================================================
# BL 최적화
# ============================================================

class TestBLWeights:
    @pytest.fixture
    def provider(self):
        return MCPDataProvider(vps_host="test.local")

    @pytest.mark.asyncio
    async def test_get_bl_weights_success(self, provider):
        mock_result = {
            "success": True,
            "data": {
                "optimal_weights": {"005930": 0.15, "000660": 0.12},
            },
        }
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            weights = await provider.get_bl_weights(
                views=[{"ticker": "005930", "view": 0.10}]
            )
        assert weights["005930"] == pytest.approx(0.15)
        assert weights["000660"] == pytest.approx(0.12)


# ============================================================
# DART 재무비율
# ============================================================

class TestDartFinancials:
    @pytest.fixture
    def provider(self):
        return MCPDataProvider(vps_host="test.local")

    @pytest.mark.asyncio
    async def test_get_dart_financials_success(self, provider):
        mock_result = {
            "success": True,
            "data": {
                "opm": 0.132,
                "roe": 0.145,
                "revenue_growth": 0.085,
            },
        }
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            data = await provider.get_dart_financials("005930")
        assert data["opm"] == pytest.approx(0.132)
        assert data["roe"] == pytest.approx(0.145)

    @pytest.mark.asyncio
    async def test_get_dart_financials_uses_stock_code(self, provider):
        """stock_code 파라미터로 호출"""
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = {"success": True, "ratios": {"roe": 5.5}}
            await provider.get_dart_financials("005930")
        call_args = mock.call_args[0]
        assert call_args[1]["stock_code"] == "005930"


# ============================================================
# 동기 래퍼
# ============================================================

class TestSyncWrappers:
    def test_all_sync_methods_exist(self):
        """모든 async 메서드에 대응하는 _sync 래퍼 존재 확인"""
        provider = MCPDataProvider()
        sync_methods = [
            "get_risk_free_rate_sync",
            "get_benchmark_returns_sync",
            "get_factor_scores_sync",
            "get_stock_returns_sync",
            "get_bl_weights_sync",
            "get_dart_financials_sync",
            "get_garch_vol_sync",
            "get_returns_dict_sync",
            "health_check_sync",
        ]
        for method_name in sync_methods:
            assert hasattr(provider, method_name), f"Missing: {method_name}"


# ============================================================
# 환경변수 설정
# ============================================================

class TestEnvironmentConfig:
    def test_default_host(self):
        provider = MCPDataProvider()
        assert "62.171.141.206" in provider._vps_url or provider._vps_host

    def test_custom_host(self):
        provider = MCPDataProvider(vps_host="custom.server.com")
        assert provider._vps_host == "custom.server.com"
        assert "custom.server.com" in provider._vps_url

    def test_custom_kis_url(self):
        provider = MCPDataProvider(kis_mcp_url="http://localhost:9999/mcp")
        assert provider._kis_mcp_url == "http://localhost:9999/mcp"
