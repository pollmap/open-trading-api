"""Walk-Forward Validator 테스트"""

import math

import pytest

from kis_backtest.core.walk_forward import (
    WalkForwardValidator,
    WFConfig,
    WFResult,
    FoldResult,
    _sharpe,
    _max_drawdown,
    _cumulative_return,
    _split_folds,
)


# ── 유틸 함수 테스트 ────────────────────────────────────

class TestSharpe:
    def test_positive_returns(self):
        rets = [0.01] * 100
        s = _sharpe(rets)
        assert s > 10  # 일정한 양수 수익 → 매우 높은 Sharpe

    def test_zero_returns(self):
        rets = [0.0] * 100
        s = _sharpe(rets)
        assert s == 0.0

    def test_mixed_returns(self):
        rets = [0.01, -0.01, 0.02, -0.005, 0.015] * 20
        s = _sharpe(rets)
        assert isinstance(s, float)
        assert s > 0

    def test_negative_returns(self):
        rets = [-0.01] * 100
        s = _sharpe(rets)
        assert s < -10

    def test_single_return(self):
        assert _sharpe([0.01]) == 0.0

    def test_empty_returns(self):
        assert _sharpe([]) == 0.0


class TestMaxDrawdown:
    def test_no_drawdown(self):
        rets = [0.01] * 50
        dd = _max_drawdown(rets)
        assert dd == 0.0

    def test_simple_drawdown(self):
        rets = [0.10, -0.20, 0.05]
        dd = _max_drawdown(rets)
        assert dd < 0
        assert dd > -0.25

    def test_pure_loss(self):
        rets = [-0.05] * 10
        dd = _max_drawdown(rets)
        assert dd < -0.30

    def test_empty(self):
        assert _max_drawdown([]) == 0.0

    def test_recovery(self):
        rets = [0.10, -0.15, 0.20, 0.10]
        dd = _max_drawdown(rets)
        assert dd < 0


class TestCumulativeReturn:
    def test_positive(self):
        rets = [0.10, 0.10, 0.10]
        cr = _cumulative_return(rets)
        assert abs(cr - (1.1**3 - 1)) < 1e-10

    def test_zero(self):
        assert _cumulative_return([0, 0, 0]) == 0.0

    def test_empty(self):
        assert _cumulative_return([]) == 0.0

    def test_negative(self):
        rets = [-0.10, -0.10]
        cr = _cumulative_return(rets)
        assert cr < 0


# ── 폴드 분할 테스트 ────────────────────────────────────

class TestSplitFolds:
    def test_rolling_5_folds(self):
        folds = _split_folds(1000, 5, 0.7, anchored=False)
        assert len(folds) == 5
        for tr_s, tr_e, te_s, te_e in folds:
            assert tr_s < tr_e
            assert te_s < te_e
            assert tr_e == te_s  # 연속

    def test_anchored_folds(self):
        folds = _split_folds(1000, 5, 0.7, anchored=True)
        assert len(folds) > 0
        for tr_s, tr_e, te_s, te_e in folds:
            assert tr_s == 0  # anchored: 시작점 고정

    def test_small_data(self):
        folds = _split_folds(50, 5, 0.7, anchored=False)
        assert len(folds) == 5


# ── 메인 Validator 테스트 ────────────────────────────────

class TestWalkForwardValidator:
    @pytest.fixture
    def good_returns(self):
        """양호한 전략 수익률 (252일 × 2년)"""
        import random
        random.seed(42)
        return [random.gauss(0.0005, 0.01) for _ in range(504)]

    @pytest.fixture
    def bad_returns(self):
        """나쁜 전략 수익률"""
        import random
        random.seed(99)
        return [random.gauss(-0.001, 0.02) for _ in range(504)]

    def test_validate_basic(self, good_returns):
        validator = WalkForwardValidator(WFConfig(n_folds=5, min_sharpe=0.0))
        result = validator.validate(good_returns)

        assert isinstance(result, WFResult)
        assert len(result.folds) == 5
        assert result.total_days == 504

    def test_validate_pass(self, good_returns):
        validator = WalkForwardValidator(WFConfig(
            n_folds=5, min_sharpe=0.0, max_oos_dd=-0.50,
        ))
        result = validator.validate(good_returns)
        assert result.passed

    def test_validate_fail_high_threshold(self, good_returns):
        """높은 threshold에서 실패"""
        validator = WalkForwardValidator(WFConfig(
            n_folds=5, min_sharpe=5.0,  # 비현실적으로 높은 기준
        ))
        result = validator.validate(good_returns)
        assert not result.passed
        assert "FAIL" in result.verdict

    def test_insufficient_data(self):
        validator = WalkForwardValidator()
        result = validator.validate([0.01] * 30)
        assert len(result.folds) == 0

    def test_strategy_fn(self, good_returns):
        """전략 함수 적용"""
        def buy_hold(train_rets):
            return train_rets  # pass through

        validator = WalkForwardValidator(WFConfig(n_folds=3))
        result = validator.validate(good_returns, strategy_fn=buy_hold)
        assert len(result.folds) == 3

    def test_oos_returns_per_fold(self, good_returns):
        """직접 OOS 수익률 제공"""
        validator = WalkForwardValidator(WFConfig(n_folds=3))
        oos = [[0.005] * 30, [0.003] * 30, [0.001] * 30]
        result = validator.validate(good_returns, oos_returns_per_fold=oos)
        assert len(result.folds) == 3
        assert result.folds[0].oos_sharpe > result.folds[2].oos_sharpe

    def test_anchored_mode(self, good_returns):
        validator = WalkForwardValidator(WFConfig(n_folds=5, anchored=True))
        result = validator.validate(good_returns)
        assert len(result.folds) > 0
        # anchored: 첫 폴드 학습 시작 = 0
        assert result.folds[0].train_start == 0

    def test_summary_table(self, good_returns):
        validator = WalkForwardValidator(WFConfig(n_folds=3))
        result = validator.validate(good_returns)
        table = result.summary_table()
        assert len(table) == 3
        assert "is_sharpe" in table[0]
        assert "oos_sharpe" in table[0]

    def test_to_dict(self, good_returns):
        validator = WalkForwardValidator(WFConfig(n_folds=3))
        result = validator.validate(good_returns)
        d = result.to_dict()
        assert "passed" in d
        assert "verdict" in d
        assert "folds" in d

    def test_verdict_messages(self):
        """다양한 실패 사유"""
        config = WFConfig(min_sharpe=1.0, max_oos_dd=-0.05, min_win_rate=0.8)
        result = WFResult(
            config=config,
            folds=[
                FoldResult(0, 0, 70, 70, 100, 2.0, 0.5, 0.05, -0.10, 30, 0.75),
                FoldResult(1, 100, 170, 170, 200, 1.5, -0.2, -0.03, -0.08, 30, 1.13),
            ],
            total_days=200,
        )
        assert "FAIL" in result.verdict
        assert "Sharpe" in result.verdict or "MDD" in result.verdict

    def test_degradation(self, good_returns):
        """IS → OOS Sharpe 감소 추적"""
        validator = WalkForwardValidator(WFConfig(n_folds=5))
        result = validator.validate(good_returns)
        assert isinstance(result.mean_degradation, float)

    def test_oos_median_sharpe(self, good_returns):
        validator = WalkForwardValidator(WFConfig(n_folds=5))
        result = validator.validate(good_returns)
        assert isinstance(result.oos_median_sharpe, float)


class TestMultiAsset:
    def test_validate_multi_asset(self):
        import random
        random.seed(42)
        returns_dict = {
            "005930": [random.gauss(0.0005, 0.01) for _ in range(300)],
            "000660": [random.gauss(0.0003, 0.015) for _ in range(300)],
        }
        weights = {"005930": 0.6, "000660": 0.4}

        validator = WalkForwardValidator(WFConfig(n_folds=3, min_sharpe=0.0))
        result = validator.validate_multi_asset(returns_dict, weights)
        assert len(result.folds) == 3

    def test_empty_weights(self):
        validator = WalkForwardValidator()
        result = validator.validate_multi_asset({}, {})
        assert len(result.folds) == 0
