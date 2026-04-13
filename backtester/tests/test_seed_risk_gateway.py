"""SEED 단계 리스크 게이트 강화 테스트 (v0.9 STEP 5).

Plan 5-1: SEED 단계에서 종목당 max 5%, 섹터 max 20%.
"""
from __future__ import annotations

import pytest

from kis_backtest.core.pipeline import PipelineResult
from kis_backtest.execution.risk_gateway import (
    RiskGateway,
    SEED_MAX_SECTOR_WEIGHT,
    SEED_MAX_SYMBOL_WEIGHT,
)
from kis_backtest.execution.models import PlannedTrade, TradeReason, TransactionCostEstimate
from kis_backtest.models import AccountBalance, OrderSide


def _make_trade(symbol: str, name: str, amount: float) -> PlannedTrade:
    price = 10_000
    qty = max(1, int(amount / price))
    return PlannedTrade(
        symbol=symbol, name=name, side=OrderSide.BUY,
        quantity=qty, estimated_price=price,
        estimated_cost=TransactionCostEstimate(commission=0, tax=0, slippage=0),
        reason=TradeReason.NEW_ENTRY,
        target_weight=0.05, current_weight=0.0,
    )


def _balance(equity: float) -> AccountBalance:
    return AccountBalance(
        total_cash=equity, available_cash=equity, total_equity=equity,
        total_pnl=0, total_pnl_percent=0, currency="KRW",
    )


def _passing_pipeline() -> PipelineResult:
    return PipelineResult(
        order=None, risk_passed=True, risk_details=[], vol_adjustments={},
        turb_index=0.0, dd_state=None, estimated_annual_cost=0.0,
        kelly_allocation=1.0,
    )


# ── 종목 집중도 (Gate 8) ───────────────────────────────────────


def test_symbol_weight_within_limit_passes():
    """종목 비중 4% < 5% → 통과."""
    gateway = RiskGateway(
        mode="paper",
        max_symbol_weight=0.05,
        require_market_hours=False,
    )
    trades = [_make_trade("005930", "삼성전자", 400_000)]
    decision = gateway.check(trades, _balance(10_000_000), _passing_pipeline())
    assert decision.approved
    assert any("종목 집중도 OK" in c for c in decision.checks)


def test_symbol_weight_exceeds_limit_blocked():
    """종목 비중 7% > 5% → 차단."""
    gateway = RiskGateway(
        mode="paper",
        max_symbol_weight=0.05,
        require_market_hours=False,
    )
    trades = [_make_trade("005930", "삼성전자", 700_000)]
    decision = gateway.check(trades, _balance(10_000_000), _passing_pipeline())
    assert not decision.approved
    assert any("종목 집중도 초과" in c for c in decision.checks)


def test_symbol_weight_none_means_skipped():
    """max_symbol_weight=None (PAPER) → 체크 스킵."""
    gateway = RiskGateway(
        mode="paper",
        max_symbol_weight=None,
        require_market_hours=False,
    )
    trades = [_make_trade("005930", "삼성전자", 5_000_000)]  # 50%
    decision = gateway.check(trades, _balance(10_000_000), _passing_pipeline())
    # 50%는 단일주문 30% 상한에 걸리지만 종목 집중도 체크는 스킵
    assert any("종목 집중도 체크 스킵" in c for c in decision.checks)


# ── 섹터 집중도 (Gate 9) ───────────────────────────────────────


def test_sector_weight_within_limit_passes():
    """반도체 2종목 합쳐 15% < 20% → 통과."""
    gateway = RiskGateway(
        mode="paper",
        max_symbol_weight=0.08,  # 종목 개별은 각 7.5%로 통과시키기 위해 상한 완화
        max_sector_weight=0.20,
        sector_map={"005930": "반도체", "000660": "반도체"},
        require_market_hours=False,
    )
    trades = [
        _make_trade("005930", "삼성전자", 750_000),   # 7.5%
        _make_trade("000660", "SK하이닉스", 750_000),  # 7.5%
    ]
    decision = gateway.check(trades, _balance(10_000_000), _passing_pipeline())
    assert decision.approved, decision.summary()


def test_sector_weight_exceeds_limit_blocked():
    """반도체 3종목 합 25% > 20% → 섹터 차단."""
    gateway = RiskGateway(
        mode="paper",
        max_symbol_weight=0.10,
        max_sector_weight=0.20,
        sector_map={
            "005930": "반도체", "000660": "반도체", "042700": "반도체",
        },
        require_market_hours=False,
    )
    trades = [
        _make_trade("005930", "삼성전자", 800_000),
        _make_trade("000660", "SK하이닉스", 800_000),
        _make_trade("042700", "한미반도체", 900_000),
    ]
    decision = gateway.check(trades, _balance(10_000_000), _passing_pipeline())
    assert not decision.approved
    assert any("섹터 집중도 초과" in c for c in decision.checks)


def test_sector_map_empty_skips_sector_check():
    """sector_map 없으면 섹터 체크 스킵."""
    gateway = RiskGateway(
        mode="paper",
        max_symbol_weight=0.10,
        max_sector_weight=0.20,
        require_market_hours=False,
    )
    trades = [_make_trade("005930", "삼성전자", 400_000)]
    decision = gateway.check(trades, _balance(10_000_000), _passing_pipeline())
    assert any("섹터 집중도 체크 스킵" in c for c in decision.checks)


def test_seed_constants_match_plan():
    """SEED 상수 값 확인 — 종목 5%, 섹터 20%."""
    assert SEED_MAX_SYMBOL_WEIGHT == 0.05
    assert SEED_MAX_SECTOR_WEIGHT == 0.20


# ── run_loop stage-aware interval ──────────────────────────────


def test_resolve_interval_paper_1hour():
    """PAPER 단계 → 3600s (1시간)."""
    from kis_backtest.luxon.terminal import LuxonTerminal
    from kis_backtest.execution.capital_ladder import CapitalLadder, Stage

    term = LuxonTerminal(symbols=["005930"])
    term._capital_ladder = CapitalLadder()
    assert term._capital_ladder.current_stage == Stage.PAPER
    assert term._resolve_interval(stage_aware=True) == 3600


def test_resolve_interval_seed_4hours():
    """SEED 단계 → 14400s (4시간)."""
    from kis_backtest.luxon.terminal import LuxonTerminal
    from kis_backtest.execution.capital_ladder import CapitalLadder, Stage

    term = LuxonTerminal(symbols=["005930"])
    ladder = CapitalLadder()
    ladder._stage_idx = 1  # SEED
    term._capital_ladder = ladder
    assert ladder.current_stage == Stage.SEED
    assert term._resolve_interval(stage_aware=True) == 14400


def test_resolve_interval_falls_back_to_config():
    """stage_aware=False → config.refresh_secs 사용."""
    from kis_backtest.luxon.terminal import LuxonTerminal, TerminalConfig

    term = LuxonTerminal(TerminalConfig(
        symbols=["005930"], refresh_secs=7200,
    ))
    assert term._resolve_interval(stage_aware=False) == 7200


# ── run_loop 예외 격리 ────────────────────────────────────────


def test_run_loop_isolates_cycle_exceptions(monkeypatch, tmp_path):
    """단일 사이클 예외가 다음 사이클을 막지 않음."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("time.sleep", lambda _: None)  # 빠르게

    from kis_backtest.luxon.terminal import LuxonTerminal

    term = LuxonTerminal(symbols=["005930"])
    term._initialized = True  # boot 스킵

    call_count = {"n": 0}

    def flaky_cycle():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("flaky failure")
        from kis_backtest.luxon.terminal import CycleReport
        return CycleReport(
            cycle_num=call_count["n"],
            started_at="", finished_at="",
            regime="unknown", regime_confidence=0.0,
            decisions=[], ta_signals=[],
            convictions_before={}, convictions_after={},
            kill_triggered=False, mcp_mode="offline",
        )

    term.cycle = flaky_cycle
    term.run_loop(max_cycles=3, stage_aware_interval=False)

    assert call_count["n"] == 3  # 첫 실패 후 2, 3번도 실행됨
