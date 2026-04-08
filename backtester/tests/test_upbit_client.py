"""Upbit 클라이언트 테스트

실제 API 호출 없이 단위 테스트 (mock 기반).
실제 API 호출 테스트는 @pytest.mark.integration으로 분리.
"""

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from kis_backtest.providers.upbit.client import (
    UpbitClient,
    UpbitError,
    get_tick_size,
    _RATE_LIMIT_INTERVAL,
)
from kis_backtest.providers.upbit.models import (
    UpbitMarket,
    UpbitCandle,
    UpbitTicker,
    UpbitOrderbook,
    UpbitOrderbookUnit,
    UpbitTrade,
    UpbitAccount,
    UpbitOrder,
    UpbitOrderSide,
    UpbitOrderType,
    UpbitOrderState,
    _parse_market,
    _parse_candle,
    _parse_ticker,
    _parse_orderbook,
    _parse_account,
    _parse_order,
)


# ── 호가 단위 테스트 ────────────────────────────────────

class TestTickSize:
    def test_high_price(self):
        assert get_tick_size(3_500_000) == 3_500_000

    def test_mid_price(self):
        result = get_tick_size(150_000)
        assert result % 50 == 0

    def test_low_price(self):
        result = get_tick_size(500)
        assert result == 500

    def test_very_low_price(self):
        result = get_tick_size(0.05)
        assert result > 0

    def test_exact_boundary(self):
        result = get_tick_size(2_000_000)
        assert result % 1_000 == 0


# ── 모델 파싱 테스트 ────────────────────────────────────

class TestParseMarket:
    def test_basic(self):
        data = {
            "market": "KRW-BTC",
            "korean_name": "비트코인",
            "english_name": "Bitcoin",
            "market_warning": "NONE",
        }
        m = _parse_market(data)
        assert isinstance(m, UpbitMarket)
        assert m.market == "KRW-BTC"
        assert m.base == "KRW"
        assert m.coin == "BTC"

    def test_minimal(self):
        data = {"market": "USDT-ETH"}
        m = _parse_market(data)
        assert m.base == "USDT"
        assert m.coin == "ETH"


class TestParseCandle:
    def test_basic(self):
        data = {
            "market": "KRW-BTC",
            "candle_date_time_utc": "2026-04-07T00:00:00",
            "candle_date_time_kst": "2026-04-07T09:00:00",
            "opening_price": 50000000,
            "high_price": 51000000,
            "low_price": 49000000,
            "trade_price": 50500000,
            "candle_acc_trade_volume": 1234.5,
            "candle_acc_trade_price": 62000000000,
        }
        c = _parse_candle(data)
        assert c.close == 50500000
        assert c.volume == 1234.5


class TestParseTicker:
    def test_basic(self):
        data = {
            "market": "KRW-BTC",
            "trade_price": 50000000,
            "prev_closing_price": 49000000,
            "change": "RISE",
            "change_price": 1000000,
            "change_rate": 0.0204,
            "acc_trade_volume_24h": 5000,
            "acc_trade_price_24h": 250000000000,
        }
        t = _parse_ticker(data)
        assert t.price == 50000000
        assert t.change == "RISE"


class TestParseOrderbook:
    def test_basic(self):
        data = {
            "market": "KRW-BTC",
            "timestamp": 1712500000000,
            "total_ask_size": 10.5,
            "total_bid_size": 12.3,
            "orderbook_units": [
                {
                    "ask_price": 50100000,
                    "bid_price": 50000000,
                    "ask_size": 1.5,
                    "bid_size": 2.0,
                },
                {
                    "ask_price": 50200000,
                    "bid_price": 49900000,
                    "ask_size": 0.8,
                    "bid_size": 1.2,
                },
            ],
        }
        ob = _parse_orderbook(data)
        assert ob.best_ask == 50100000
        assert ob.best_bid == 50000000
        assert ob.spread == 100000
        assert len(ob.orderbook_units) == 2


class TestParseAccount:
    def test_krw_account(self):
        data = {
            "currency": "KRW",
            "balance": "1000000",
            "locked": "50000",
            "avg_buy_price": "0",
        }
        a = _parse_account(data)
        assert a.currency == "KRW"
        assert a.available == 950000
        assert a.total_value == 1000000

    def test_coin_account(self):
        data = {
            "currency": "BTC",
            "balance": "0.5",
            "locked": "0.1",
            "avg_buy_price": "50000000",
        }
        a = _parse_account(data)
        assert a.available == 0.4
        assert a.total_value == 25000000


class TestParseOrder:
    def test_basic(self):
        data = {
            "uuid": "test-uuid-123",
            "side": "bid",
            "ord_type": "limit",
            "price": "50000000",
            "state": "done",
            "market": "KRW-BTC",
            "volume": "0.001",
            "remaining_volume": "0",
            "executed_volume": "0.001",
            "trades_count": 1,
            "created_at": "2026-04-07T10:00:00+09:00",
        }
        o = _parse_order(data)
        assert o.side == UpbitOrderSide.BID
        assert o.is_filled
        assert o.fill_rate == 1.0

    def test_partial_fill(self):
        data = {
            "uuid": "test-uuid-456",
            "side": "ask",
            "ord_type": "limit",
            "price": "50000000",
            "state": "wait",
            "market": "KRW-BTC",
            "volume": "1.0",
            "remaining_volume": "0.5",
            "executed_volume": "0.5",
            "trades_count": 2,
            "created_at": "2026-04-07T10:00:00+09:00",
        }
        o = _parse_order(data)
        assert not o.is_filled
        assert o.fill_rate == 0.5


# ── 클라이언트 테스트 ────────────────────────────────────

class TestClientInit:
    def test_default_no_auth(self):
        client = UpbitClient()
        assert not client.is_authenticated
        client.close()

    def test_with_keys(self):
        client = UpbitClient(access_key="test", secret_key="secret")
        assert client.is_authenticated
        client.close()

    def test_context_manager(self):
        with UpbitClient() as client:
            assert not client.is_authenticated

    def test_require_auth_raises(self):
        client = UpbitClient()
        with pytest.raises(UpbitError, match="인증 정보 필요"):
            client._require_auth()
        client.close()


class TestClientCandleUrl:
    def test_day(self):
        assert UpbitClient._candle_url("day") == "/candles/days"

    def test_week(self):
        assert UpbitClient._candle_url("week") == "/candles/weeks"

    def test_month(self):
        assert UpbitClient._candle_url("month") == "/candles/months"

    def test_minute1(self):
        assert UpbitClient._candle_url("minute1") == "/candles/minutes/1"

    def test_minute240(self):
        assert UpbitClient._candle_url("minute240") == "/candles/minutes/240"

    def test_fallback(self):
        assert UpbitClient._candle_url("invalid") == "/candles/days"


class TestClientMocked:
    """HTTP 요청을 mock하여 클라이언트 로직 테스트"""

    @pytest.fixture
    def client(self):
        c = UpbitClient()
        c._last_request_time = 0  # rate limit 무시
        yield c
        c.close()

    def test_get_markets(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "market": "KRW-BTC",
                "korean_name": "비트코인",
                "english_name": "Bitcoin",
                "market_warning": "NONE",
            },
            {
                "market": "KRW-ETH",
                "korean_name": "이더리움",
                "english_name": "Ethereum",
                "market_warning": "NONE",
            },
            {
                "market": "BTC-XRP",
                "korean_name": "리플",
                "english_name": "Ripple",
                "market_warning": "NONE",
            },
        ]

        with patch.object(client._client, "get", return_value=mock_response):
            markets = client.get_markets(fiat="KRW")
            assert len(markets) == 2
            assert all(m.base == "KRW" for m in markets)

    def test_get_candles(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "market": "KRW-BTC",
                "candle_date_time_utc": "2026-04-07T00:00:00",
                "candle_date_time_kst": "2026-04-07T09:00:00",
                "opening_price": 50000000,
                "high_price": 51000000,
                "low_price": 49000000,
                "trade_price": 50500000,
                "candle_acc_trade_volume": 100,
                "candle_acc_trade_price": 5000000000,
            },
        ]

        with patch.object(client._client, "get", return_value=mock_response):
            candles = client.get_candles("KRW-BTC", count=1)
            assert len(candles) == 1
            assert candles[0].close == 50500000

    def test_get_ticker(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "market": "KRW-BTC",
                "trade_price": 50000000,
                "prev_closing_price": 49000000,
                "change": "RISE",
                "change_price": 1000000,
                "change_rate": 0.0204,
                "acc_trade_volume_24h": 5000,
                "acc_trade_price_24h": 250000000000,
            },
        ]

        with patch.object(client._client, "get", return_value=mock_response):
            tickers = client.get_ticker("KRW-BTC")
            assert len(tickers) == 1
            assert tickers[0].price == 50000000

    def test_handle_error_response(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "error": {
                "name": "invalid_parameter",
                "message": "잘못된 파라미터",
            }
        }

        with patch.object(client._client, "get", return_value=mock_response):
            with pytest.raises(UpbitError) as exc_info:
                client._get("/test")
            assert exc_info.value.code == "invalid_parameter"
            assert exc_info.value.status == 400


class TestUpbitError:
    def test_basic(self):
        err = UpbitError("test error", code="test_code", status=400)
        assert str(err) == "test error"
        assert err.code == "test_code"
        assert err.status == 400


# ── WebSocket 테스트 (모듈 임포트만) ────────────────────

class TestWebSocketImport:
    def test_import(self):
        from kis_backtest.providers.upbit.websocket import UpbitWebSocket
        ws = UpbitWebSocket()
        assert ws._max_reconnects == 10

    def test_custom_config(self):
        from kis_backtest.providers.upbit.websocket import UpbitWebSocket
        ws = UpbitWebSocket(
            ping_interval=30,
            reconnect_delay=1.0,
            max_reconnects=5,
        )
        assert ws._max_reconnects == 5
