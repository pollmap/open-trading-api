"""MCP 데이터 프로바이더 — 모든 MCP 도구 호출의 단일 진입점

Nexus Finance MCP(VPS 364도구)와 KIS Backtest MCP(로컬 3846)에 대한
중앙화된 데이터 페칭 레이어. TTL 캐시, async/sync 지원, 실패 시 안전한 fallback.

오픈소스 배포 시 사용자가 자기 MCP 서버 URL만 환경변수로 지정하면
동일한 파이프라인을 바로 사용할 수 있도록 설계.

Usage:
    from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider

    # 기본 (VPS 환경변수 또는 기본값 사용)
    provider = MCPDataProvider()

    # 동기 호출
    rate = provider.get_risk_free_rate_sync()
    returns = provider.get_benchmark_returns_sync(period="1y")

    # 비동기 호출
    rate = await provider.get_risk_free_rate()

    # 파이프라인과 통합
    from kis_backtest.core.pipeline import QuantPipeline
    pipeline = QuantPipeline(mcp_provider=provider)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence

import httpx

from kis_backtest.portfolio.mcp_connector import (
    normalize_factor_scores,
    normalize_bl_weights,
    normalize_returns,
)

logger = logging.getLogger(__name__)

# 기본 설정 (환경변수로 오버라이드 가능)
DEFAULT_VPS_HOST = os.environ.get("MCP_VPS_HOST", "62.171.141.206")
DEFAULT_VPS_TOKEN = os.environ.get("MCP_VPS_TOKEN", "")
DEFAULT_KIS_MCP_URL = os.environ.get("KIS_MCP_URL", "http://127.0.0.1:3846/mcp")
DEFAULT_TIMEOUT = 30
DEFAULT_CACHE_TTL = 3600  # 1시간


class _CacheEntry:
    """TTL 기반 캐시 엔트리"""
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: int):
        self.value = value
        self.expires_at = time.monotonic() + ttl

    @property
    def is_valid(self) -> bool:
        return time.monotonic() < self.expires_at


class MCPDataProvider:
    """Nexus Finance MCP + KIS Backtest MCP 데이터 프로바이더

    모든 MCP 호출을 중앙화하고, TTL 캐시와 안전한 fallback을 제공한다.
    파이프라인의 다른 모듈은 이 프로바이더를 통해서만 외부 데이터에 접근한다.
    """

    def __init__(
        self,
        vps_host: Optional[str] = None,
        vps_token: Optional[str] = None,
        kis_mcp_url: Optional[str] = None,
        cache_ttl: int = DEFAULT_CACHE_TTL,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self._vps_host = vps_host or DEFAULT_VPS_HOST
        self._vps_token = vps_token or DEFAULT_VPS_TOKEN
        self._kis_mcp_url = kis_mcp_url or DEFAULT_KIS_MCP_URL
        self._vps_url = f"http://{self._vps_host}/mcp"
        self._health_url = f"http://{self._vps_host}/health"
        self._cache_ttl = cache_ttl
        self._timeout = timeout
        self._cache: Dict[str, _CacheEntry] = {}

    # ── 내부 유틸 ──────────────────────────────────────────────

    def _get_cached(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if entry and entry.is_valid:
            return entry.value
        return None

    def _set_cached(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        self._cache[key] = _CacheEntry(value, ttl or self._cache_ttl)

    def _vps_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._vps_token:
            headers["Authorization"] = f"Bearer {self._vps_token}"
        return headers

    async def _call_vps_tool(
        self, tool_name: str, arguments: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """VPS MCP 도구 호출 (JSON-RPC over HTTP)"""
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
            "id": 1,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                self._vps_url,
                json=payload,
                headers=self._vps_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        # JSON-RPC result 추출
        if "result" in data:
            result = data["result"]
            # MCP tool result에서 content 추출
            if isinstance(result, dict) and "content" in result:
                for item in result.get("content", []):
                    if item.get("type") == "text":
                        import json
                        try:
                            return json.loads(item["text"])
                        except (json.JSONDecodeError, KeyError):
                            return {"success": True, "data": item.get("text", "")}
            return result
        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        return data

    async def _call_kis_tool(
        self, tool_name: str, arguments: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """KIS Backtest MCP 도구 호출 (Streamable HTTP)"""
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
            "id": 1,
        }
        # Streamable HTTP MCP 프로토콜: Accept 헤더 필수
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                self._kis_mcp_url,
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        if "result" in data:
            return data["result"]
        if "error" in data:
            raise RuntimeError(f"KIS MCP error: {data['error']}")
        return data

    # ── Health Check ───────────────────────────────────────────

    async def health_check(self) -> Dict[str, Any]:
        """VPS MCP 서버 health check"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    self._health_url, headers=self._vps_headers()
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning("MCP health check 실패: %s", e)
            return {"status": "error", "message": str(e)}

    def health_check_sync(self) -> Dict[str, Any]:
        return _run_sync(self.health_check())

    # ── 기준금리 (ECOS) ───────────────────────────────────────

    async def get_risk_free_rate(self) -> float:
        """한국은행 기준금리 조회 (ECOS MCP)

        Returns:
            float: 연율 기준금리 (예: 0.0275 = 2.75%)
        """
        cached = self._get_cached("risk_free_rate")
        if cached is not None:
            return cached

        try:
            result = await self._call_vps_tool(
                "ecos_get_base_rate",
                {"stat_code": "722Y001", "item_code": "0101000"},
            )
            rate = self._parse_ecos_rate(result)
            if rate is not None:
                self._set_cached("risk_free_rate", rate)
                logger.info("ECOS 기준금리 조회 성공: %.4f", rate)
                return rate
        except Exception as e:
            logger.warning("ECOS 기준금리 조회 실패, fallback 사용: %s", e)

        return 0.035  # fallback

    @staticmethod
    def _parse_ecos_rate(result: Dict[str, Any]) -> Optional[float]:
        """ECOS 결과에서 기준금리 파싱"""
        if not result:
            return None

        # success/data 구조
        data = result.get("data", result)

        # 직접 숫자인 경우
        if isinstance(data, (int, float)):
            return float(data) / 100 if data > 1 else float(data)

        # dict에서 rate 필드 찾기
        for key in ("rate", "base_rate", "value", "DATA_VALUE"):
            if key in data:
                val = float(data[key])
                return val / 100 if val > 1 else val

        # list인 경우 (최신 값)
        if isinstance(data, list) and data:
            last = data[-1] if isinstance(data[-1], dict) else {"value": data[-1]}
            for key in ("rate", "base_rate", "value", "DATA_VALUE"):
                if key in last:
                    val = float(last[key])
                    return val / 100 if val > 1 else val

        return None

    def get_risk_free_rate_sync(self) -> float:
        return _run_sync(self.get_risk_free_rate())

    # ── 벤치마크 수익률 (KRX/stocks_history) ──────────────────

    async def get_benchmark_returns(
        self, ticker: str = "069500", period: str = "1y"
    ) -> List[float]:
        """벤치마크(KODEX200 ETF) 일간 수익률 조회

        Args:
            ticker: 벤치마크 종목코드 (기본: 069500 KODEX200)
            period: 조회 기간 ("3m", "6m", "1y", "2y")
        """
        cache_key = f"benchmark_{ticker}_{period}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            result = await self._call_vps_tool(
                "stocks_history",
                {"ticker": ticker, "period": period},
            )
            returns = normalize_returns(result)
            if returns:
                self._set_cached(cache_key, returns)
                logger.info(
                    "벤치마크 %s 수익률 %d일 조회 성공", ticker, len(returns)
                )
                return returns
        except Exception as e:
            logger.warning("벤치마크 수익률 조회 실패: %s", e)

        return []  # fallback

    def get_benchmark_returns_sync(
        self, ticker: str = "069500", period: str = "1y"
    ) -> List[float]:
        return _run_sync(self.get_benchmark_returns(ticker, period))

    # ── 팩터 스코어 (factor_score) ────────────────────────────

    async def get_factor_scores(
        self, tickers: Sequence[str], factors: Optional[List[str]] = None
    ) -> Dict[str, Dict]:
        """종목 팩터 스코어 조회

        Args:
            tickers: 종목코드 리스트
            factors: 팩터 리스트 (기본: momentum, value, quality, low_vol)
        """
        cache_key = f"factor_{','.join(sorted(tickers))}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            args: Dict[str, Any] = {"tickers": list(tickers)}
            if factors:
                args["factors"] = factors
            result = await self._call_vps_tool("factor_score", args)
            scores = normalize_factor_scores(result)
            if scores:
                self._set_cached(cache_key, scores)
                logger.info("팩터 스코어 %d종목 조회 성공", len(scores))
                return scores
        except Exception as e:
            logger.warning("팩터 스코어 조회 실패: %s", e)

        return {}  # fallback

    def get_factor_scores_sync(
        self, tickers: Sequence[str], factors: Optional[List[str]] = None
    ) -> Dict[str, Dict]:
        return _run_sync(self.get_factor_scores(tickers, factors))

    # ── 종목 수익률 (stocks_history) ──────────────────────────

    async def get_stock_returns(
        self, ticker: str, period: str = "1y"
    ) -> List[float]:
        """개별 종목 일간 수익률 조회"""
        cache_key = f"returns_{ticker}_{period}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            result = await self._call_vps_tool(
                "stocks_history", {"ticker": ticker, "period": period}
            )
            returns = normalize_returns(result)
            if returns:
                self._set_cached(cache_key, returns)
                return returns
        except Exception as e:
            logger.warning("종목 %s 수익률 조회 실패: %s", ticker, e)

        return []

    def get_stock_returns_sync(self, ticker: str, period: str = "1y") -> List[float]:
        return _run_sync(self.get_stock_returns(ticker, period))

    # ── BL 최적화 (portadv_black_litterman) ───────────────────

    async def get_bl_weights(
        self,
        views: List[Dict[str, Any]],
        market_cap_weights: Optional[Dict[str, float]] = None,
        tau: float = 0.05,
    ) -> Dict[str, float]:
        """Black-Litterman 최적 비중 조회"""
        cache_key = f"bl_{hash(str(views))}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            args: Dict[str, Any] = {"views": views, "tau": tau}
            if market_cap_weights:
                args["market_cap_weights"] = market_cap_weights
            result = await self._call_vps_tool("portadv_black_litterman", args)
            weights = normalize_bl_weights(result)
            if weights:
                self._set_cached(cache_key, weights)
                logger.info("BL 최적화 %d종목 완료", len(weights))
                return weights
        except Exception as e:
            logger.warning("BL 최적화 실패: %s", e)

        return {}

    def get_bl_weights_sync(
        self,
        views: List[Dict[str, Any]],
        market_cap_weights: Optional[Dict[str, float]] = None,
        tau: float = 0.05,
    ) -> Dict[str, float]:
        return _run_sync(self.get_bl_weights(views, market_cap_weights, tau))

    # ── DART 재무비율 ─────────────────────────────────────────

    async def get_dart_financials(
        self, ticker: str, report_type: str = "CFS"
    ) -> Dict[str, Any]:
        """DART 재무비율 조회 (Kill Condition 평가용)

        Args:
            ticker: 종목코드
            report_type: "CFS" (연결) 또는 "OFS" (별도) — CFS 기본
        """
        cache_key = f"dart_{ticker}_{report_type}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            result = await self._call_vps_tool(
                "dart_financial_ratios",
                {"ticker": ticker, "report_type": report_type},
            )
            if result and result.get("success"):
                data = result.get("data", result)
                self._set_cached(cache_key, data)
                logger.info("DART 재무비율 %s 조회 성공", ticker)
                return data
        except Exception as e:
            logger.warning("DART 재무비율 %s 조회 실패: %s", ticker, e)

        return {}

    def get_dart_financials_sync(
        self, ticker: str, report_type: str = "CFS"
    ) -> Dict[str, Any]:
        return _run_sync(self.get_dart_financials(ticker, report_type))

    # ── GARCH 변동성 ──────────────────────────────────────────

    async def get_garch_vol(self, ticker: str) -> Optional[float]:
        """GARCH(1,1) 예측 변동성 조회"""
        try:
            result = await self._call_vps_tool(
                "vol_garch", {"ticker": ticker}
            )
            if result and result.get("success"):
                data = result.get("data", result)
                vol = data.get("forecast_vol", data.get("volatility"))
                if vol is not None:
                    return float(vol)
        except Exception as e:
            logger.warning("GARCH 변동성 %s 조회 실패: %s", ticker, e)

        return None

    def get_garch_vol_sync(self, ticker: str) -> Optional[float]:
        return _run_sync(self.get_garch_vol(ticker))

    # ── 다중 종목 수익률 일괄 조회 ────────────────────────────

    async def get_returns_dict(
        self, tickers: Sequence[str], period: str = "1y"
    ) -> Dict[str, List[float]]:
        """여러 종목 수익률을 병렬로 조회하여 {ticker: [returns]} dict 반환"""
        import asyncio

        tasks = {
            ticker: self.get_stock_returns(ticker, period)
            for ticker in tickers
        }
        results = {}
        for ticker, coro in tasks.items():
            try:
                returns = await coro
                if returns:
                    results[ticker] = returns
            except Exception as e:
                logger.warning("종목 %s 수익률 조회 실패: %s", ticker, e)

        return results

    def get_returns_dict_sync(
        self, tickers: Sequence[str], period: str = "1y"
    ) -> Dict[str, List[float]]:
        return _run_sync(self.get_returns_dict(tickers, period))

    # ── 캐시 관리 ─────────────────────────────────────────────

    def clear_cache(self) -> int:
        """캐시 전체 초기화, 삭제된 엔트리 수 반환"""
        count = len(self._cache)
        self._cache.clear()
        return count

    def cache_stats(self) -> Dict[str, Any]:
        """캐시 상태 요약"""
        now = time.monotonic()
        valid = sum(1 for e in self._cache.values() if e.is_valid)
        expired = len(self._cache) - valid
        return {
            "total": len(self._cache),
            "valid": valid,
            "expired": expired,
        }


# ── 동기 실행 헬퍼 ────────────────────────────────────────────

def _run_sync(coro):
    """async 코루틴을 동기로 실행 (이벤트 루프 유무에 관계없이)"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # 이미 이벤트 루프가 돌고 있으면 새 스레드에서 실행
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=60)
    else:
        return asyncio.run(coro)
