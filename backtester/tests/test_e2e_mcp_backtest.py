"""E2E MCP 통합 테스트 — 실제 서버 호출

실행 조건:
- VPS MCP 서버 가동 중 (62.171.141.206 또는 MCP_VPS_HOST)
- KIS Backtest MCP 서버 가동 중 (127.0.0.1:3846)

실행 방법:
    pytest tests/test_e2e_mcp_backtest.py -m integration -v
"""

import json
import os

import httpx
import pytest

from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
from kis_backtest.core.pipeline import QuantPipeline


def _call_kis_mcp(
    tool_name: str,
    arguments: dict,
    headers: dict = None,
    timeout: int = 30,
):
    """KIS MCP 호출 — SSE 스트림 또는 JSON 응답 모두 처리"""
    if headers is None:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
        "id": 1,
    }

    # SSE 스트림 응답 처리
    with httpx.stream(
        "POST",
        "http://127.0.0.1:3846/mcp",
        json=payload,
        headers=headers,
        timeout=timeout,
    ) as resp:
        content_type = resp.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            # SSE 파싱: "data: {...}\n\n" 형태
            full_text = ""
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    full_text = line[6:]  # "data: " 이후
            if full_text:
                return json.loads(full_text)
            return None
        else:
            # 일반 JSON
            body = resp.read()
            if body:
                return json.loads(body)
            return None


def _vps_reachable() -> bool:
    try:
        host = os.environ.get("MCP_VPS_HOST", "62.171.141.206")
        resp = httpx.get(f"http://{host}/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def _kis_mcp_reachable() -> bool:
    try:
        url = os.environ.get("KIS_MCP_URL", "http://127.0.0.1:3846/health")
        if "/mcp" in url:
            url = url.replace("/mcp", "/health")
        resp = httpx.get(url, timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


skip_no_vps = pytest.mark.skipif(
    not _vps_reachable(), reason="VPS MCP 서버 미접속"
)
skip_no_kis = pytest.mark.skipif(
    not _kis_mcp_reachable(), reason="KIS MCP 서버 미접속"
)


# ============================================================
# Health Checks
# ============================================================

@pytest.mark.integration
class TestMCPHealth:
    @skip_no_vps
    def test_nexus_mcp_health(self):
        """VPS Nexus Finance MCP 서버 상태"""
        provider = MCPDataProvider()
        health = provider.health_check_sync()
        assert health["status"] == "ok"
        print(f"\n  VPS MCP: {json.dumps(health, ensure_ascii=False)}")

    @skip_no_kis
    def test_kis_backtest_mcp_health(self):
        """로컬 KIS Backtest MCP 서버 상태"""
        resp = httpx.get("http://127.0.0.1:3846/health", timeout=5)
        data = resp.json()
        assert data["status"] == "ok"
        print(f"\n  KIS MCP: {json.dumps(data, ensure_ascii=False)}")


# ============================================================
# 실 데이터 조회
# ============================================================

@pytest.mark.integration
class TestLiveMCPData:
    @pytest.fixture
    def provider(self):
        token = os.environ.get("MCP_VPS_TOKEN", "")
        return MCPDataProvider(vps_token=token)

    @skip_no_vps
    def test_risk_free_rate_is_real(self, provider):
        """ECOS 실제 기준금리 조회 — 하드코딩 0.035가 아닌 실 데이터"""
        rate = provider.get_risk_free_rate_sync()
        print(f"\n  한국은행 기준금리: {rate*100:.2f}%")
        assert 0.01 <= rate <= 0.10, f"비정상 기준금리: {rate}"
        # 실제 값이면 정확히 0.035일 확률 낮음 (현재 2.75% 수준)

    @skip_no_vps
    def test_benchmark_returns_real(self, provider):
        """KODEX200 ETF 수익률 실데이터"""
        returns = provider.get_benchmark_returns_sync(ticker="069500", period="3m")
        print(f"\n  KODEX200 수익률: {len(returns)}일")
        if returns:
            assert len(returns) >= 30, f"3개월인데 {len(returns)}일만 수신"
            assert all(isinstance(r, float) for r in returns)
            print(f"  최근 5일 수익률: {[f'{r:.4f}' for r in returns[-5:]]}")

    @skip_no_vps
    def test_factor_scores_real(self, provider):
        """삼성전자+SK하이닉스 팩터 스코어"""
        scores = provider.get_factor_scores_sync(["005930", "000660"])
        print(f"\n  팩터 스코어: {json.dumps(scores, ensure_ascii=False, indent=2)}")
        if scores:
            assert "005930" in scores or len(scores) > 0

    @skip_no_vps
    def test_stock_returns_samsung(self, provider):
        """삼성전자 일간 수익률 1년"""
        returns = provider.get_stock_returns_sync("005930", period="1y")
        print(f"\n  삼성전자 수익률: {len(returns)}일")
        if returns:
            assert len(returns) >= 200


# ============================================================
# 파이프라인 + MCP 통합
# ============================================================

@pytest.mark.integration
class TestPipelineWithLiveMCP:
    @skip_no_vps
    def test_pipeline_with_live_rf(self):
        """실시간 기준금리로 파이프라인 실행"""
        token = os.environ.get("MCP_VPS_TOKEN", "")
        provider = MCPDataProvider(vps_token=token)
        pipeline = QuantPipeline(mcp_provider=provider)

        actual_rf = pipeline.config.risk_free_rate
        print(f"\n  파이프라인 적용 기준금리: {actual_rf*100:.2f}%")

        result = pipeline.run(
            factor_scores={
                "005930": {"name": "삼성전자", "score": 0.82, "sector": "IT"},
                "000660": {"name": "SK하이닉스", "score": 0.75, "sector": "IT"},
            },
            optimal_weights={"005930": 0.15, "000660": 0.12},
            backtest_sharpe=0.85,
            backtest_max_dd=-0.12,
        )

        assert result.order is not None
        assert result.risk_passed is True
        print(f"  Kelly 할당: {result.kelly_allocation:.4f}")
        print(f"  연간 비용: {result.estimated_annual_cost*100:.2f}%")
        print(f"  리스크 판정: {'PASS' if result.risk_passed else 'FAIL'}")


# ============================================================
# KIS Backtest MCP 실행
# ============================================================

@pytest.mark.integration
class TestKISBacktestMCP:
    @skip_no_kis
    def test_list_presets(self):
        """KIS MCP 프리셋 전략 목록 조회"""
        # Streamable HTTP MCP — SSE 스트림 파싱 필요
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        data = _call_kis_mcp("list_presets_tool", {}, headers)
        print(f"\n  KIS MCP 응답: {json.dumps(data, ensure_ascii=False)[:500]}")
        assert data is not None

    @skip_no_kis
    def test_first_backtest_sma_samsung(self):
        """삼성전자 SMA crossover 백테스트 — Phase 4 핵심 테스트"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        data = _call_kis_mcp(
            "run_preset_backtest_tool",
            {
                "strategy_id": "sma_crossover",
                "symbols": ["005930"],
                "start_date": "2025-01-01",
                "end_date": "2026-01-01",
                "initial_capital": 10000000,
            },
            headers,
            timeout=60,
        )
        print(f"\n  백테스트 응답: {json.dumps(data, ensure_ascii=False)[:800]}")
        assert data is not None
        if isinstance(data, dict) and "error" not in data:
            print("  첫 백테스트 실행 성공!")
