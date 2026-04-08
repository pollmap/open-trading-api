"""Capital Ladder 테스트"""

import json
import tempfile
from pathlib import Path

import pytest

from kis_backtest.execution.capital_ladder import (
    CapitalLadder,
    LadderConfig,
    Stage,
    StageConfig,
    DEFAULT_STAGES,
    DailyEquity,
    LadderStatus,
)


class TestStageEnum:
    def test_ordering(self):
        assert Stage.PAPER < Stage.SEED < Stage.GROWTH < Stage.SCALE < Stage.FULL

    def test_values(self):
        assert Stage.PAPER == 0
        assert Stage.FULL == 4


class TestDefaultStages:
    def test_five_stages(self):
        assert len(DEFAULT_STAGES) == 5

    def test_capital_progression(self):
        pcts = [s.capital_pct for s in DEFAULT_STAGES]
        assert pcts == [0.0, 0.10, 0.30, 0.60, 1.00]

    def test_all_have_labels(self):
        for s in DEFAULT_STAGES:
            assert s.label


class TestCapitalLadderInit:
    def test_default_config(self):
        ladder = CapitalLadder()
        assert ladder.current_stage == Stage.PAPER
        assert ladder.deployed_capital == 0.0

    def test_custom_capital(self):
        ladder = CapitalLadder(LadderConfig(total_capital=50_000_000))
        assert ladder.deployed_capital == 0.0
        ladder._stage_idx = 1  # SEED
        assert ladder.deployed_capital == 5_000_000

    def test_init_history(self):
        ladder = CapitalLadder()
        assert len(ladder._history) == 1
        assert ladder._history[0].action == "init"


class TestUpdate:
    def test_basic_update(self):
        ladder = CapitalLadder()
        result = ladder.update(10_000_000, dt="2026-01-01")
        assert result is None
        assert len(ladder._equity_history) == 1

    def test_daily_return_calc(self):
        ladder = CapitalLadder()
        ladder.update(10_000_000, dt="2026-01-01")
        ladder.update(10_100_000, dt="2026-01-02")
        assert abs(ladder._equity_history[1].daily_return - 0.01) < 1e-10

    def test_peak_tracking(self):
        ladder = CapitalLadder()
        ladder.update(10_000_000)
        ladder.update(11_000_000)
        ladder.update(10_500_000)
        assert ladder._peak_equity == 11_000_000


class TestPromotion:
    @pytest.fixture
    def ready_ladder(self):
        """승격 가능한 상태의 래더"""
        ladder = CapitalLadder(LadderConfig(total_capital=10_000_000))
        # 25일 동안 꾸준한 수익
        equity = 10_000_000
        for i in range(25):
            equity *= 1.002  # 일 0.2% 수익
            ladder.update(equity, dt=f"2026-01-{i+1:02d}")
        return ladder

    def test_can_promote_true(self, ready_ladder):
        ok, blockers = ready_ladder.can_promote()
        assert ok, f"blockers: {blockers}"
        assert len(blockers) == 0

    def test_can_promote_insufficient_days(self):
        ladder = CapitalLadder()
        for i in range(5):
            ladder.update(10_000_000 + i * 10_000, dt=f"2026-01-{i+1:02d}")
        ok, blockers = ladder.can_promote()
        assert not ok
        assert any("기간" in b for b in blockers)

    def test_promote_success(self, ready_ladder):
        msg = ready_ladder.promote()
        assert "승격" in msg
        assert ready_ladder.current_stage == Stage.SEED
        assert ready_ladder.deployed_capital == 1_000_000

    def test_promote_resets_stage_counter(self, ready_ladder):
        ready_ladder.promote()
        assert ready_ladder.days_in_stage == 0

    def test_promote_at_full(self, ready_ladder):
        ready_ladder._stage_idx = 4  # FULL
        msg = ready_ladder.promote()
        assert "최고 단계" in msg

    def test_force_promote(self):
        ladder = CapitalLadder()
        msg = ladder.promote(force=True)
        assert "승격" in msg or "강제" in msg
        assert ladder.current_stage == Stage.SEED

    def test_promote_blocked(self):
        ladder = CapitalLadder()
        # 손실 상태
        for i in range(25):
            ladder.update(10_000_000 - i * 50_000)
        msg = ladder.promote()
        assert "승격 불가" in msg


class TestDemotion:
    def test_basic_demote(self):
        ladder = CapitalLadder()
        ladder.promote(force=True)  # → SEED
        assert ladder.current_stage == Stage.SEED
        msg = ladder.demote(reason="테스트")
        assert "강등" in msg
        assert ladder.current_stage == Stage.PAPER

    def test_demote_at_paper(self):
        ladder = CapitalLadder()
        msg = ladder.demote()
        assert "최저 단계" in msg

    def test_auto_demote_on_big_dd(self):
        ladder = CapitalLadder(LadderConfig(total_capital=10_000_000))
        ladder.promote(force=True)  # → SEED (max_dd=-0.10, demote at -0.15)
        ladder.update(10_000_000)
        ladder.update(10_500_000)  # peak
        # 큰 하락
        result = ladder.update(8_500_000)  # -19% DD → 자동 강등
        assert ladder.current_stage == Stage.PAPER
        assert result is not None
        assert "강등" in result


class TestStatus:
    def test_status_fields(self):
        ladder = CapitalLadder()
        for i in range(5):
            ladder.update(10_000_000 + i * 10_000)
        st = ladder.status()
        assert isinstance(st, LadderStatus)
        assert st.stage == Stage.PAPER
        assert st.deployed_capital == 0.0
        assert st.days_in_stage == 5

    def test_status_to_dict(self):
        ladder = CapitalLadder()
        ladder.update(10_000_000)
        d = ladder.status().to_dict()
        assert "stage" in d
        assert "capital_pct" in d
        assert "history" in d


class TestPipelineIntegration:
    def test_get_pipeline_capital(self):
        ladder = CapitalLadder(LadderConfig(total_capital=10_000_000))
        assert ladder.get_pipeline_capital() == 0.0

        ladder.promote(force=True)  # SEED
        assert ladder.get_pipeline_capital() == 1_000_000

        ladder.promote(force=True)  # GROWTH
        assert ladder.get_pipeline_capital() == 3_000_000


class TestStatePersistence:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = str(Path(tmpdir) / "ladder_state.json")

            # 저장
            ladder1 = CapitalLadder(LadderConfig(
                total_capital=10_000_000,
                state_file=state_file,
            ))
            for i in range(25):
                ladder1.update(10_000_000 + i * 20_000)
            ladder1.promote(force=True)

            # 로드
            ladder2 = CapitalLadder(LadderConfig(
                total_capital=10_000_000,
                state_file=state_file,
            ))
            assert ladder2.current_stage == Stage.SEED
            assert len(ladder2._equity_history) == 25

    def test_no_state_file(self):
        ladder = CapitalLadder(LadderConfig(state_file=None))
        ladder.promote(force=True)
        # 에러 없이 동작

    def test_missing_state_file(self):
        ladder = CapitalLadder(LadderConfig(
            state_file="/tmp/nonexistent_test_state.json"
        ))
        assert ladder.current_stage == Stage.PAPER


class TestFullLifecycle:
    """전체 라이프사이클 시뮬레이션"""

    def test_paper_to_full(self):
        ladder = CapitalLadder(LadderConfig(total_capital=10_000_000))
        equity = 10_000_000

        stages_visited = [ladder.current_stage]

        # PAPER → SEED → GROWTH → SCALE → FULL
        for stage_idx in range(4):
            for day in range(25):
                equity *= 1.003
                ladder.update(equity)

            if ladder.can_promote()[0]:
                ladder.promote()
                stages_visited.append(ladder.current_stage)

        assert Stage.PAPER in stages_visited
        assert ladder.current_stage.value >= Stage.SEED.value

    def test_promote_demote_cycle(self):
        ladder = CapitalLadder(LadderConfig(total_capital=10_000_000))

        # 승격
        equity = 10_000_000
        for i in range(25):
            equity *= 1.003
            ladder.update(equity)
        ladder.promote(force=True)
        assert ladder.current_stage == Stage.SEED

        # 강등
        ladder.demote(reason="성과 부진")
        assert ladder.current_stage == Stage.PAPER

        # 다시 승격
        ladder.promote(force=True)
        assert ladder.current_stage == Stage.SEED

        # 히스토리 확인
        assert len(ladder._history) >= 4  # init + 3 transitions
