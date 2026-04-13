"""
Luxon Terminal — MCP 기술적 지표 → GothamGraph + CatalystTracker 자동 주입.

MCP ta_rsi / ta_macd / ta_bollinger 를 호출해서
과매도/골든크로스/볼린저 하단 돌파 등 실 시장 신호를
EVENT 노드 + CATALYST_FOR 엣지로 GothamGraph 에 주입한다.
동시에 CatalystTracker 에도 등록하여 catalyst_score 에 반영.

신호 → 카탈리스트 매핑:
    RSI < 30          → oversold  (TECHNICAL, impact=+6, prob=0.70)
    RSI > 70          → overbought (TECHNICAL, impact=-6, prob=0.70)
    MACD 골든크로스   → bullish_cross (TECHNICAL, impact=+5, prob=0.60)
    MACD 데드크로스   → bearish_cross (TECHNICAL, impact=-5, prob=0.60)
    가격 < 볼린저 하단 → bb_oversold (TECHNICAL, impact=+5, prob=0.65)
    가격 > 볼린저 상단 → bb_overbought (TECHNICAL, impact=-5, prob=0.65)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from kis_backtest.luxon.graph.edges import EdgeKind, GraphEdge
from kis_backtest.luxon.graph.graph import GothamGraph
from kis_backtest.luxon.graph.nodes import GraphNode, NodeKind, make_node_id
from kis_backtest.portfolio.catalyst_tracker import CatalystTracker, CatalystType

logger = logging.getLogger(__name__)

# MCP 응답 키 탐색 순서 (서버마다 키 이름이 다를 수 있음)
_RSI_KEYS = ("rsi", "RSI", "rsi_14", "value")
_MACD_KEYS = ("macd", "MACD", "macd_line")
_SIGNAL_KEYS = ("signal", "signal_line", "macd_signal")
_PRICE_KEYS = ("close", "price", "current_price", "last_price")
_BB_UPPER_KEYS = ("upper", "upper_band", "bb_upper", "upperband")
_BB_LOWER_KEYS = ("lower", "lower_band", "bb_lower", "lowerband")


def _first(d: Dict, keys: tuple, default: Optional[float] = None) -> Optional[float]:
    """dict에서 첫 번째로 매칭되는 키의 값 반환."""
    for k in keys:
        if k in d:
            try:
                return float(d[k])
            except (TypeError, ValueError):
                pass
    return default


@dataclass
class TASignal:
    """단일 TA 신호 - 그래프 노드 및 카탈리스트로 변환될 단위."""
    name: str
    description: str
    impact: float          # +양수=강세 / -음수=약세
    probability: float     # 0~1
    source: str            # "RSI" | "MACD" | "Bollinger"


class TASignalIngestor:
    """MCP TA 신호 → GothamGraph EventNode + CatalystTracker 이중 주입.

    Args:
        graph: 타깃 GothamGraph.
        tracker: 타깃 CatalystTracker.
    """

    def __init__(self, graph: GothamGraph, tracker: CatalystTracker) -> None:
        self._graph = graph
        self._tracker = tracker

    # ── 공개 API ────────────────────────────────────────────────────

    def ingest_sync(self, mcp: Any, symbols: List[str]) -> Dict[str, List[TASignal]]:
        """동기 래퍼. asyncio loop 상태에 따라 자동 분기."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as exe:
                fut = exe.submit(asyncio.run, self.ingest(mcp, symbols))
                return fut.result()
        else:
            return asyncio.run(self.ingest(mcp, symbols))

    async def ingest(self, mcp: Any, symbols: List[str]) -> Dict[str, List[TASignal]]:
        """모든 종목에 대해 TA 신호 fetch + 그래프/트래커에 주입.

        Returns:
            {symbol: [TASignal, ...]} — 주입된 신호 목록.
        """
        result: Dict[str, List[TASignal]] = {}
        for sym in symbols:
            try:
                signals = await self._fetch_and_parse(mcp, sym)
                if signals:
                    self._inject(sym, signals)
                    result[sym] = signals
                    logger.info("[TA] %s: %d 신호 주입", sym, len(signals))
                else:
                    logger.debug("[TA] %s: 신호 없음 (중립)", sym)
            except Exception as exc:
                logger.warning("[TA] %s 처리 실패: %s", sym, exc)
        return result

    # ── 내부 로직 ────────────────────────────────────────────────────

    async def _fetch_and_parse(self, mcp: Any, symbol: str) -> List[TASignal]:
        """MCP 3종 호출 → TASignal 리스트."""
        signals: List[TASignal] = []

        # 1. RSI
        try:
            rsi_data = await mcp._call_vps_tool("ta_rsi", {"ticker": symbol})
            signals.extend(self._parse_rsi(rsi_data))
        except Exception as e:
            logger.debug("[TA] RSI fetch 실패 %s: %s", symbol, e)

        # 2. MACD
        try:
            macd_data = await mcp._call_vps_tool("ta_macd", {"ticker": symbol})
            signals.extend(self._parse_macd(macd_data))
        except Exception as e:
            logger.debug("[TA] MACD fetch 실패 %s: %s", symbol, e)

        # 3. Bollinger
        try:
            bb_data = await mcp._call_vps_tool("ta_bollinger", {"ticker": symbol})
            signals.extend(self._parse_bollinger(bb_data))
        except Exception as e:
            logger.debug("[TA] Bollinger fetch 실패 %s: %s", symbol, e)

        return signals

    @staticmethod
    def _parse_rsi(data: Dict) -> List[TASignal]:
        """RSI 데이터 → TASignal."""
        rsi = _first(data, _RSI_KEYS)
        if rsi is None:
            # data가 list면 최신 값
            if isinstance(data, list) and data:
                try:
                    rsi = float(data[-1].get("rsi", data[-1].get("value", 0)))
                except Exception:
                    return []
        if rsi is None:
            return []

        if rsi < 30:
            return [TASignal(
                name=f"RSI과매도({rsi:.1f})",
                description=f"RSI={rsi:.1f} < 30: 기술적 과매도 반등 신호",
                impact=+6.0, probability=0.70, source="RSI",
            )]
        if rsi > 70:
            return [TASignal(
                name=f"RSI과매수({rsi:.1f})",
                description=f"RSI={rsi:.1f} > 70: 기술적 과매수 조정 신호",
                impact=-6.0, probability=0.70, source="RSI",
            )]
        # 중립 (30~70): 신호 없음
        return []

    @staticmethod
    def _parse_macd(data: Dict) -> List[TASignal]:
        """MACD 데이터 → TASignal. 골든/데드 크로스 감지."""
        macd = _first(data, _MACD_KEYS)
        signal = _first(data, _SIGNAL_KEYS)
        if macd is None or signal is None:
            return []

        if macd > signal and macd > 0:
            return [TASignal(
                name="MACD골든크로스",
                description=f"MACD={macd:.4f} > Signal={signal:.4f}: 상승 모멘텀",
                impact=+5.0, probability=0.60, source="MACD",
            )]
        if macd < signal and macd < 0:
            return [TASignal(
                name="MACD데드크로스",
                description=f"MACD={macd:.4f} < Signal={signal:.4f}: 하락 모멘텀",
                impact=-5.0, probability=0.60, source="MACD",
            )]
        return []

    @staticmethod
    def _parse_bollinger(data: Dict) -> List[TASignal]:
        """Bollinger Band 데이터 → TASignal. 밴드 이탈 감지."""
        price = _first(data, _PRICE_KEYS)
        upper = _first(data, _BB_UPPER_KEYS)
        lower = _first(data, _BB_LOWER_KEYS)
        if price is None or upper is None or lower is None:
            return []

        if price < lower:
            return [TASignal(
                name="볼린저하단이탈",
                description=f"현가({price:.0f}) < 하단밴드({lower:.0f}): 과매도 반등 기대",
                impact=+5.0, probability=0.65, source="Bollinger",
            )]
        if price > upper:
            return [TASignal(
                name="볼린저상단이탈",
                description=f"현가({price:.0f}) > 상단밴드({upper:.0f}): 과매수 조정 기대",
                impact=-5.0, probability=0.65, source="Bollinger",
            )]
        return []

    def _inject(self, symbol: str, signals: List[TASignal]) -> None:
        """TASignal 리스트 → CatalystTracker + GothamGraph."""
        today = date.today().isoformat()
        sym_id = make_node_id(NodeKind.SYMBOL, symbol)

        # SymbolNode idempotent 생성
        if not self._graph.has_node(sym_id):
            self._graph.add_node(GraphNode(
                node_id=sym_id,
                kind=NodeKind.SYMBOL,
                label=symbol,
                timestamp=datetime.now(),
                payload={"ticker": symbol},
            ))

        for sig in signals:
            # CatalystTracker 등록 (catalyst_score 에 반영)
            try:
                self._tracker.add(
                    symbol=symbol,
                    name=sig.name,
                    catalyst_type=CatalystType.TECHNICAL,
                    expected_date=today,
                    probability=sig.probability,
                    impact=sig.impact,
                    description=sig.description,
                    source=sig.source,
                )
            except Exception as e:
                logger.debug("[TA] CatalystTracker 추가 실패 %s/%s: %s", symbol, sig.name, e)

            # GothamGraph EVENT 노드 주입
            node_id = make_node_id(NodeKind.EVENT, f"ta:{symbol}:{sig.source}:{today}")
            try:
                event_node = GraphNode(
                    node_id=node_id,
                    kind=NodeKind.EVENT,
                    label=f"{sig.source} {sig.name}",
                    timestamp=datetime.now(),
                    payload={
                        "signal": sig.name,
                        "impact": sig.impact,
                        "probability": sig.probability,
                        "description": sig.description,
                        "source": sig.source,
                        "date": today,
                    },
                )
                self._graph.add_node(event_node)

                weight = min(1.0, abs(sig.impact) / 10.0)
                self._graph.add_edge(GraphEdge(
                    source_id=node_id,
                    target_id=sym_id,
                    kind=EdgeKind.CATALYST_FOR,
                    weight=weight,
                    timestamp=datetime.now(),
                    meta={"sign": 1 if sig.impact > 0 else -1, "source": sig.source},
                ))
            except ValueError:
                pass  # 노드 중복 — 멱등 허용
