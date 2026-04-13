"""v1.0 감사에서 발견한 통합 갭 수정 검증 (C1/C2/C3/M1).

C1: cycle() → CapitalLadder.update() 자동 호출
C2: cycle() → RiskGateway 체크 자동 실행
C3: CUFA HTML 자동 인식 (JSON 우선, HTML fallback)
M1: KOSDAQ sector_map 추론
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kis_backtest.luxon.terminal import LuxonTerminal, TerminalConfig


# ── C1: CapitalLadder equity 업데이트 ────────────────────────


def test_c1_cycle_updates_capital_ladder(monkeypatch, tmp_path):
    """cycle() 내부에서 _update_ladder_equity가 호출돼 days_in_stage 증가."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    term = LuxonTerminal(symbols=["005930"], paper_mode=True)
    term.boot()

    initial_days = term._capital_ladder.days_in_stage
    term.cycle()
    term.cycle()
    term.cycle()

    assert term._capital_ladder.days_in_stage >= initial_days + 3, \
        "cycle() 3회 후 ladder days_in_stage 증가 기대"


def test_c1_update_ladder_equity_uses_brokerage_when_live(tmp_path, monkeypatch):
    """paper_mode=False + live_executor 있으면 brokerage.get_balance() 사용."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    from kis_backtest.execution.capital_ladder import CapitalLadder
    from kis_backtest.models import AccountBalance

    term = LuxonTerminal(symbols=["005930"], paper_mode=False)
    term._capital_ladder = CapitalLadder()

    class _FakeBrok:
        def get_balance(self):
            return AccountBalance(
                total_cash=0, available_cash=0, total_equity=9_500_000,
                total_pnl=-500_000, total_pnl_percent=-5.0, currency="KRW",
            )

    class _FakeExec:
        _brokerage = _FakeBrok()

    term._live_executor = _FakeExec()
    term._update_ladder_equity()

    last_equity = term._capital_ladder._equity_history[-1].equity
    assert last_equity == 9_500_000


# ── C2: RiskGateway 통합 ────────────────────────────────────


def test_c2_boot_creates_risk_gateway(tmp_path, monkeypatch):
    """boot() 호출 시 RiskGateway 생성."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    term = LuxonTerminal(symbols=["005930"], paper_mode=True)
    term.boot()

    assert term._risk_gateway is not None


def test_c2_risk_gateway_disabled_by_config(tmp_path, monkeypatch):
    """enable_risk_gateway=False → RiskGateway 미생성."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    term = LuxonTerminal(TerminalConfig(
        symbols=["005930"],
        paper_mode=True,
        enable_risk_gateway=False,
    ))
    term.boot()

    assert term._risk_gateway is None


def test_c2_risk_gateway_stage_aware():
    """PAPER 단계 → max_symbol_weight=None. SEED 단계 → 0.05."""
    from kis_backtest.execution.capital_ladder import CapitalLadder, Stage

    term = LuxonTerminal(symbols=["005930"])
    term._capital_ladder = CapitalLadder()

    # PAPER
    gw_paper = term._build_risk_gateway()
    assert gw_paper._max_symbol_weight is None

    # SEED
    term._capital_ladder._stage_idx = 1
    assert term._capital_ladder.current_stage == Stage.SEED
    gw_seed = term._build_risk_gateway()
    assert gw_seed._max_symbol_weight == 0.05
    assert gw_seed._max_sector_weight == 0.20


# ── C3: CUFA HTML fallback ──────────────────────────────────


def test_c3_load_cufa_prefers_json_over_html(tmp_path):
    """동일 symbol이 JSON+HTML에 있으면 JSON 우선."""
    from kis_backtest.luxon.integration.cufa_conviction import (
        load_cufa_digests_from_dir,
    )

    # JSON: IP 3개
    (tmp_path / "samsung.json").write_text(
        json.dumps({
            "ticker": "005930",
            "investment_points": [{"id": 1}, {"id": 2}, {"id": 3}],
            "kill_conditions": [],
        }),
        encoding="utf-8",
    )
    # HTML: 같은 symbol, IP 0 (heuristic 한계)
    (tmp_path / "samsung.html").write_text(
        "<html><title>삼성전자 (005930) — CUFA 기업분석보고서</title>"
        "<body><h2>섹터 — 반도체</h2></body></html>",
        encoding="utf-8",
    )

    digests = load_cufa_digests_from_dir(tmp_path)
    # JSON 먼저 로드됨 + HTML은 중복으로 스킵
    assert len(digests) == 1
    assert digests[0]["ticker"] == "005930"
    assert len(digests[0]["investment_points"]) == 3  # JSON 값 유지


def test_c3_load_cufa_html_only_minimal_digest(tmp_path):
    """HTML만 있으면 최소 digest(IP=0, sector만) 반환 → conviction=5.0."""
    from kis_backtest.luxon.integration.cufa_conviction import (
        build_convictions_from_digests,
        load_cufa_digests_from_dir,
    )

    (tmp_path / "sk.html").write_text(
        "<html><title>SK하이닉스 (000660) — CUFA 기업분석보고서</title>"
        "<body><h2>섹터 — 반도체</h2></body></html>",
        encoding="utf-8",
    )

    digests = load_cufa_digests_from_dir(tmp_path)
    assert len(digests) == 1
    assert digests[0]["ticker"] == "000660"

    convictions = build_convictions_from_digests(digests)
    assert convictions == {"000660": 5.0}  # IP=0, kill=0 → base


# ── M1: KOSDAQ Market 추론 ─────────────────────────────────


def test_m1_sector_map_infers_kosdaq():
    """sector_map에 KOSDAQ 포함되면 Market.KOSDAQ으로 세율 적용."""
    from kis_backtest.luxon.terminal import _orch_to_portfolio_order
    from kis_backtest.strategies.risk.cost_model import Market
    from tests.test_terminal_live_execution import _make_orch_report
    from kis_backtest.portfolio.ackman_druckenmiller import InvestmentDecision
    from kis_backtest.portfolio.conviction_sizer import PositionSize

    decisions = [InvestmentDecision(
        symbol="247540", action="buy", conviction=7.0,
        catalyst_score=0.5, regime="expansion",
        regime_weight_adjustment=1.0, final_weight=0.1,
    )]
    sizes = [PositionSize(
        symbol="247540", conviction=7.0, weight=0.1,
        amount=1_000_000, kelly_raw=0.12, capped=False,
    )]
    orch = _make_orch_report(decisions, sizes)

    # sector_map에 KOSDAQ 표기
    order = _orch_to_portfolio_order(
        orch, capital=10_000_000,
        sector_map={"247540": "2차전지 (KOSDAQ)"},
    )
    assert order.allocations[0].market == Market.KOSDAQ


def test_m1_sector_map_default_kospi():
    """sector_map 없으면 Market.KOSPI 기본."""
    from kis_backtest.luxon.terminal import _orch_to_portfolio_order
    from kis_backtest.strategies.risk.cost_model import Market
    from tests.test_terminal_live_execution import _make_orch_report
    from kis_backtest.portfolio.ackman_druckenmiller import InvestmentDecision
    from kis_backtest.portfolio.conviction_sizer import PositionSize

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

    order = _orch_to_portfolio_order(orch, capital=10_000_000)
    assert order.allocations[0].market == Market.KOSPI
