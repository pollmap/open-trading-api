"""품질 게이트 — 하드코딩/회귀 방지 영구 테스트

이 테스트는 절대 삭제하지 마라. MCP 연동이 풀려서
다시 하드코딩으로 돌아가는 것을 방지한다.
"""

import os
from pathlib import Path

import pytest


BACKTESTER_ROOT = Path(__file__).parent.parent / "kis_backtest"


# ============================================================
# 하드코딩 방지
# ============================================================

class TestNoHardcodedData:
    def test_pipeline_config_rf_is_none_by_default(self):
        """PipelineConfig 기본 rf = None (MCP 우선)"""
        from kis_backtest.core.pipeline import PipelineConfig
        config = PipelineConfig()
        assert config.risk_free_rate is None

    def test_mcp_vps_host_reads_env(self):
        """MCP_VPS_HOST가 환경변수에서 읽히는지 확인"""
        # mcp_connector.py가 os.environ.get 사용 확인
        connector_path = BACKTESTER_ROOT / "portfolio" / "mcp_connector.py"
        content = connector_path.read_text(encoding="utf-8")
        assert 'os.environ.get("MCP_VPS_HOST"' in content

    def test_no_hardcoded_vps_ip_in_logic(self):
        """kis_backtest 소스에서 <MCP_VPS_HOST>이 fallback 외에 없는지 확인"""
        hardcoded_count = 0
        for py_file in BACKTESTER_ROOT.rglob("*.py"):
            # 테스트 파일은 제외 (보안 테스트가 IP를 참조할 수 있음)
            if "tests" in py_file.parts or py_file.name.startswith("test_"):
                continue
            content = py_file.read_text(encoding="utf-8")
            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                if "<MCP_VPS_HOST>" in line:
                    # fallback (os.environ.get(..., "<MCP_VPS_HOST>"))은 허용
                    if "environ" in line or "default" in line.lower():
                        continue
                    hardcoded_count += 1

        assert hardcoded_count == 0, f"하드코딩 VPS IP {hardcoded_count}곳 발견"


# ============================================================
# 모듈 존재 확인
# ============================================================

class TestModulesExist:
    def test_mcp_data_provider_importable(self):
        from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
        assert MCPDataProvider is not None

    def test_cufa_bridge_importable(self):
        from kis_backtest.portfolio.cufa_bridge import CUFABridge
        assert CUFABridge is not None

    def test_portfolio_exports_new_modules(self):
        """portfolio __init__ 에서 새 모듈이 export됨"""
        from kis_backtest.portfolio import MCPDataProvider, CUFABridge
        assert MCPDataProvider is not None
        assert CUFABridge is not None


# ============================================================
# MCP 프로바이더 계약
# ============================================================

class TestMCPProviderContract:
    def test_provider_has_all_required_methods(self):
        """MCPDataProvider 필수 메서드 존재 확인"""
        from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
        provider = MCPDataProvider()
        required = [
            "get_risk_free_rate", "get_risk_free_rate_sync",
            "get_benchmark_returns", "get_benchmark_returns_sync",
            "get_factor_scores", "get_factor_scores_sync",
            "get_stock_returns", "get_stock_returns_sync",
            "get_bl_weights", "get_bl_weights_sync",
            "get_hrp_weights", "get_hrp_weights_sync",
            "get_dart_financials", "get_dart_financials_sync",
            "get_garch_vol", "get_garch_vol_sync",
            "get_returns_dict", "get_returns_dict_sync",
            "get_stock_prices", "get_stock_prices_sync",
            "get_prices_dict", "get_prices_dict_sync",
            "search_stocks", "search_stocks_sync",
            "run_and_wait_backtest", "run_and_wait_backtest_sync",
            "health_check", "health_check_sync",
            "clear_cache", "cache_stats",
        ]
        for method in required:
            assert hasattr(provider, method), f"Missing: {method}"

    def test_provider_accepts_custom_config(self):
        """사용자가 자기 서버 설정으로 프로바이더 생성 가능"""
        from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
        provider = MCPDataProvider(
            vps_host="my-server.example.com",
            vps_token="my-token-123",
            kis_mcp_url="http://localhost:9999/mcp",
            cache_ttl=7200,
            timeout=60,
        )
        assert provider._vps_host == "my-server.example.com"
        assert provider._kis_mcp_url == "http://localhost:9999/mcp"


# ============================================================
# CUFA 브릿지 계약
# ============================================================

class TestCUFABridgeContract:
    def test_parse_returns_kill_conditions(self):
        from kis_backtest.portfolio.cufa_bridge import CUFABridge
        from kis_backtest.portfolio.review_engine import KillCondition
        report = {
            "kill_conditions": [
                {"condition": "test", "metric": "opm", "trigger": 0.1, "current": 0.12},
            ],
        }
        kcs = CUFABridge.parse_kill_conditions(report)
        assert len(kcs) == 1
        assert isinstance(kcs[0], KillCondition)

    def test_ip_strategy_map_covers_all_types(self):
        from kis_backtest.portfolio.cufa_bridge import IP_STRATEGY_MAP
        expected_types = {"growth", "capa", "momentum", "value", "surprise",
                         "turnaround", "dividend", "stability"}
        assert set(IP_STRATEGY_MAP.keys()) == expected_types

    def test_three_stop_always_returns_required_keys(self):
        from kis_backtest.portfolio.cufa_bridge import CUFABridge
        result = CUFABridge.three_stop_risk(1_000_000, 0.02)
        required_keys = {"stop1_price_pct", "stop1_size", "stop2_price_pct",
                        "stop2_size", "stop3_price_pct", "stop3_size",
                        "max_loss_r", "max_loss_amount"}
        assert set(result.keys()) == required_keys
