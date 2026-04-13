"""CapitalLadder WF 자동 승급 + run_walk_forward 스크립트 통합 테스트 (v0.8).

Plan 4-2: PAPER→SEED 승급 조건 = OOS Sharpe≥0.5 AND MaxDD>-10% AND 기간≥4주.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import pytest

from kis_backtest.core.walk_forward import WFConfig, WFResult, FoldResult
from kis_backtest.execution.capital_ladder import (
    CapitalLadder,
    DEFAULT_STAGES,
    LadderConfig,
    Stage,
    StageConfig,
)


# ── DEFAULT_STAGES 튜닝 확인 ─────────────────────────────────────


def test_default_stages_paper_tightened():
    """PAPER 단계 기준: min_sharpe 0.5, max_dd -10% (Plan 4-2)."""
    paper = DEFAULT_STAGES[0]
    assert paper.stage == Stage.PAPER
    assert paper.min_sharpe == 0.5
    assert paper.max_dd == -0.10
    assert paper.min_days == 20  # 4주


# ── promote_if_wf_passed ─────────────────────────────────────────


def _make_wf_result(
    mean_sharpe: float,
    worst_dd: float,
    passed: bool,
    *,
    cfg_min_sharpe: float = 0.5,
    cfg_max_dd: float = -0.10,
) -> WFResult:
    """합성 WFResult. WFConfig 기준을 커스텀해서 WFResult.passed 조절."""
    fold = FoldResult(
        fold_idx=0, train_start=0, train_end=60, test_start=60, test_end=80,
        is_sharpe=mean_sharpe + 0.3,
        oos_sharpe=mean_sharpe,
        oos_return=0.05,
        oos_max_dd=worst_dd,
        oos_n_days=20,
        degradation=0.2,
    )
    return WFResult(
        config=WFConfig(
            n_folds=1, min_sharpe=cfg_min_sharpe, max_oos_dd=cfg_max_dd,
        ),
        folds=[fold],
        total_days=80,
    )


def _ladder_with_days(n_days: int, capital: float = 10_000_000) -> CapitalLadder:
    """n_days 동안 소폭 상승한 페이퍼 래더."""
    ladder = CapitalLadder(LadderConfig(total_capital=capital))
    equity = capital
    for i in range(n_days):
        equity *= 1.001
        ladder.update(equity, dt=f"2026-01-{i+1:02d}")
    return ladder


def test_promote_if_wf_passed_success():
    """WF 통과 + 기간 충족 → PAPER → SEED 자동 승급."""
    ladder = _ladder_with_days(25)
    wf = _make_wf_result(mean_sharpe=0.8, worst_dd=-0.05, passed=True)

    msg = ladder.promote_if_wf_passed(wf)
    assert msg is not None
    assert "WF 승급" in msg
    assert ladder.current_stage == Stage.SEED
    assert ladder.deployed_capital == 1_000_000


def test_promote_if_wf_passed_blocked_by_low_sharpe():
    """OOS Sharpe < min → 승급 차단."""
    ladder = _ladder_with_days(25)
    wf = _make_wf_result(mean_sharpe=0.3, worst_dd=-0.05, passed=False)

    msg = ladder.promote_if_wf_passed(wf)
    assert msg is None
    assert ladder.current_stage == Stage.PAPER


def test_promote_if_wf_passed_blocked_by_deep_dd():
    """OOS MaxDD 초과 → 승급 차단."""
    ladder = _ladder_with_days(25)
    # passed=True로 넣되 DD만 깊게: 헬퍼가 DD 체크로 걸러야 함
    wf = _make_wf_result(mean_sharpe=0.8, worst_dd=-0.25, passed=True)

    msg = ladder.promote_if_wf_passed(wf)
    assert msg is None
    assert ladder.current_stage == Stage.PAPER


def test_promote_if_wf_passed_blocked_by_insufficient_days():
    """기간 < 20일 → 승급 차단."""
    ladder = _ladder_with_days(10)
    wf = _make_wf_result(mean_sharpe=0.8, worst_dd=-0.05, passed=True)

    msg = ladder.promote_if_wf_passed(wf)
    assert msg is None


def test_promote_if_wf_passed_at_full_stage_returns_none():
    """이미 FULL이면 None 반환."""
    ladder = CapitalLadder(LadderConfig(total_capital=10_000_000))
    ladder._stage_idx = 4  # FULL
    wf = _make_wf_result(mean_sharpe=1.0, worst_dd=-0.03, passed=True)
    assert ladder.promote_if_wf_passed(wf) is None


def test_promote_if_wf_passed_custom_thresholds():
    """사용자 정의 임계값 — 0.3 / -20%."""
    ladder = _ladder_with_days(25)
    # WFConfig도 완화해서 wf.passed=True가 되도록
    wf = _make_wf_result(
        mean_sharpe=0.35, worst_dd=-0.15, passed=True,
        cfg_min_sharpe=0.3, cfg_max_dd=-0.20,
    )

    msg = ladder.promote_if_wf_passed(
        wf, min_oos_sharpe=0.3, max_oos_dd=-0.20,
    )
    assert msg is not None
    assert ladder.current_stage == Stage.SEED


def test_promote_if_wf_passed_records_history():
    """승급 히스토리에 OOS Sharpe + DD가 기록됨."""
    ladder = _ladder_with_days(25)
    wf = _make_wf_result(mean_sharpe=0.8, worst_dd=-0.05, passed=True)

    ladder.promote_if_wf_passed(wf)
    last = ladder.status().history[-1]
    assert last.action == "promote"
    assert "WF 통과 승급" in last.reason
    assert abs(last.sharpe - 0.8) < 1e-6
    assert abs(last.max_dd - (-0.05)) < 1e-6


# ── run_walk_forward 스크립트 I/O ────────────────────────────────


def test_load_returns_from_file_returns_format(tmp_path):
    """{"returns": [...]} 포맷."""
    from scripts.run_walk_forward import load_returns_from_file

    path = tmp_path / "returns.json"
    path.write_text(json.dumps({"returns": [0.01, -0.005, 0.003]}),
                    encoding="utf-8")
    result = load_returns_from_file(path)
    assert result == [0.01, -0.005, 0.003]


def test_load_returns_from_file_equity_format(tmp_path):
    """[{"date":, "equity":, "daily_return":}, ...] 포맷."""
    from scripts.run_walk_forward import load_returns_from_file

    path = tmp_path / "equity.json"
    path.write_text(json.dumps([
        {"date": "2026-01-01", "equity": 1_000_000, "daily_return": 0.0},
        {"date": "2026-01-02", "equity": 1_010_000, "daily_return": 0.01},
        {"date": "2026-01-03", "equity": 1_020_100, "daily_return": 0.01},
    ]), encoding="utf-8")
    result = load_returns_from_file(path)
    assert len(result) == 2  # 첫날 제외
    assert result == [0.01, 0.01]


def test_load_returns_computes_from_equity_only(tmp_path):
    """daily_return 없이 equity만 있어도 자동 계산."""
    from scripts.run_walk_forward import load_returns_from_file

    path = tmp_path / "equity_only.json"
    path.write_text(json.dumps([
        {"date": "2026-01-01", "equity": 1_000_000},
        {"date": "2026-01-02", "equity": 1_020_000},
    ]), encoding="utf-8")
    result = load_returns_from_file(path)
    assert len(result) == 1
    assert abs(result[0] - 0.02) < 1e-9


def test_run_walk_forward_helper_passes_on_good_returns():
    """양의 일관된 수익률 → WFResult.passed 가능."""
    from scripts.run_walk_forward import run_walk_forward

    # 100일 동안 일 0.5%+noise (양의 Sharpe 보장)
    import random
    random.seed(42)
    returns = [0.005 + random.uniform(-0.002, 0.002) for _ in range(100)]

    wf = run_walk_forward(
        returns=returns,
        n_folds=4,
        train_ratio=0.7,
        min_sharpe=0.5,
        max_dd=-0.10,
    )
    assert wf.oos_mean_sharpe > 0
    assert len(wf.folds) == 4
