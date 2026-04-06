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
    returns = provider.get_benchmark_returns_sync(start_date="20250101")

    # 비동기 호출
    rate = await provider.get_risk_free_rate()

    # 파이프라인과 통합
    from kis_backtest.core.pipeline import QuantPipeline
    pipeline = QuantPipeline(mcp_provider=provider)
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import time
from pathlib import Path
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


def _load_token_from_mcp_json(
    server_name: str = "nexus-finance",
) -> str:
    """~/.mcp.json에서 MCP 서버 Bearer 토큰 자동 추출

    토큰 해결 체인: vps_token 인자 → MCP_VPS_TOKEN env → ~/.mcp.json → ""
    오픈소스 사용자가 ~/.mcp.json에 토큰을 설정하면 자동으로 읽힘.
    """
    try:
        mcp_json = Path.home() / ".mcp.json"
        if not mcp_json.exists():
            return ""
        config = _json.loads(mcp_json.read_text(encoding="utf-8"))
        headers = (
            config.get("mcpServers", {})
            .get(server_name, {})
            .get("headers", {})
        )
        auth = headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]  # "Bearer xxx" → "xxx"
    except Exception as e:
        logger.debug("~/.mcp.json 토큰 로딩 실패: %s", e)
    return ""
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
        # 토큰 해결 체인: 인자 → 환경변수 → ~/.mcp.json → ""
        self._vps_token = vps_token or DEFAULT_VPS_TOKEN or _load_token_from_mcp_json()
        self._kis_mcp_url = kis_mcp_url or DEFAULT_KIS_MCP_URL
        self._vps_url = f"http://{self._vps_host}/mcp"
        self._health_url = f"http://{self._vps_host}/health"
        self._cache_ttl = cache_ttl
        self._timeout = timeout
        self._cache: Dict[str, _CacheEntry] = {}
        self._vps_session_id: Optional[str] = None  # Streamable HTTP 세션

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

    async def _ensure_vps_session(self) -> None:
        """Streamable HTTP MCP 세션 초기화 (필요 시)"""
        if self._vps_session_id:
            return

        payload = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "kis-quant-pipeline", "version": "1.0.0"},
            },
            "id": 0,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST", self._vps_url, json=payload, headers=self._vps_headers()
            ) as resp:
                resp.raise_for_status()
                session_id = resp.headers.get("mcp-session-id")
                if session_id:
                    self._vps_session_id = session_id
                    logger.info("VPS MCP 세션 초기화 완료: %s...", session_id[:8])
                else:
                    logger.warning("VPS MCP 세션 ID 미수신")
                # SSE 응답 body 소비 (연결 정리)
                async for _ in resp.aiter_lines():
                    pass

    async def _call_vps_tool(
        self, tool_name: str, arguments: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """VPS MCP 도구 호출 (Streamable HTTP + SSE 스트림)"""
        await self._ensure_vps_session()

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
            "id": 1,
        }
        headers = self._vps_headers()
        if self._vps_session_id:
            headers["Mcp-Session-Id"] = self._vps_session_id

        data = await self._post_mcp_sse(self._vps_url, payload, headers)

        # 세션 만료 시 재초기화 후 재시도
        if isinstance(data, dict) and "error" in data:
            err_msg = str(data.get("error", {}).get("message", ""))
            if "session" in err_msg.lower():
                self._vps_session_id = None
                await self._ensure_vps_session()
                headers["Mcp-Session-Id"] = self._vps_session_id or ""
                data = await self._post_mcp_sse(self._vps_url, payload, headers)

        # JSON-RPC result에서 content 추출
        if isinstance(data, dict) and "result" in data:
            result = data["result"]
            if isinstance(result, dict) and "content" in result:
                for item in result.get("content", []):
                    if item.get("type") == "text":
                        try:
                            return _json.loads(item["text"])
                        except (_json.JSONDecodeError, KeyError):
                            return {"success": True, "data": item.get("text", "")}
            return result
        if isinstance(data, dict) and "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        return data or {}

    async def _post_mcp_sse(
        self, url: str, payload: Dict, headers: Dict
    ) -> Optional[Dict]:
        """MCP SSE 스트림 POST 호출 — data: 라인 파싱"""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST", url, json=payload, headers=headers
            ) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")

                if "text/event-stream" in content_type:
                    # SSE 전체를 모아서 마지막 data: 라인 파싱
                    all_data = []
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            all_data.append(line[6:])
                    if all_data:
                        # 여러 data: 라인이 올 수 있음 — 전체 합치기
                        full_text = all_data[-1]
                        try:
                            return _json.loads(full_text)
                        except _json.JSONDecodeError:
                            # data: 라인이 여러 줄에 걸쳐 올 수 있음
                            combined = "".join(all_data)
                            try:
                                return _json.loads(combined)
                            except _json.JSONDecodeError:
                                logger.warning("SSE 파싱 실패: %s...", combined[:100])
                    return None
                else:
                    body = await resp.aread()
                    try:
                        return _json.loads(body)
                    except _json.JSONDecodeError:
                        return None

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
                {},  # 인자 없이 호출 — MCP 도구가 기본 파라미터 사용
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
        """ECOS 결과에서 기준금리 파싱

        실제 ECOS 응답: {"success": true, "data": [{"value": "2.75", ...}, ...]}
        """
        if not result:
            return None

        data = result.get("data", result)

        # 직접 숫자인 경우
        if isinstance(data, (int, float)):
            return float(data) / 100 if data > 1 else float(data)

        # list인 경우 — 마지막 항목의 value (최신 금리)
        if isinstance(data, list) and data:
            last = data[-1] if isinstance(data[-1], dict) else {"value": data[-1]}
            for key in ("value", "rate", "base_rate", "DATA_VALUE"):
                if key in last:
                    val = float(last[key])
                    return val / 100 if val > 1 else val

        # dict에서 rate 필드 찾기
        if isinstance(data, dict):
            for key in ("rate", "base_rate", "value", "DATA_VALUE"):
                if key in data:
                    val = float(data[key])
                    return val / 100 if val > 1 else val

        return None

    def get_risk_free_rate_sync(self) -> float:
        return _run_sync(self.get_risk_free_rate())

    # ── 벤치마크 수익률 (KRX/stocks_history) ──────────────────

    async def get_benchmark_returns(
        self, ticker: str = "069500", period: str = "1y",
        start_date: Optional[str] = None, end_date: Optional[str] = None,
    ) -> List[float]:
        """벤치마크(KODEX200 ETF) 일간 수익률 조회

        Args:
            ticker: 벤치마크 종목코드 (기본: 069500 KODEX200)
            start_date: 시작일 "YYYYMMDD" (예: "20210101")
            end_date: 종료일 "YYYYMMDD" (예: "20260405")
        """
        cache_key = f"benchmark_{ticker}_{start_date or 'default'}_{end_date or 'default'}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            args: Dict[str, Any] = {"stock_code": ticker}
            if start_date:
                args["start_date"] = start_date
            if end_date:
                args["end_date"] = end_date
            result = await self._call_vps_tool("stocks_history", args)
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
        self, ticker: str = "069500",
        start_date: Optional[str] = None, end_date: Optional[str] = None,
    ) -> List[float]:
        return _run_sync(self.get_benchmark_returns(ticker, start_date=start_date, end_date=end_date))

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
            # factor_score는 stocks_data: dict 형태 필요
            # {"000720": {"name": "현대건설"}} 또는 주가 데이터 포함 가능
            stocks_dict = {t: {"name": t} for t in tickers}
            args: Dict[str, Any] = {"stocks_data": stocks_dict}
            if factors:
                args["factors"] = factors
            result = await self._call_vps_tool("factor_score", args)
            # 에러 문자열 방어 (normalize_factor_scores가 str에서 크래시)
            if isinstance(result, dict) and isinstance(result.get("data"), str):
                logger.warning("factor_score 에러: %s", result["data"][:100])
                return {}
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
        self, ticker: str,
        start_date: Optional[str] = None, end_date: Optional[str] = None,
    ) -> List[float]:
        """개별 종목 일간 수익률 조회 (기본: 최대 기간)

        Args:
            start_date: "YYYYMMDD" (기본: "20000101" 최대 기간)
            end_date: "YYYYMMDD" (기본: 오늘)
        """
        cache_key = f"returns_{ticker}_{start_date or 'max'}_{end_date or 'today'}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            args: Dict[str, Any] = {"stock_code": ticker}
            if start_date:
                args["start_date"] = start_date
            if end_date:
                args["end_date"] = end_date
            result = await self._call_vps_tool("stocks_history", args)
            returns = normalize_returns(result)
            if returns:
                self._set_cached(cache_key, returns)
                return returns
        except Exception as e:
            logger.warning("종목 %s 수익률 조회 실패: %s", ticker, e)

        return []

    def get_stock_returns_sync(
        self, ticker: str,
        start_date: Optional[str] = None, end_date: Optional[str] = None,
    ) -> List[float]:
        return _run_sync(self.get_stock_returns(ticker, start_date=start_date, end_date=end_date))

    # ── BL 최적화 (portadv_black_litterman) ───────────────────

    async def get_bl_weights(
        self,
        returns_dict: Dict[str, List[float]],
        views: Optional[List[Dict[str, Any]]] = None,
        market_caps: Optional[Dict[str, float]] = None,
        tau: float = 0.05,
        risk_free_rate: Optional[float] = None,
    ) -> Dict[str, float]:
        """Black-Litterman 최적 비중 조회

        MCP portadv_black_litterman 필수 파라미터: series_list, names.
        returns_dict에서 자동 변환.

        Args:
            returns_dict: {ticker: [daily_returns]} — 필수
            views: BL 투자자 뷰 (factor_to_views 출력)
            market_caps: {ticker: 시가총액} — 균형 비중용
            tau: 불확실성 스케일 (기본 0.05)
            risk_free_rate: 무위험 이자율
        """
        cache_key = f"bl_{hash(str(sorted(returns_dict.keys())))}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            tickers = list(returns_dict.keys())
            # 수익률 길이 정렬 (최소 공통 길이)
            min_len = min(len(returns_dict[t]) for t in tickers) if tickers else 0
            if min_len < 30:
                logger.warning("BL: 수익률 데이터 부족 (%d일)", min_len)
                return {}

            args: Dict[str, Any] = {
                "series_list": [returns_dict[t][-min_len:] for t in tickers],
                "names": tickers,
                "tau": tau,
            }
            if views:
                args["views"] = views
            if market_caps:
                args["market_caps"] = [market_caps.get(t, 0) for t in tickers]
            if risk_free_rate is not None:
                args["risk_free_rate"] = risk_free_rate

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
        returns_dict: Dict[str, List[float]],
        views: Optional[List[Dict[str, Any]]] = None,
        market_caps: Optional[Dict[str, float]] = None,
        tau: float = 0.05,
        risk_free_rate: Optional[float] = None,
    ) -> Dict[str, float]:
        return _run_sync(self.get_bl_weights(returns_dict, views, market_caps, tau, risk_free_rate))

    # ── HRP 최적화 (portadv_hrp) ─────────────────────────────

    async def get_hrp_weights(
        self,
        returns_dict: Dict[str, List[float]],
    ) -> Dict[str, float]:
        """Hierarchical Risk Parity 최적 비중 (López de Prado)

        MCP portadv_hrp: 공분산 역행렬 없는 트리 기반 배분.
        series_list, names 필수.

        Args:
            returns_dict: {ticker: [daily_returns]} — 모든 시리즈 동일 길이 권장
        """
        cache_key = f"hrp_{hash(str(sorted(returns_dict.keys())))}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            tickers = list(returns_dict.keys())
            min_len = min(len(returns_dict[t]) for t in tickers) if tickers else 0
            if min_len < 30:
                logger.warning("HRP: 수익률 데이터 부족 (%d일)", min_len)
                return {}

            args = {
                "series_list": [returns_dict[t][-min_len:] for t in tickers],
                "names": tickers,
            }
            result = await self._call_vps_tool("portadv_hrp", args)
            weights = normalize_bl_weights(result)  # BL과 동일한 {ticker: weight} 형식
            if weights:
                self._set_cached(cache_key, weights)
                logger.info("HRP 최적화 %d종목 완료", len(weights))
                return weights
        except Exception as e:
            logger.warning("HRP 최적화 실패: %s", e)

        return {}

    def get_hrp_weights_sync(
        self,
        returns_dict: Dict[str, List[float]],
    ) -> Dict[str, float]:
        return _run_sync(self.get_hrp_weights(returns_dict))

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
                {"stock_code": ticker},
            )
            if result and result.get("success"):
                # DART 응답: {"success": true, "ratios": {...}} or {"data": {...}}
                data = result.get("ratios", result.get("data", result))
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

    # ── 종목 검색 (stocks_search) ─────────────────────────────

    async def search_stocks(self, keyword: str) -> List[Dict[str, str]]:
        """종목 검색 — stocks_search MCP 도구

        Args:
            keyword: 검색어 (한국어 회사명 또는 코드, 예: "건설", "반도체", "005930")

        Returns:
            [{"ticker": "005930", "name": "삼성전자", ...}, ...]
        """
        cache_key = f"search_{keyword}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            result = await self._call_vps_tool("stocks_search", {"keyword": keyword})
            stocks = self._parse_search_result(result)
            if stocks:
                self._set_cached(cache_key, stocks, ttl=86400)  # 24시간 캐시
                logger.info("종목 검색 '%s': %d건", keyword, len(stocks))
                return stocks
        except Exception as e:
            logger.warning("종목 검색 '%s' 실패: %s", keyword, e)

        return []

    @staticmethod
    def _parse_search_result(result: Dict[str, Any]) -> List[Dict[str, str]]:
        """stocks_search 결과를 [{ticker, name, ...}] 리스트로 파싱"""
        if not result:
            return []

        data = result.get("data", result)

        # list of dicts
        if isinstance(data, list):
            return [
                {
                    "ticker": item.get("ticker", item.get("stock_code", item.get("code", ""))),
                    "name": item.get("name", item.get("stock_name", "")),
                    "market": item.get("market", ""),
                }
                for item in data
                if item.get("ticker") or item.get("stock_code") or item.get("code")
            ]

        # dict with items
        if isinstance(data, dict) and "items" in data:
            return MCPDataProvider._parse_search_result({"data": data["items"]})

        # 텍스트 결과 (비구조화) — 빈 리스트 반환
        return []

    def search_stocks_sync(self, keyword: str) -> List[Dict[str, str]]:
        return _run_sync(self.search_stocks(keyword))

    # ── GARCH 변동성 ──────────────────────────────────────────

    async def get_garch_vol(self, ticker: str) -> Optional[float]:
        """GARCH(1,1) 예측 변동성 조회"""
        try:
            result = await self._call_vps_tool(
                "vol_garch", {"stock_code": ticker}
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
        self,
        tickers: Sequence[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_concurrent: int = 5,
    ) -> Dict[str, List[float]]:
        """여러 종목 수익률을 병렬로 조회하여 {ticker: [returns]} dict 반환

        Args:
            tickers: 종목코드 리스트
            start_date: "YYYYMMDD" (예: "20210101")
            end_date: "YYYYMMDD" (예: "20260405")
            max_concurrent: 동시 MCP 호출 수 (rate limit 방지)
        """
        sem = asyncio.Semaphore(max_concurrent)

        async def _fetch(ticker: str) -> tuple:
            async with sem:
                try:
                    returns = await self.get_stock_returns(
                        ticker, start_date=start_date, end_date=end_date,
                    )
                    return ticker, returns
                except Exception as e:
                    logger.warning("종목 %s 수익률 조회 실패: %s", ticker, e)
                    return ticker, []

        gather_results = await asyncio.gather(*[_fetch(t) for t in tickers])
        return {t: r for t, r in gather_results if r}

    def get_returns_dict_sync(
        self,
        tickers: Sequence[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_concurrent: int = 5,
    ) -> Dict[str, List[float]]:
        return _run_sync(self.get_returns_dict(tickers, start_date, end_date, max_concurrent))

    # ── 캐시 관리 ─────────────────────────────────────────────

    def clear_cache(self) -> int:
        """캐시 전체 초기화, 삭제된 엔트리 수 반환"""
        count = len(self._cache)
        self._cache.clear()
        return count

    def cache_stats(self) -> Dict[str, Any]:
        """캐시 상태 요약"""
        valid = sum(1 for e in self._cache.values() if e.is_valid)
        expired = len(self._cache) - valid
        return {
            "total": len(self._cache),
            "valid": valid,
            "expired": expired,
        }

    # ══════════════════════════════════════════════════════════════
    # 스키마 발견: 364도구 파라미터 자동 조회
    # ══════════════════════════════════════════════════════════════

    async def discover_tools(self) -> Dict[str, Dict]:
        """VPS MCP 전체 도구 스키마 조회 (tools/list)

        Returns:
            {tool_name: {"required": [...], "params": {...}, "description": "..."}}
        """
        cached = self._get_cached("_tool_catalog")
        if cached is not None:
            return cached

        await self._ensure_vps_session()
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": {},
            "id": 99,
        }
        headers = self._vps_headers()
        if self._vps_session_id:
            headers["Mcp-Session-Id"] = self._vps_session_id

        data = await self._post_mcp_sse(self._vps_url, payload, headers)
        if not data:
            return {}

        tools = data.get("result", {}).get("tools", [])
        catalog: Dict[str, Dict] = {}
        for t in tools:
            name = t.get("name", "")
            schema = t.get("inputSchema", {})
            catalog[name] = {
                "required": schema.get("required", []),
                "params": {
                    k: v.get("type", "unknown")
                    for k, v in schema.get("properties", {}).items()
                },
                "description": t.get("description", "")[:200],
            }

        self._set_cached("_tool_catalog", catalog, ttl=86400)  # 24시간 캐시
        logger.info("MCP 도구 카탈로그 로딩: %d개", len(catalog))
        return catalog

    def discover_tools_sync(self) -> Dict[str, Dict]:
        return _run_sync(self.discover_tools())

    async def get_tool_schema(self, tool_name: str) -> Optional[Dict]:
        """특정 도구의 스키마 조회"""
        catalog = await self.discover_tools()
        return catalog.get(tool_name)

    # ══════════════════════════════════════════════════════════════
    # Phase 7: KIS 백테스트 실행 + 결과 수집
    # ══════════════════════════════════════════════════════════════

    async def run_backtest(
        self,
        strategy_id: str,
        symbols: List[str],
        start_date: str = "2025-01-01",
        end_date: str = "2026-01-01",
        initial_capital: float = 10_000_000,
        **kwargs,
    ) -> Optional[str]:
        """KIS MCP 백테스트 실행 → job_id 반환

        Returns:
            job_id 문자열 (폴링용) 또는 None (실패 시)
        """
        args = {
            "strategy_id": strategy_id,
            "symbols": symbols,
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": initial_capital,
            **kwargs,
        }
        try:
            result = await self._call_kis_tool_sse(
                "run_preset_backtest_tool", args
            )
            if result and isinstance(result, dict):
                # JSON-RPC result → content text → inner JSON
                inner = self._extract_kis_content(result)
                if inner and inner.get("success"):
                    job_id = inner["data"].get("job_id")
                    logger.info("백테스트 시작: job_id=%s, strategy=%s", job_id, strategy_id)
                    return job_id
        except Exception as e:
            logger.warning("백테스트 실행 실패: %s", e)
        return None

    async def poll_backtest_result(
        self,
        job_id: str,
        timeout: int = 300,
        interval: int = 5,
    ) -> Dict[str, Any]:
        """job_id 폴링 → completed/failed까지 대기 → 결과 반환

        Args:
            job_id: run_backtest에서 받은 ID
            timeout: 최대 대기 시간 (초)
            interval: 폴링 간격 (초)

        Returns:
            완료 시: {"status": "completed", "result": {metrics, equity_curve, ...}}
            실패 시: {"status": "failed", "error": "..."}
            타임아웃 시: {"status": "timeout"}
        """
        import asyncio as _asyncio

        elapsed = 0
        while elapsed < timeout:
            try:
                result = await self._call_kis_tool_sse(
                    "get_backtest_result_tool", {"job_id": job_id}
                )
                inner = self._extract_kis_content(result)
                if inner:
                    status = inner.get("data", {}).get("status", "")
                    if status == "completed":
                        logger.info("백테스트 완료: job_id=%s", job_id)
                        return inner.get("data", {})
                    if status == "failed":
                        logger.warning("백테스트 실패: %s", inner.get("error", ""))
                        return {"status": "failed", "error": inner.get("error", "")}
            except Exception as e:
                logger.warning("폴링 에러: %s", e)

            await _asyncio.sleep(interval)
            elapsed += interval

        return {"status": "timeout"}

    async def run_and_wait_backtest(
        self,
        strategy_id: str,
        symbols: List[str],
        start_date: str = "2025-01-01",
        end_date: str = "2026-01-01",
        initial_capital: float = 10_000_000,
        timeout: int = 300,
        **kwargs,
    ) -> Dict[str, Any]:
        """실행 + 폴링 + 결과 한 번에 (편의 메서드)"""
        job_id = await self.run_backtest(
            strategy_id, symbols, start_date, end_date, initial_capital, **kwargs
        )
        if not job_id:
            return {"status": "failed", "error": "job_id 미수신"}
        return await self.poll_backtest_result(job_id, timeout=timeout)

    def run_and_wait_backtest_sync(self, **kwargs) -> Dict[str, Any]:
        return _run_sync(self.run_and_wait_backtest(**kwargs))

    # ── KIS SSE 호출 헬퍼 ─────────────────────────────────────

    async def _call_kis_tool_sse(
        self, tool_name: str, arguments: Optional[Dict] = None
    ) -> Optional[Dict]:
        """KIS Backtest MCP 도구 호출 (Streamable HTTP SSE)"""
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
            "id": 1,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        return await self._post_mcp_sse(self._kis_mcp_url, payload, headers)

    @staticmethod
    def _extract_kis_content(result: Optional[Dict]) -> Optional[Dict]:
        """KIS MCP JSON-RPC 응답에서 content text → inner JSON 추출"""
        if not result or not isinstance(result, dict):
            return None
        content_list = result.get("result", {}).get("content", [])
        for item in content_list:
            if item.get("type") == "text":
                try:
                    return _json.loads(item["text"])
                except (_json.JSONDecodeError, KeyError):
                    pass
        return None

    # ══════════════════════════════════════════════════════════════
    # Phase 8: 마이크로구조 + 알파 품질 + 실행 최적화
    # ══════════════════════════════════════════════════════════════

    async def get_micro_toxicity(self, ticker: str) -> Dict[str, Any]:
        """VPIN (Volume-Synchronized Probability of Informed Trading)

        Returns:
            {"vpin": 0.45, "flash_crash_risk": "WARNING", ...} 또는 {}
        """
        cache_key = f"micro_toxicity_{ticker}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            result = await self._call_vps_tool("micro_toxicity", {"stock_code": ticker})
            if result and result.get("success"):
                data = result.get("data", result)
                self._set_cached(cache_key, data, ttl=600)  # 10분 캐시
                return data
        except Exception as e:
            logger.debug("VPIN 조회 실패 (%s): %s", ticker, e)
        return {}

    def get_micro_toxicity_sync(self, ticker: str) -> Dict[str, Any]:
        return _run_sync(self.get_micro_toxicity(ticker))

    async def get_micro_amihud(self, ticker: str) -> Optional[float]:
        """Amihud 비유동성 지표 (높을수록 비유동적)"""
        cache_key = f"micro_amihud_{ticker}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            result = await self._call_vps_tool("micro_amihud", {"stock_code": ticker})
            if result and result.get("success"):
                val = result.get("data", {}).get("amihud_illiquidity")
                if val is not None:
                    val = float(val)
                    self._set_cached(cache_key, val)
                    return val
        except Exception as e:
            logger.debug("Amihud 조회 실패 (%s): %s", ticker, e)
        return None

    def get_micro_amihud_sync(self, ticker: str) -> Optional[float]:
        return _run_sync(self.get_micro_amihud(ticker))

    async def get_micro_kyle_lambda(self, ticker: str) -> Optional[float]:
        """Kyle Lambda: 가격 임팩트 (bps per million won)"""
        cache_key = f"micro_kyle_{ticker}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            result = await self._call_vps_tool("micro_kyle_lambda", {"stock_code": ticker})
            if result and result.get("success"):
                val = result.get("data", {}).get("kyle_lambda")
                if val is not None:
                    val = float(val)
                    self._set_cached(cache_key, val)
                    return val
        except Exception as e:
            logger.debug("Kyle Lambda 조회 실패 (%s): %s", ticker, e)
        return None

    def get_micro_kyle_lambda_sync(self, ticker: str) -> Optional[float]:
        return _run_sync(self.get_micro_kyle_lambda(ticker))

    async def get_alpha_decay(self, ticker: str) -> Dict[str, Any]:
        """알파 반감기 (IC half-life, crowding)"""
        cache_key = f"alpha_decay_{ticker}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            result = await self._call_vps_tool("alpha_decay", {"stock_code": ticker})
            if result and result.get("success"):
                data = result.get("data", result)
                self._set_cached(cache_key, data, ttl=7200)  # 2시간 캐시
                return data
        except Exception as e:
            logger.debug("Alpha Decay 조회 실패 (%s): %s", ticker, e)
        return {}

    def get_alpha_decay_sync(self, ticker: str) -> Dict[str, Any]:
        return _run_sync(self.get_alpha_decay(ticker))

    async def get_alpha_crowding(self, tickers: Sequence[str]) -> Dict[str, float]:
        """팩터 혼잡도 (종목별 crowding percentile)"""
        cache_key = f"alpha_crowding_{','.join(sorted(tickers)[:5])}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            result = await self._call_vps_tool(
                "alpha_crowding", {"tickers": list(tickers)}
            )
            if result and result.get("success"):
                data = result.get("data", {})
                if isinstance(data, dict):
                    self._set_cached(cache_key, data)
                    return data
        except Exception as e:
            logger.debug("Alpha Crowding 조회 실패: %s", e)
        return {}

    def get_alpha_crowding_sync(self, tickers: Sequence[str]) -> Dict[str, float]:
        return _run_sync(self.get_alpha_crowding(tickers))

    async def get_exec_optimal(
        self,
        ticker: str,
        order_size_millions: float,
        time_horizon_hours: int = 4,
    ) -> Dict[str, Any]:
        """Almgren-Chriss 최적 실행 경로"""
        cache_key = f"exec_optimal_{ticker}_{order_size_millions}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            result = await self._call_vps_tool(
                "stochvol_exec_optimal",
                {
                    "stock_code": ticker,
                    "order_size_millions": order_size_millions,
                    "time_horizon_hours": time_horizon_hours,
                },
            )
            if result and result.get("success"):
                data = result.get("data", result)
                self._set_cached(cache_key, data, ttl=300)  # 5분 캐시
                return data
        except Exception as e:
            logger.debug("Almgren-Chriss 조회 실패 (%s): %s", ticker, e)
        return {}

    def get_exec_optimal_sync(
        self, ticker: str, order_size_millions: float, time_horizon_hours: int = 4
    ) -> Dict[str, Any]:
        return _run_sync(
            self.get_exec_optimal(ticker, order_size_millions, time_horizon_hours)
        )

    # ══════════════════════════════════════════════════════════════
    # 업그레이드: 결과 자동 저장 (Karpathy 누적 학습 루프)
    # ══════════════════════════════════════════════════════════════

    def save_result(
        self,
        result: Dict[str, Any],
        category: str = "backtest",
        tag: str = "",
    ) -> Path:
        """분석/백테스트 결과를 JSON으로 자동 저장

        저장 경로: {project_root}/results/{category}/{date}_{tag}.json
        Vault 연동 시 이 파일을 자동으로 인덱싱할 수 있음.
        """
        from datetime import datetime

        results_dir = Path.home() / "Desktop" / "open-trading-api" / "backtester" / "results" / category
        results_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{tag}.json" if tag else f"{timestamp}.json"
        filepath = results_dir / filename

        filepath.write_text(
            _json.dumps(result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("결과 저장: %s", filepath)
        return filepath


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
