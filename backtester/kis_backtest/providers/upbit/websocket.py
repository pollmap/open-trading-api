"""Upbit WebSocket 클라이언트

실시간 체결, 호가, 현재가 스트리밍.
pyupbit WebSocketManager 패턴 참고, asyncio 기반.

Usage:
    import asyncio
    from kis_backtest.providers.upbit.websocket import UpbitWebSocket

    async def main():
        ws = UpbitWebSocket()
        async for msg in ws.subscribe(
            type="ticker",
            codes=["KRW-BTC", "KRW-ETH"],
        ):
            print(f"{msg['code']}: {msg['trade_price']}")

    asyncio.run(main())
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)

WS_URL = "wss://api.upbit.com/websocket/v1"


class UpbitWebSocket:
    """업비트 WebSocket 클라이언트 (asyncio)

    구독 타입:
        - "ticker": 현재가
        - "trade": 체결
        - "orderbook": 호가
    """

    def __init__(
        self,
        ping_interval: int = 60,
        reconnect_delay: float = 3.0,
        max_reconnects: int = 10,
    ):
        self._ping_interval = ping_interval
        self._reconnect_delay = reconnect_delay
        self._max_reconnects = max_reconnects

    async def subscribe(
        self,
        type: str,
        codes: List[str],
        is_only_realtime: bool = True,
    ) -> AsyncIterator[Dict[str, Any]]:
        """WebSocket 구독 (async generator)

        Args:
            type: "ticker", "trade", "orderbook"
            codes: 마켓 코드 리스트 ["KRW-BTC", "KRW-ETH"]
            is_only_realtime: True면 실시간 데이터만

        Yields:
            Dict: 수신된 메시지 (JSON)
        """
        try:
            import websockets
        except ImportError:
            raise ImportError("websockets 패키지 필요: pip install websockets")

        reconnect_count = 0

        while reconnect_count < self._max_reconnects:
            try:
                async for ws in websockets.connect(
                    WS_URL,
                    ping_interval=self._ping_interval,
                ):
                    try:
                        # 구독 메시지 전송
                        subscribe_msg = [
                            {"ticket": str(uuid.uuid4())[:8]},
                            {
                                "type": type,
                                "codes": codes,
                                "isOnlyRealtime": is_only_realtime,
                            },
                        ]
                        await ws.send(json.dumps(subscribe_msg))
                        logger.info(
                            "WebSocket 구독 시작: type=%s, codes=%s",
                            type, codes,
                        )
                        reconnect_count = 0  # 연결 성공 시 리셋

                        while True:
                            raw = await ws.recv()
                            if isinstance(raw, bytes):
                                raw = raw.decode("utf-8")
                            data = json.loads(raw)
                            yield data

                    except websockets.ConnectionClosed as e:
                        logger.warning("WebSocket 연결 끊김: %s", e)
                        reconnect_count += 1
                        await asyncio.sleep(self._reconnect_delay)
                        continue

            except Exception as e:
                logger.error("WebSocket 에러: %s", e)
                reconnect_count += 1
                if reconnect_count < self._max_reconnects:
                    await asyncio.sleep(self._reconnect_delay * reconnect_count)

        logger.error("최대 재연결 횟수 초과 (%d)", self._max_reconnects)

    async def get_tickers_stream(
        self,
        codes: List[str],
        duration_seconds: float = 0,
    ) -> List[Dict[str, Any]]:
        """현재가 스트림에서 지정 시간만큼 수집

        Args:
            codes: 마켓 코드 리스트
            duration_seconds: 수집 시간 (0이면 첫 메시지만)

        Returns:
            수집된 ticker 메시지 리스트
        """
        collected: List[Dict[str, Any]] = []
        start = asyncio.get_event_loop().time()

        async for msg in self.subscribe("ticker", codes):
            collected.append(msg)
            if duration_seconds <= 0:
                break
            if asyncio.get_event_loop().time() - start > duration_seconds:
                break

        return collected
