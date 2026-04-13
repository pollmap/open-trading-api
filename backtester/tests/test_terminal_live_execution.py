"""LuxonTerminal STEP 2 — paper_mode=False 경로 테스트.

OrchestrationReport → PortfolioOrder 어댑터 + LiveOrderExecutor 연동.
실제 KIS API를 호출하지 않도록 fake brokerage/price_provider 주입.
"""
from __future__ import annotations

from datetime import datetime
from typing import List

import pytest

from kis_backtest.luxon.terminal import (
    LuxonTerminal,
    TerminalConfig,
    _KISPriceAdapter,
    _orch_to_portfolio_order,
)
from kis_backtest.luxon.orchestrator import OrchestrationReport
from kis_backtest.portfolio.ackman_druckenmiller import (
    InvestmentDecision,
    PortfolioDecision,
)
from kis_backtest.portfolio.conviction_sizer import PositionSize
from kis_backtest.portfolio.macro_regime import Regime
from kis_backtest.portfolio.mcp_bridge import OrderAction, PortfolioOrder
from kis_backtest.models import (
    AccountBalance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Quote,
)
from kis_backtest.execution.order_executor import LiveOrderExecutor


# ── Fixtures ────────────────────────────────────────────────────────


def _make_orch_report(
    decisions: List[InvestmentDecision],
    sizes: List[PositionSize],
) -> OrchestrationReport:
    pd = PortfolioDecision(
        regime=Regime.EXPANSION,
        regime_confidence=0.8,
        decisions=decisions,
        total_equity_weight=sum(d.final_weight for d in decisions),
        cash_weight=max(0.0, 1.0 - sum(d.final_weight for d in decisions)),
    )
    return OrchestrationReport(
        regime="expansion",
        regime_confidence=0.8,
        portfolio=pd,
        position_sizes=sizes,
    )


class FakeBrokerage:
    def __init__(self) -> None:
        self.submitted: List[dict] = []

    def get_balance(self) -> AccountBalance:
        return AccountBalance(
            total_cash=10_000_000,
            available_cash=10_000_000,
            total_equity=10_000_000,
            total_pnl=0,
            total_pnl_percent=0,
            currency="KRW",
        )

    def get_positions(self) -> List[Position]:
        return []

    def submit_order(self, symbol, side, quantity, order_type, price):
        self.submitted.append({
            "symbol": symbol, "side": side, "quantity": quantity,
        })
        return Order(
            id=f"FAKE-{len(self.submitted):04d}",
            symbol=symbol, side=side, order_type=order_type,
            quantity=quantity, price=price,
            filled_quantity=quantity, average_price=price or 70_000,
            status=OrderStatus.FILLED,
            created_at=datetime.now(), updated_at=datetime.now(),
            commission=100.0,
        )


class FakePriceProvider:
    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices

    def get_current_price(self, symbol: str) -> float:
        return self._prices.get(symbol, 0.0)


# ── _orch_to_portfolio_order 변환 테스트 ───────────────────────────


def test_orch_to_portfolio_order_basic():
    decisions = [
        InvestmentDecision(
            symbol="005930", action="buy", conviction=8.0,
            catalyst_score=0.7, regime="expansion",
            regime_weight_adjustment=1.2, final_weight=0.15,
        ),
        InvestmentDecision(
            symbol="000660", action="hold", conviction=5.0,
            catalyst_score=0.3, regime="expansion",
            regime_weight_adjustment=1.0, final_weight=0.10,
        ),
    ]
    sizes = [
        PositionSize(symbol="005930", conviction=8.0, weight=0.15,
                     amount=1_500_000, kelly_raw=0.18, capped=False),
        PositionSize(symbol="000660", conviction=5.0, weight=0.10,
                     amount=1_000_000, kelly_raw=0.12, capped=False),
    ]
    orch = _make_orch_report(decisions, sizes)

    order = _orch_to_portfolio_order(orch, capital=10_000_000)

    assert isinstance(order, PortfolioOrder)
    assert len(order.allocations) == 2
    assert order.total_capital == 10_000_000
    assert order.risk_gate_passed is True

    # 005930 → BUY, 000660 → HOLD
    alloc_map = {a.ticker: a for a in order.allocations}
    assert alloc_map["005930"].action == OrderAction.BUY
    assert alloc_map["005930"].target_weight == 0.15
    assert alloc_map["000660"].action == OrderAction.HOLD


def test_orch_to_portfolio_order_handles_invalid_action():
    """action="skip" 같이 OrderAction에 없는 값은 HOLD로 폴백."""
    decisions = [
        InvestmentDecision(
            symbol="005930", action="skip", conviction=3.0,
            catalyst_score=0.0, regime="contraction",
            regime_weight_adjustment=0.5, final_weight=0.0,
        ),
    ]
    sizes = [
        PositionSize(symbol="005930", conviction=3.0, weight=0.0,
                     amount=0, kelly_raw=0.0, capped=False),
    ]
    orch = _make_orch_report(decisions, sizes)

    order = _orch_to_portfolio_order(orch, capital=1_000_000)
    assert order.allocations[0].action == OrderAction.HOLD


# ── _KISPriceAdapter ────────────────────────────────────────────────


def test_kis_price_adapter_mid_price():
    class _FakeData:
        def get_quote(self, symbol):
            return Quote(time=datetime.now(), bid_price=70_000,
                         bid_size=10, ask_price=70_100, ask_size=10)

    adapter = _KISPriceAdapter(_FakeData())
    assert adapter.get_current_price("005930") == 70_050.0


def test_kis_price_adapter_swallows_errors():
    class _FakeData:
        def get_quote(self, symbol):
            raise RuntimeError("KIS API down")

    adapter = _KISPriceAdapter(_FakeData())
    assert adapter.get_current_price("005930") == 0.0


# ── LuxonTerminal paper_mode=False 경로 ────────────────────────────


def test_terminal_live_execute_routes_to_executor(monkeypatch, tmp_path):
    """paper_mode=False이면 LiveOrderExecutor.execute()가 호출됨."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    term = LuxonTerminal(
        symbols=["005930"],
        capital=10_000_000,
        paper_mode=False,
    )
    term._cycle_num = 1

    brokerage = FakeBrokerage()
    price_provider = FakePriceProvider({"005930": 70_000})
    term._live_executor = LiveOrderExecutor(
        brokerage=brokerage, price_provider=price_provider,
    )

    decisions = [InvestmentDecision(
        symbol="005930", action="buy", conviction=8.0,
        catalyst_score=0.7, regime="expansion",
        regime_weight_adjustment=1.2, final_weight=0.15,
    )]
    sizes = [PositionSize(
        symbol="005930", conviction=8.0, weight=0.15,
        amount=1_500_000, kelly_raw=0.18, capped=False,
    )]
    orch = _make_orch_report(decisions, sizes)

    term._live_execute(orch, decisions=[{"symbol": "005930", "action": "buy"}])

    # 주문 제출됨
    assert len(brokerage.submitted) == 1
    assert brokerage.submitted[0]["symbol"] == "005930"
    assert brokerage.submitted[0]["side"] == OrderSide.BUY

    # fills/live/ 경로에 기록됨
    live_dir = tmp_path / ".luxon" / "fills" / "live"
    assert live_dir.exists()
    files = list(live_dir.glob("cycle_0001_*.json"))
    assert len(files) == 1

    import json
    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["cycle_num"] == 1
    assert record["regime"] == "expansion"
    assert len(record["executed"]) == 1


def test_terminal_paper_mode_true_skips_live_executor():
    """paper_mode=True이면 _live_executor 초기화 안 함."""
    term = LuxonTerminal(symbols=["005930"], paper_mode=True)
    # boot() 없이도 live_executor 기본값은 None
    assert term._live_executor is None


def test_terminal_config_kis_paper_default():
    """kis_paper 기본값 True (모의투자 API)."""
    cfg = TerminalConfig(symbols=["005930"])
    assert cfg.kis_paper is True
    assert cfg.paper_mode is True
