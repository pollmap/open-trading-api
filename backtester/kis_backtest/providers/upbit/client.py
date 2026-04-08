"""Upbit REST + WebSocket 클라이언트

pyupbit(Apache 2.0) API 구조 참고, httpx 기반 자체 구현.
JWT 인증, rate limit, 에러 핸들링 포함.

Usage:
    # 시세 조회 (인증 불필요)
    client = UpbitClient()
    markets = client.get_markets(fiat="KRW")
    candles = client.get_candles("KRW-BTC", interval="day", count=200)
    ticker = client.get_ticker("KRW-BTC")

    # 주문 실행 (인증 필요)
    client = UpbitClient(access_key="...", secret_key="...")
    accounts = client.get_accounts()
    order = client.buy_limit("KRW-BTC", volume=0.001, price=50000000)
"""

from __future__ import annotations

import hashlib
import json as _json
import logging
import math
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.upbit.com/v1"
WS_URL = "wss://api.upbit.com/websocket/v1"

# Rate limit: 초당 10회 (시세), 초당 8회 (주문)
_RATE_LIMIT_INTERVAL = 0.12  # 초

# 원화 마켓 호가 단위 테이블
_TICK_TABLE = [
    (2_000_000, 1_000),
    (1_000_000, 500),
    (500_000, 100),
    (100_000, 50),
    (10_000, 10),
    (1_000, 1),
    (100, 0.1),
    (10, 0.01),
    (1, 0.001),
    (0.1, 0.0001),
    (0.01, 0.00001),
    (0.001, 0.000001),
    (0.0001, 0.0000001),
    (0, 0.00000001),
]


def get_tick_size(price: float) -> float:
    """원화 마켓 주문 가격 단위 (호가)

    업비트 원화 마켓의 주문 가격은 호가 단위에 맞춰야 함.
    ref: https://docs.upbit.com/docs/market-info-trade-price-detail
    """
    for threshold, tick in _TICK_TABLE:
        if price >= threshold:
            return math.floor(price / tick) * tick
    return price


class UpbitError(Exception):
    """업비트 API 에러"""
    def __init__(self, message: str, code: str = "", status: int = 0):
        super().__init__(message)
        self.code = code
        self.status = status


class UpbitClient:
    """업비트 REST API 클라이언트

    Args:
        access_key: API 접근키 (환경변수 UPBIT_ACCESS_KEY 사용 가능)
        secret_key: API 시크릿키 (환경변수 UPBIT_SECRET_KEY 사용 가능)
        timeout: HTTP 요청 타임아웃 (초)
    """

    def __init__(
        self,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        timeout: float = 10.0,
    ):
        self._access = access_key or os.environ.get("UPBIT_ACCESS_KEY", "")
        self._secret = secret_key or os.environ.get("UPBIT_SECRET_KEY", "")
        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        self._last_request_time: float = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "UpbitClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @property
    def is_authenticated(self) -> bool:
        return bool(self._access and self._secret)

    # ── 시세 API (인증 불필요) ──────────────────────────

    def get_markets(self, fiat: str = "KRW") -> List[Dict[str, Any]]:
        """마켓 목록 조회

        Args:
            fiat: 기준 통화 필터 ("KRW", "BTC", "USDT", "" = 전체)
        """
        from kis_backtest.providers.upbit.models import _parse_market
        data = self._get("/market/all", params={"isDetails": "true"})
        markets = [_parse_market(m) for m in data]
        if fiat:
            markets = [m for m in markets if m.base == fiat]
        return markets

    def get_candles(
        self,
        market: str,
        interval: str = "day",
        count: int = 200,
        to: Optional[str] = None,
    ) -> List["UpbitCandle"]:
        """캔들(OHLCV) 조회

        Args:
            market: 마켓 코드 (예: "KRW-BTC")
            interval: "day", "week", "month", "minute1"~"minute240"
            count: 캔들 수 (최대 200)
            to: 마지막 캔들 시각 (ISO 8601, UTC)
        """
        from kis_backtest.providers.upbit.models import _parse_candle

        url = self._candle_url(interval)
        params: Dict[str, Any] = {"market": market, "count": min(count, 200)}
        if to:
            params["to"] = to

        data = self._get(url, params=params)
        candles = [_parse_candle(c) for c in data]
        candles.reverse()  # 시간순 정렬 (과거 → 최신)
        return candles

    def get_candles_all(
        self,
        market: str,
        interval: str = "day",
        count: int = 1000,
    ) -> List["UpbitCandle"]:
        """대량 캔들 조회 (페이지네이션)

        200개씩 반복 호출하여 원하는 수량만큼 수집.
        """
        all_candles: List = []
        remaining = count
        to_param: Optional[str] = None

        while remaining > 0:
            batch_size = min(remaining, 200)
            batch = self.get_candles(
                market, interval=interval, count=batch_size, to=to_param,
            )
            if not batch:
                break

            all_candles = batch + all_candles  # 시간순 유지
            remaining -= len(batch)

            # 다음 페이지: 가장 오래된 캔들 시각
            to_param = batch[0].candle_date_time_utc
            if len(batch) < batch_size:
                break  # 더 이상 데이터 없음

        return all_candles

    def get_ticker(self, markets: str | List[str]) -> List["UpbitTicker"]:
        """현재가 정보 조회

        Args:
            markets: 마켓 코드 (문자열 또는 리스트)
        """
        from kis_backtest.providers.upbit.models import _parse_ticker
        if isinstance(markets, list):
            markets_str = ",".join(markets)
        else:
            markets_str = markets
        data = self._get("/ticker", params={"markets": markets_str})
        return [_parse_ticker(t) for t in data]

    def get_orderbook(self, markets: str | List[str]) -> List["UpbitOrderbook"]:
        """호가 정보 조회"""
        from kis_backtest.providers.upbit.models import _parse_orderbook
        if isinstance(markets, list):
            markets_str = ",".join(markets)
        else:
            markets_str = markets
        data = self._get("/orderbook", params={"markets": markets_str})
        return [_parse_orderbook(o) for o in data]

    def get_trades(
        self,
        market: str,
        count: int = 50,
    ) -> List["UpbitTrade"]:
        """최근 체결 내역 조회"""
        from kis_backtest.providers.upbit.models import UpbitTrade
        data = self._get("/trades/ticks", params={
            "market": market, "count": min(count, 500),
        })
        return [
            UpbitTrade(
                market=t["market"],
                trade_date_utc=t.get("trade_date_utc", ""),
                trade_time_utc=t.get("trade_time_utc", ""),
                trade_price=float(t.get("trade_price", 0)),
                trade_volume=float(t.get("trade_volume", 0)),
                ask_bid=t.get("ask_bid", ""),
                sequential_id=t.get("sequential_id", 0),
                timestamp=t.get("timestamp", 0),
            )
            for t in data
        ]

    # ── 주문 API (인증 필요) ──────────────────────────

    def get_accounts(self) -> List["UpbitAccount"]:
        """전체 계좌 조회"""
        from kis_backtest.providers.upbit.models import _parse_account
        self._require_auth()
        data = self._get("/accounts", auth=True)
        return [_parse_account(a) for a in data]

    def get_krw_balance(self) -> float:
        """KRW 가용 잔고"""
        accounts = self.get_accounts()
        for acc in accounts:
            if acc.currency == "KRW":
                return acc.available
        return 0.0

    def buy_limit(
        self,
        market: str,
        volume: float,
        price: float,
    ) -> "UpbitOrder":
        """지정가 매수

        Args:
            market: 마켓 코드 (예: "KRW-BTC")
            volume: 매수 수량
            price: 매수 가격 (호가 단위 자동 조정)
        """
        adjusted_price = get_tick_size(price) if market.startswith("KRW-") else price
        return self._place_order(
            market=market,
            side="bid",
            ord_type="limit",
            volume=str(volume),
            price=str(adjusted_price),
        )

    def buy_market(self, market: str, total_amount: float) -> "UpbitOrder":
        """시장가 매수 (총액 기준)

        Args:
            market: 마켓 코드
            total_amount: 매수 총액 (원)
        """
        return self._place_order(
            market=market,
            side="bid",
            ord_type="price",
            price=str(total_amount),
        )

    def sell_limit(
        self,
        market: str,
        volume: float,
        price: float,
    ) -> "UpbitOrder":
        """지정가 매도"""
        adjusted_price = get_tick_size(price) if market.startswith("KRW-") else price
        return self._place_order(
            market=market,
            side="ask",
            ord_type="limit",
            volume=str(volume),
            price=str(adjusted_price),
        )

    def sell_market(self, market: str, volume: float) -> "UpbitOrder":
        """시장가 매도 (수량 기준)"""
        return self._place_order(
            market=market,
            side="ask",
            ord_type="market",
            volume=str(volume),
        )

    def cancel_order(self, order_uuid: str) -> "UpbitOrder":
        """주문 취소"""
        from kis_backtest.providers.upbit.models import _parse_order
        self._require_auth()
        data = self._delete("/order", params={"uuid": order_uuid})
        return _parse_order(data)

    def get_order(self, order_uuid: str) -> "UpbitOrder":
        """주문 상세 조회"""
        from kis_backtest.providers.upbit.models import _parse_order
        self._require_auth()
        data = self._get("/order", params={"uuid": order_uuid}, auth=True)
        return _parse_order(data)

    def get_orders(
        self,
        market: Optional[str] = None,
        state: str = "wait",
    ) -> List["UpbitOrder"]:
        """주문 목록 조회"""
        from kis_backtest.providers.upbit.models import _parse_order
        self._require_auth()
        params: Dict[str, Any] = {"state": state}
        if market:
            params["market"] = market
        data = self._get("/orders", params=params, auth=True)
        return [_parse_order(o) for o in data]

    # ── 내부 메서드 ──────────────────────────────────

    def _place_order(self, **params: Any) -> "UpbitOrder":
        from kis_backtest.providers.upbit.models import _parse_order
        self._require_auth()
        # None 값 제거
        body = {k: v for k, v in params.items() if v is not None}
        data = self._post("/orders", body=body)
        return _parse_order(data)

    def _require_auth(self) -> None:
        if not self.is_authenticated:
            raise UpbitError(
                "인증 정보 필요: access_key/secret_key 또는 "
                "UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY 환경변수 설정",
                code="auth_required",
            )

    def _auth_headers(self, query: Optional[Dict] = None) -> Dict[str, str]:
        """JWT 인증 헤더 생성"""
        try:
            import jwt as pyjwt
        except ImportError:
            raise UpbitError(
                "PyJWT 패키지 필요: pip install PyJWT",
                code="missing_dependency",
            )

        payload: Dict[str, Any] = {
            "access_key": self._access,
            "nonce": str(uuid.uuid4()),
        }

        if query:
            query_string = urlencode(query, doseq=True)
            m = hashlib.sha512()
            m.update(query_string.encode())
            payload["query_hash"] = m.hexdigest()
            payload["query_hash_alg"] = "SHA512"

        token = pyjwt.encode(payload, self._secret, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    def _rate_limit(self) -> None:
        """간단한 rate limit (초당 ~8회)"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < _RATE_LIMIT_INTERVAL:
            time.sleep(_RATE_LIMIT_INTERVAL - elapsed)
        self._last_request_time = time.time()

    def _get(
        self,
        path: str,
        params: Optional[Dict] = None,
        auth: bool = False,
    ) -> Any:
        self._rate_limit()
        headers = self._auth_headers(params) if auth else {}
        resp = self._client.get(path, params=params, headers=headers)
        return self._handle_response(resp)

    def _post(self, path: str, body: Dict) -> Any:
        self._rate_limit()
        headers = self._auth_headers(body)
        resp = self._client.post(path, json=body, headers=headers)
        return self._handle_response(resp)

    def _delete(self, path: str, params: Dict) -> Any:
        self._rate_limit()
        headers = self._auth_headers(params)
        resp = self._client.delete(path, params=params, headers=headers)
        return self._handle_response(resp)

    def _handle_response(self, resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            try:
                err = resp.json()
                error_info = err.get("error", {})
                raise UpbitError(
                    message=error_info.get("message", resp.text),
                    code=error_info.get("name", "unknown"),
                    status=resp.status_code,
                )
            except (ValueError, KeyError):
                raise UpbitError(
                    message=resp.text,
                    code="http_error",
                    status=resp.status_code,
                )
        return resp.json()

    @staticmethod
    def _candle_url(interval: str) -> str:
        """인터벌에 맞는 캔들 API URL"""
        mapping = {
            "day": "/candles/days",
            "days": "/candles/days",
            "week": "/candles/weeks",
            "weeks": "/candles/weeks",
            "month": "/candles/months",
            "months": "/candles/months",
        }
        if interval in mapping:
            return mapping[interval]

        # minuteN 형태
        for prefix in ("minute", "minutes"):
            if interval.startswith(prefix):
                n = interval[len(prefix):]
                if n.isdigit() and int(n) in (1, 3, 5, 10, 15, 30, 60, 240):
                    return f"/candles/minutes/{n}"

        return "/candles/days"  # fallback
