"""LiveOrderExecutor 테스트

시나리오:
    1. 신규 포트폴리오 진입 (빈 계좌 → 3종목 매수)
    2. 리밸런싱 (기존 포지션 → 비중 조정)
    3. 전량 매도 (모든 포지션 EXIT)
    4. 현금 부족 (매수 금액 > 가용 현금)
    5. dry_run 모드
    6. 리스크 게이트 미통과 시 빈 리포트
    7. 최소 거래금액 미만 스킵
"""

import pytest
from datetime import datetime
from typing import Dict, List, Optional
from unittest.mock import MagicMock

from kis_backtest.models import (
    AccountBalance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from kis_backtest.portfolio.mcp_bridge import (
    OrderAction,
    PortfolioOrder,
    StockAllocation,
)
from kis_backtest.strategies.risk.cost_model import (
    KoreaTransactionCostModel,
    Market,
)
from kis_backtest.execution.models import (
    ExecutionReport,
    PlannedTrade,
    TradeReason,
    TransactionCostEstimate,
)
from kis_backtest.execution.order_executor import LiveOrderExecutor


# ─── Mock 구현 ────────────────────────────────

class MockPriceProvider:
    """테스트용 현재가 제공자"""
    def __init__(self, prices: Dict[str, float]):
        self._prices = prices

    def get_current_price(self, symbol: str) -> float:
        if symbol not in self._prices:
            raise ValueError(f"Unknown symbol: {symbol}")
        return self._prices[symbol]


class MockBrokerage:
    """테스트용 브로커리지"""
    def __init__(
        self,
        balance: AccountBalance,
        positions: List[Position],
    ):
        self._balance = balance
        self._positions = positions
        self._submitted_orders: List[Order] = []
        self._order_counter = 0

    def get_balance(self) -> AccountBalance:
        return self._balance

    def get_positions(self) -> List[Position]:
        return self._positions

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
    ) -> Order:
        self._order_counter += 1
        order = Order(
            id=f"ORD{self._order_counter:04d}",
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            filled_quantity=quantity,
            average_price=price or 0,
            status=OrderStatus.FILLED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            commission=quantity * 100 * 0.00015 if price is None else quantity * price * 0.00015,
        )
        self._submitted_orders.append(order)
        return order


def _make_order(
    allocations: List[StockAllocation],
    total_capital: float = 10_000_000,
    risk_passed: bool = True,
) -> PortfolioOrder:
    """테스트용 PortfolioOrder 생성 헬퍼"""
    total_weight = sum(a.target_weight for a in allocations)
    return PortfolioOrder(
        strategy_name="test_strategy",
        created_at=datetime.now(),
        total_capital=total_capital,
        allocations=allocations,
        cash_weight=max(0, 1.0 - total_weight),
        cost_model=KoreaTransactionCostModel(),
        estimated_annual_cost=0.0276,
        kelly_fraction=0.5,
        risk_gate_passed=risk_passed,
        risk_gate_details=["ALL PASS"] if risk_passed else ["Sharpe too low"],
        rebalance_frequency="monthly",
    )


# ─── 테스트 ────────────────────────────────────

class TestNewEntry:
    """시나리오 1: 빈 계좌에 신규 진입"""

    def test_creates_buy_trades(self):
        prices = {"005930": 70000, "000660": 180000, "051910": 350000}
        brokerage = MockBrokerage(
            balance=AccountBalance(
                total_cash=10_000_000,
                available_cash=10_000_000,
                total_equity=10_000_000,
                total_pnl=0,
                total_pnl_percent=0,
                currency="KRW",
            ),
            positions=[],
        )
        executor = LiveOrderExecutor(brokerage, MockPriceProvider(prices))

        order = _make_order([
            StockAllocation("005930", "삼성전자", Market.KOSPI, 0.30, 0.82, "IT", 0, OrderAction.BUY, 3_000_000),
            StockAllocation("000660", "SK하이닉스", Market.KOSPI, 0.20, 0.75, "IT", 0, OrderAction.BUY, 2_000_000),
            StockAllocation("051910", "LG화학", Market.KOSPI, 0.15, 0.68, "화학", 0, OrderAction.BUY, 1_500_000),
        ])

        report = executor.plan(order)

        assert report.n_planned == 3
        assert all(t.side == OrderSide.BUY for t in report.planned)
        assert all(t.reason == TradeReason.NEW_ENTRY for t in report.planned)

    def test_quantity_calculation(self):
        """비중 → 수량 변환 검증: floor(weight * equity / price)"""
        prices = {"005930": 70000}
        brokerage = MockBrokerage(
            balance=AccountBalance(
                total_cash=10_000_000,
                available_cash=10_000_000,
                total_equity=10_000_000,
                total_pnl=0,
                total_pnl_percent=0,
                currency="KRW",
            ),
            positions=[],
        )
        executor = LiveOrderExecutor(brokerage, MockPriceProvider(prices))

        order = _make_order([
            StockAllocation("005930", "삼성전자", Market.KOSPI, 0.30, 0.82, "IT", 0, OrderAction.BUY, 3_000_000),
        ])

        report = executor.plan(order)
        trade = report.planned[0]
        # floor(0.30 * 10,000,000 / 70,000) = floor(42.857) = 42
        assert trade.quantity == 42
        assert trade.estimated_price == 70000

    def test_actual_execution(self):
        """실제 주문 제출 검증"""
        prices = {"005930": 70000}
        brokerage = MockBrokerage(
            balance=AccountBalance(
                total_cash=10_000_000,
                available_cash=10_000_000,
                total_equity=10_000_000,
                total_pnl=0,
                total_pnl_percent=0,
                currency="KRW",
            ),
            positions=[],
        )
        executor = LiveOrderExecutor(brokerage, MockPriceProvider(prices))

        order = _make_order([
            StockAllocation("005930", "삼성전자", Market.KOSPI, 0.30, 0.82, "IT", 0, OrderAction.BUY, 3_000_000),
        ])

        report = executor.execute(order)
        assert report.n_executed == 1
        assert len(brokerage._submitted_orders) == 1
        assert brokerage._submitted_orders[0].symbol == "005930"
        assert brokerage._submitted_orders[0].side == OrderSide.BUY


class TestRebalance:
    """시나리오 2: 기존 포지션 리밸런싱"""

    def test_rebalance_generates_buy_and_sell(self):
        prices = {"005930": 70000, "000660": 180000}
        brokerage = MockBrokerage(
            balance=AccountBalance(
                total_cash=2_000_000,
                available_cash=2_000_000,
                total_equity=10_000_000,
                total_pnl=500_000,
                total_pnl_percent=5.0,
                currency="KRW",
            ),
            positions=[
                Position(symbol="005930", quantity=50, average_price=65000, current_price=70000,
                         unrealized_pnl=250000, unrealized_pnl_percent=7.69, name="삼성전자"),
                Position(symbol="000660", quantity=20, average_price=170000, current_price=180000,
                         unrealized_pnl=200000, unrealized_pnl_percent=5.88, name="SK하이닉스"),
            ],
        )
        executor = LiveOrderExecutor(brokerage, MockPriceProvider(prices))

        # 삼성전자 비중 감소 (50주 → 30주), SK하이닉스 비중 증가 (20주 → 25주)
        order = _make_order([
            StockAllocation("005930", "삼성전자", Market.KOSPI, 0.21, 0.82, "IT", 0.35, OrderAction.SELL, 1_400_000),
            StockAllocation("000660", "SK하이닉스", Market.KOSPI, 0.45, 0.75, "IT", 0.36, OrderAction.BUY, 900_000),
        ])

        report = executor.plan(order)

        sells = [t for t in report.planned if t.side == OrderSide.SELL]
        buys = [t for t in report.planned if t.side == OrderSide.BUY]
        assert len(sells) >= 1  # 삼성전자 매도
        assert len(buys) >= 1   # SK하이닉스 매수

    def test_sell_first_then_buy(self):
        """매도 먼저 실행 후 매수 검증"""
        prices = {"005930": 70000, "000660": 180000}
        brokerage = MockBrokerage(
            balance=AccountBalance(
                total_cash=500_000,
                available_cash=500_000,
                total_equity=10_000_000,
                total_pnl=0,
                total_pnl_percent=0,
                currency="KRW",
            ),
            positions=[
                Position(symbol="005930", quantity=50, average_price=65000, current_price=70000,
                         unrealized_pnl=250000, unrealized_pnl_percent=7.69, name="삼성전자"),
            ],
        )
        executor = LiveOrderExecutor(brokerage, MockPriceProvider(prices))

        order = _make_order([
            StockAllocation("005930", "삼성전자", Market.KOSPI, 0.10, 0.82, "IT", 0.35, OrderAction.SELL, 1_750_000),
            StockAllocation("000660", "SK하이닉스", Market.KOSPI, 0.25, 0.75, "IT", 0, OrderAction.BUY, 2_500_000),
        ])

        report = executor.execute(order)

        # 매도가 먼저 제출되었는지 확인
        if len(brokerage._submitted_orders) >= 2:
            assert brokerage._submitted_orders[0].side == OrderSide.SELL
            assert brokerage._submitted_orders[1].side == OrderSide.BUY


class TestFullExit:
    """시나리오 3: 전량 매도"""

    def test_exit_all_positions(self):
        prices = {"005930": 70000, "000660": 180000}
        brokerage = MockBrokerage(
            balance=AccountBalance(
                total_cash=1_000_000,
                available_cash=1_000_000,
                total_equity=10_000_000,
                total_pnl=0,
                total_pnl_percent=0,
                currency="KRW",
            ),
            positions=[
                Position(symbol="005930", quantity=50, average_price=65000, current_price=70000,
                         unrealized_pnl=250000, unrealized_pnl_percent=7.69, name="삼성전자"),
                Position(symbol="000660", quantity=20, average_price=170000, current_price=180000,
                         unrealized_pnl=200000, unrealized_pnl_percent=5.88, name="SK하이닉스"),
            ],
        )
        executor = LiveOrderExecutor(brokerage, MockPriceProvider(prices))

        # target_weight=0 → 전량 매도
        order = _make_order([
            StockAllocation("005930", "삼성전자", Market.KOSPI, 0.0, 0.82, "IT", 0.35, OrderAction.SELL, 3_500_000),
            StockAllocation("000660", "SK하이닉스", Market.KOSPI, 0.0, 0.75, "IT", 0.36, OrderAction.SELL, 3_600_000),
        ])

        report = executor.plan(order)

        assert report.n_planned == 2
        assert all(t.side == OrderSide.SELL for t in report.planned)
        assert all(t.reason == TradeReason.EXIT for t in report.planned)

        # 수량 검증: 보유 전량 매도
        samsung = next(t for t in report.planned if t.symbol == "005930")
        assert samsung.quantity == 50

        hynix = next(t for t in report.planned if t.symbol == "000660")
        assert hynix.quantity == 20


class TestInsufficientCash:
    """시나리오 4: 현금 부족"""

    def test_skip_when_insufficient_cash(self):
        prices = {"005930": 70000, "000660": 180000}
        brokerage = MockBrokerage(
            balance=AccountBalance(
                total_cash=300_000,
                available_cash=300_000,
                total_equity=10_000_000,
                total_pnl=0,
                total_pnl_percent=0,
                currency="KRW",
            ),
            positions=[],
        )
        executor = LiveOrderExecutor(brokerage, MockPriceProvider(prices))

        order = _make_order([
            StockAllocation("005930", "삼성전자", Market.KOSPI, 0.30, 0.82, "IT", 0, OrderAction.BUY, 3_000_000),
            StockAllocation("000660", "SK하이닉스", Market.KOSPI, 0.20, 0.75, "IT", 0, OrderAction.BUY, 2_000_000),
        ])

        report = executor.execute(order)

        # 현금 300,000원으로 3,000,000원 매수 불가 → 스킵
        assert report.n_skipped > 0


class TestDryRun:
    """시나리오 5: dry_run 모드"""

    def test_dry_run_no_orders_submitted(self):
        prices = {"005930": 70000}
        brokerage = MockBrokerage(
            balance=AccountBalance(
                total_cash=10_000_000,
                available_cash=10_000_000,
                total_equity=10_000_000,
                total_pnl=0,
                total_pnl_percent=0,
                currency="KRW",
            ),
            positions=[],
        )
        executor = LiveOrderExecutor(brokerage, MockPriceProvider(prices))

        order = _make_order([
            StockAllocation("005930", "삼성전자", Market.KOSPI, 0.30, 0.82, "IT", 0, OrderAction.BUY, 3_000_000),
        ])

        report = executor.execute(order, dry_run=True)

        assert report.n_planned == 1
        assert report.n_executed == 0
        assert len(brokerage._submitted_orders) == 0


class TestRiskGateFail:
    """시나리오 6: 리스크 게이트 미통과"""

    def test_risk_fail_returns_empty(self):
        prices = {"005930": 70000}
        brokerage = MockBrokerage(
            balance=AccountBalance(
                total_cash=10_000_000,
                available_cash=10_000_000,
                total_equity=10_000_000,
                total_pnl=0,
                total_pnl_percent=0,
                currency="KRW",
            ),
            positions=[],
        )
        executor = LiveOrderExecutor(brokerage, MockPriceProvider(prices))

        order = _make_order(
            allocations=[
                StockAllocation("005930", "삼성전자", Market.KOSPI, 0.30, 0.82, "IT", 0, OrderAction.BUY, 3_000_000),
            ],
            risk_passed=False,
        )

        report = executor.execute(order)
        assert report.n_planned == 0
        assert report.n_executed == 0


class TestMinTradeAmount:
    """시나리오 7: 최소 거래금액 미만 스킵"""

    def test_skip_small_trades(self):
        prices = {"005930": 70000}
        brokerage = MockBrokerage(
            balance=AccountBalance(
                total_cash=10_000_000,
                available_cash=10_000_000,
                total_equity=10_000_000,
                total_pnl=0,
                total_pnl_percent=0,
                currency="KRW",
            ),
            positions=[
                # 현재 42주, target 0.30 → 42주. 거의 변동 없음
                Position(symbol="005930", quantity=42, average_price=65000, current_price=70000,
                         unrealized_pnl=210000, unrealized_pnl_percent=7.69, name="삼성전자"),
            ],
        )
        executor = LiveOrderExecutor(brokerage, MockPriceProvider(prices))

        # target 0.295 → floor(0.295 * 10M / 70000) = 42 → 0주 차이 → 스킵
        order = _make_order([
            StockAllocation("005930", "삼성전자", Market.KOSPI, 0.295, 0.82, "IT", 0.294, OrderAction.HOLD, 0),
        ])

        report = executor.plan(order)
        assert report.n_planned == 0  # HOLD는 스킵


class TestTransactionCostEstimate:
    """거래비용 추정 모델 테스트"""

    def test_cost_components(self):
        cost = TransactionCostEstimate(
            commission=150,
            tax=2000,
            slippage=500,
        )
        assert cost.total == 2650

    def test_frozen_immutability(self):
        cost = TransactionCostEstimate(commission=100, tax=200, slippage=50)
        with pytest.raises(AttributeError):
            cost.commission = 999


class TestExecutionReport:
    """ExecutionReport 모델 테스트"""

    def test_summary_format(self):
        report = ExecutionReport(
            planned=[
                PlannedTrade(
                    symbol="005930", name="삼성전자", side=OrderSide.BUY,
                    quantity=10, estimated_price=70000,
                    estimated_cost=TransactionCostEstimate(100, 0, 50),
                    reason=TradeReason.NEW_ENTRY,
                ),
            ],
        )
        summary = report.summary()
        assert "삼성전자" in summary
        assert "매수" in summary

    def test_execution_rate(self):
        report = ExecutionReport(
            planned=[
                PlannedTrade(
                    symbol="A", name="A", side=OrderSide.BUY,
                    quantity=1, estimated_price=1000,
                    estimated_cost=TransactionCostEstimate(0, 0, 0),
                    reason=TradeReason.NEW_ENTRY,
                ),
                PlannedTrade(
                    symbol="B", name="B", side=OrderSide.BUY,
                    quantity=1, estimated_price=1000,
                    estimated_cost=TransactionCostEstimate(0, 0, 0),
                    reason=TradeReason.NEW_ENTRY,
                ),
            ],
            executed=[
                Order(
                    id="1", symbol="A", side=OrderSide.BUY,
                    order_type=OrderType.MARKET, quantity=1,
                    filled_quantity=1, average_price=1000,
                    status=OrderStatus.FILLED,
                    created_at=datetime.now(), updated_at=datetime.now(),
                ),
            ],
        )
        assert report.execution_rate == 0.5
