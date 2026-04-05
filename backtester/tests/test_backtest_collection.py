"""백테스트 결과 수집 + 마이크로구조 MCP 래퍼 테스트"""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider


# ============================================================
# 백테스트 실행 + 폴링
# ============================================================

class TestBacktestExecution:
    @pytest.fixture
    def provider(self):
        return MCPDataProvider(vps_host="test.local", kis_mcp_url="http://test:3846/mcp")

    @pytest.mark.asyncio
    async def test_run_backtest_returns_job_id(self, provider):
        """run_backtest → job_id 반환"""
        mock_response = {
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "data": {"job_id": "test-uuid-123", "status": "running"},
                    }),
                }],
            },
        }
        with patch.object(provider, "_call_kis_tool_sse", new_callable=AsyncMock) as mock:
            mock.return_value = mock_response
            job_id = await provider.run_backtest("sma_crossover", ["005930"])
        assert job_id == "test-uuid-123"

    @pytest.mark.asyncio
    async def test_run_backtest_failure_returns_none(self, provider):
        with patch.object(provider, "_call_kis_tool_sse", new_callable=AsyncMock) as mock:
            mock.side_effect = RuntimeError("connection refused")
            job_id = await provider.run_backtest("sma_crossover", ["005930"])
        assert job_id is None

    @pytest.mark.asyncio
    async def test_poll_completed(self, provider):
        """poll_backtest_result → completed 상태"""
        mock_response = {
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "data": {
                            "status": "completed",
                            "result": {
                                "metrics": {"risk": {"sharpe_ratio": 1.23}},
                                "equity_curve": {"2025-01-02": 10050000},
                            },
                        },
                    }),
                }],
            },
        }
        with patch.object(provider, "_call_kis_tool_sse", new_callable=AsyncMock) as mock:
            mock.return_value = mock_response
            result = await provider.poll_backtest_result("test-uuid", timeout=10, interval=1)
        assert result["status"] == "completed"
        assert "result" in result

    @pytest.mark.asyncio
    async def test_poll_timeout(self, provider):
        """타임아웃 시 status=timeout"""
        mock_response = {
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "data": {"status": "running"},
                    }),
                }],
            },
        }
        with patch.object(provider, "_call_kis_tool_sse", new_callable=AsyncMock) as mock:
            mock.return_value = mock_response
            result = await provider.poll_backtest_result("test-uuid", timeout=3, interval=1)
        assert result["status"] == "timeout"


# ============================================================
# 마이크로구조 MCP 래퍼
# ============================================================

class TestMicroStructureTools:
    @pytest.fixture
    def provider(self):
        return MCPDataProvider(vps_host="test.local")

    @pytest.mark.asyncio
    async def test_micro_toxicity_success(self, provider):
        mock_result = {"success": True, "data": {"vpin": 0.45, "flash_crash_risk": "WARNING"}}
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            data = await provider.get_micro_toxicity("005930")
        assert data["vpin"] == pytest.approx(0.45)

    @pytest.mark.asyncio
    async def test_micro_toxicity_fallback(self, provider):
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.side_effect = RuntimeError("fail")
            data = await provider.get_micro_toxicity("005930")
        assert data == {}

    @pytest.mark.asyncio
    async def test_micro_amihud_success(self, provider):
        mock_result = {"success": True, "data": {"amihud_illiquidity": 0.003}}
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            val = await provider.get_micro_amihud("005930")
        assert val == pytest.approx(0.003)

    @pytest.mark.asyncio
    async def test_micro_kyle_lambda_success(self, provider):
        mock_result = {"success": True, "data": {"kyle_lambda": 0.8}}
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            val = await provider.get_micro_kyle_lambda("005930")
        assert val == pytest.approx(0.8)

    @pytest.mark.asyncio
    async def test_alpha_decay_success(self, provider):
        mock_result = {
            "success": True,
            "data": {"decay_halflife_days": 45, "is_crowded": False},
        }
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            data = await provider.get_alpha_decay("005930")
        assert data["decay_halflife_days"] == 45

    @pytest.mark.asyncio
    async def test_alpha_crowding_success(self, provider):
        mock_result = {"success": True, "data": {"005930": 0.78, "000660": 0.23}}
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            data = await provider.get_alpha_crowding(["005930", "000660"])
        assert data["005930"] == pytest.approx(0.78)

    @pytest.mark.asyncio
    async def test_exec_optimal_success(self, provider):
        mock_result = {
            "success": True,
            "data": {"total_expected_cost_bps": 10.1, "participation_rate": 0.25},
        }
        with patch.object(provider, "_call_vps_tool", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            data = await provider.get_exec_optimal("005930", 5.0)
        assert data["total_expected_cost_bps"] == pytest.approx(10.1)


# ============================================================
# 결과 저장
# ============================================================

class TestResultSaving:
    def test_save_result_creates_file(self, tmp_path):
        provider = MCPDataProvider()
        result = {"sharpe": 1.23, "mdd": -0.12}
        # monkey-patch 경로
        with patch("kis_backtest.portfolio.mcp_data_provider.Path.home") as mock_home:
            mock_home.return_value = tmp_path
            path = provider.save_result(result, category="test", tag="samsung")
        assert path.exists()
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["sharpe"] == 1.23


# ============================================================
# 메서드 존재 확인
# ============================================================

class TestPhase78Methods:
    def test_all_new_methods_exist(self):
        provider = MCPDataProvider()
        new_methods = [
            # Phase 7: 백테스트
            "run_backtest", "poll_backtest_result", "run_and_wait_backtest",
            "run_and_wait_backtest_sync",
            # Phase 8: 마이크로구조
            "get_micro_toxicity", "get_micro_toxicity_sync",
            "get_micro_amihud", "get_micro_amihud_sync",
            "get_micro_kyle_lambda", "get_micro_kyle_lambda_sync",
            "get_alpha_decay", "get_alpha_decay_sync",
            "get_alpha_crowding", "get_alpha_crowding_sync",
            "get_exec_optimal", "get_exec_optimal_sync",
            # 결과 저장
            "save_result",
        ]
        for method in new_methods:
            assert hasattr(provider, method), f"Missing: {method}"
