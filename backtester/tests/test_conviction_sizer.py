"""확신 기반 포지션 사이저 테스트 — Ackman의 "확신이 높으면 크게 걸어라" """

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kis_backtest.portfolio.conviction_sizer import (
    ConvictionLevel,
    ConvictionSizer,
    PositionSize,
    _clamp,
)


# ── ConvictionLevel 테스트 ─────────────────────────────────


class TestConvictionLevel:
    """frozen dataclass 기본 동작 검증"""

    def test_create_basic(self):
        level = ConvictionLevel(
            symbol="005930",
            base_conviction=7.0,
            catalyst_boost=1.5,
            kill_condition_penalty=0.0,
            final_conviction=8.5,
        )
        assert level.symbol == "005930"
        assert level.final_conviction == 8.5

    def test_frozen_immutable(self):
        level = ConvictionLevel(
            symbol="005930",
            base_conviction=7.0,
            catalyst_boost=0.0,
            kill_condition_penalty=0.0,
            final_conviction=7.0,
        )
        with pytest.raises(AttributeError):
            level.final_conviction = 9.0  # type: ignore[misc]

    def test_equality(self):
        kwargs = dict(
            symbol="005930",
            base_conviction=7.0,
            catalyst_boost=1.0,
            kill_condition_penalty=0.0,
            final_conviction=8.0,
        )
        assert ConvictionLevel(**kwargs) == ConvictionLevel(**kwargs)

    def test_inequality_different_conviction(self):
        base = dict(
            symbol="005930",
            base_conviction=7.0,
            catalyst_boost=0.0,
            kill_condition_penalty=0.0,
        )
        a = ConvictionLevel(**base, final_conviction=7.0)
        b = ConvictionLevel(**base, final_conviction=8.0)
        assert a != b

    def test_fields_accessible(self):
        level = ConvictionLevel(
            symbol="000660",
            base_conviction=5.0,
            catalyst_boost=2.0,
            kill_condition_penalty=1.0,
            final_conviction=6.0,
        )
        assert level.base_conviction == 5.0
        assert level.catalyst_boost == 2.0
        assert level.kill_condition_penalty == 1.0


# ── PositionSize 테스트 ────────────────────────────────────


class TestPositionSize:
    """frozen dataclass 기본 동작 검증"""

    def test_create_basic(self):
        pos = PositionSize(
            symbol="005930",
            conviction=8.0,
            weight=0.15,
            amount=15_000_000.0,
            kelly_raw=0.1944,
            capped=False,
        )
        assert pos.symbol == "005930"
        assert pos.amount == 15_000_000.0

    def test_frozen_immutable(self):
        pos = PositionSize(
            symbol="005930",
            conviction=8.0,
            weight=0.15,
            amount=15_000_000.0,
            kelly_raw=0.15,
            capped=False,
        )
        with pytest.raises(AttributeError):
            pos.weight = 0.5  # type: ignore[misc]

    def test_capped_flag(self):
        pos = PositionSize(
            symbol="005930",
            conviction=10.0,
            weight=0.20,
            amount=20_000_000.0,
            kelly_raw=0.50,
            capped=True,
        )
        assert pos.capped is True

    def test_zero_weight(self):
        pos = PositionSize(
            symbol="005930",
            conviction=1.0,
            weight=0.0,
            amount=0.0,
            kelly_raw=0.0,
            capped=False,
        )
        assert pos.weight == 0.0
        assert pos.amount == 0.0

    def test_equality(self):
        kwargs = dict(
            symbol="005930",
            conviction=7.0,
            weight=0.10,
            amount=10_000_000.0,
            kelly_raw=0.10,
            capped=False,
        )
        assert PositionSize(**kwargs) == PositionSize(**kwargs)


# ── ConvictionSizer 핵심 테스트 ────────────────────────────


class TestConvictionSizer:
    """ConvictionSizer 메인 로직"""

    def test_init_defaults(self):
        sizer = ConvictionSizer()
        assert sizer.max_position_pct == 0.20
        assert sizer.min_position_pct == 0.02
        assert sizer.kelly_fraction == 0.5

    def test_init_custom(self):
        sizer = ConvictionSizer(max_position_pct=0.15, min_position_pct=0.01, kelly_fraction=0.3)
        assert sizer.max_position_pct == 0.15
        assert sizer.min_position_pct == 0.01
        assert sizer.kelly_fraction == 0.3

    def test_init_invalid_max_position(self):
        with pytest.raises(ValueError):
            ConvictionSizer(max_position_pct=0.0)
        with pytest.raises(ValueError):
            ConvictionSizer(max_position_pct=1.5)

    def test_init_invalid_min_position(self):
        with pytest.raises(ValueError):
            ConvictionSizer(min_position_pct=-0.01)
        with pytest.raises(ValueError):
            ConvictionSizer(min_position_pct=0.20)  # == max

    def test_init_invalid_kelly(self):
        with pytest.raises(ValueError):
            ConvictionSizer(kelly_fraction=0.0)
        with pytest.raises(ValueError):
            ConvictionSizer(kelly_fraction=1.5)

    def test_set_conviction_basic(self):
        sizer = ConvictionSizer()
        level = sizer.set_conviction("005930", base_conviction=7.0)
        assert level.symbol == "005930"
        assert level.base_conviction == 7.0
        assert level.catalyst_boost == 0.0
        assert level.kill_condition_penalty == 0.0
        assert level.final_conviction == 7.0

    def test_set_conviction_with_catalyst(self):
        sizer = ConvictionSizer()
        level = sizer.set_conviction("005930", base_conviction=7.0, catalyst_score=5.0)
        # catalyst_boost = clamp(5.0 * 0.3, 0, 3) = 1.5
        assert level.catalyst_boost == 1.5
        assert level.final_conviction == 8.5

    def test_set_conviction_catalyst_cap(self):
        sizer = ConvictionSizer()
        level = sizer.set_conviction("005930", base_conviction=7.0, catalyst_score=20.0)
        # catalyst_boost = clamp(20 * 0.3, 0, 3) = 3.0
        assert level.catalyst_boost == 3.0
        assert level.final_conviction == 10.0

    def test_set_conviction_with_kill_conditions(self):
        sizer = ConvictionSizer()
        level = sizer.set_conviction("005930", base_conviction=7.0, kill_conditions_active=2)
        assert level.kill_condition_penalty == 2.0
        assert level.final_conviction == 5.0

    def test_set_conviction_clamp_low(self):
        sizer = ConvictionSizer()
        level = sizer.set_conviction("005930", base_conviction=2.0, kill_conditions_active=5)
        # 2 - 5 = -3, clamped to 1.0
        assert level.final_conviction == 1.0

    def test_set_conviction_clamp_high(self):
        sizer = ConvictionSizer()
        level = sizer.set_conviction("005930", base_conviction=9.0, catalyst_score=10.0)
        # 9 + 3 = 12, clamped to 10.0
        assert level.final_conviction == 10.0

    def test_set_conviction_base_clamped(self):
        sizer = ConvictionSizer()
        level = sizer.set_conviction("005930", base_conviction=15.0)
        assert level.base_conviction == 10.0

    def test_get_conviction(self):
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=7.0)
        assert sizer.get_conviction("005930") is not None
        assert sizer.get_conviction("999999") is None

    def test_remove_conviction(self):
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=7.0)
        assert sizer.remove_conviction("005930") is True
        assert sizer.remove_conviction("005930") is False
        assert sizer.get_conviction("005930") is None

    def test_symbols_property(self):
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=7.0)
        sizer.set_conviction("000660", base_conviction=8.0)
        assert set(sizer.symbols) == {"005930", "000660"}

    # ── Half-Kelly 포지션 사이징 ──────────────────────────

    def test_size_position_conviction_10(self):
        """확신 10 → kelly_raw = (10-1)/9 * 0.5 = 0.5 → capped at 0.20"""
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=10.0)
        pos = sizer.size_position("005930", 100_000_000)
        assert pos.kelly_raw == pytest.approx(0.5, abs=0.001)
        assert pos.weight == pytest.approx(0.20, abs=0.001)
        assert pos.capped is True
        assert pos.amount == pytest.approx(20_000_000, abs=100)

    def test_size_position_conviction_1(self):
        """확신 1 → kelly_raw = 0 → weight = 0 (스킵)"""
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=1.0)
        pos = sizer.size_position("005930", 100_000_000)
        assert pos.kelly_raw == pytest.approx(0.0)
        assert pos.weight == 0.0
        assert pos.amount == 0.0

    def test_size_position_conviction_5(self):
        """확신 5 → kelly_raw = (5-1)/9 * 0.5 ≈ 0.2222 → capped at 0.20"""
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=5.0)
        pos = sizer.size_position("005930", 100_000_000)
        expected_kelly = (5.0 - 1.0) / 9.0 * 0.5
        assert pos.kelly_raw == pytest.approx(expected_kelly, abs=0.001)
        # 0.2222 > 0.20, so capped
        assert pos.capped is True
        assert pos.weight == pytest.approx(0.20, abs=0.001)

    def test_size_position_conviction_3(self):
        """확신 3 → kelly_raw = (3-1)/9 * 0.5 ≈ 0.1111"""
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=3.0)
        pos = sizer.size_position("005930", 100_000_000)
        expected_kelly = (3.0 - 1.0) / 9.0 * 0.5
        assert pos.kelly_raw == pytest.approx(expected_kelly, abs=0.001)
        assert pos.weight == pytest.approx(expected_kelly, abs=0.001)
        assert pos.capped is False

    def test_size_position_below_min_skipped(self):
        """kelly_raw < min_position_pct → weight = 0"""
        sizer = ConvictionSizer(min_position_pct=0.05)
        sizer.set_conviction("005930", base_conviction=1.5)
        # kelly_raw = (1.5-1)/9 * 0.5 ≈ 0.0278 < 0.05
        pos = sizer.size_position("005930", 100_000_000)
        assert pos.weight == 0.0

    def test_size_position_missing_conviction_raises(self):
        sizer = ConvictionSizer()
        with pytest.raises(KeyError):
            sizer.size_position("005930", 100_000_000)

    def test_size_position_invalid_capital_raises(self):
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=7.0)
        with pytest.raises(ValueError):
            sizer.size_position("005930", 0)
        with pytest.raises(ValueError):
            sizer.size_position("005930", -1000)

    def test_size_position_conviction_8_ackman_range(self):
        """확신 8-10 → Ackman 집중 투자 영역 (15-20%)"""
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=8.0)
        pos = sizer.size_position("005930", 100_000_000)
        # kelly_raw = (8-1)/9 * 0.5 ≈ 0.3889 → capped at 0.20
        assert 0.15 <= pos.weight <= 0.20

    # ── 포트폴리오 사이징 ────────────────────────────────

    def test_size_portfolio_basic(self):
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=8.0)
        sizer.set_conviction("000660", base_conviction=6.0)
        sizer.set_conviction("035420", base_conviction=1.0)  # 스킵 대상

        portfolio = sizer.size_portfolio(
            ["005930", "000660", "035420"], total_capital=100_000_000,
        )
        # 035420은 conviction 1 → weight 0 → 제외
        assert "005930" in portfolio
        assert "000660" in portfolio
        assert "035420" not in portfolio

    def test_size_portfolio_empty(self):
        sizer = ConvictionSizer()
        portfolio = sizer.size_portfolio([], total_capital=100_000_000)
        assert portfolio == {}

    def test_size_portfolio_all_skip(self):
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=1.0)
        portfolio = sizer.size_portfolio(["005930"], total_capital=100_000_000)
        assert portfolio == {}

    def test_size_portfolio_weights_sum(self):
        """포트폴리오 총 비중 확인"""
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=8.0)
        sizer.set_conviction("000660", base_conviction=6.0)
        portfolio = sizer.size_portfolio(
            ["005930", "000660"], total_capital=100_000_000,
        )
        total_weight = sum(p.weight for p in portfolio.values())
        # 각각 0.20으로 capped되므로 총 0.40
        assert total_weight == pytest.approx(0.40, abs=0.01)


# ── 유틸 함수 테스트 ──────────────────────────────────────


class TestClamp:
    def test_clamp_within_range(self):
        assert _clamp(5.0, 1.0, 10.0) == 5.0

    def test_clamp_below(self):
        assert _clamp(-1.0, 1.0, 10.0) == 1.0

    def test_clamp_above(self):
        assert _clamp(15.0, 1.0, 10.0) == 10.0

    def test_clamp_at_boundary(self):
        assert _clamp(1.0, 1.0, 10.0) == 1.0
        assert _clamp(10.0, 1.0, 10.0) == 10.0


# ── 영속화 테스트 ──────────────────────────────────────────


class TestPersistence:
    """JSON 저장/복원 round-trip"""

    def test_save_and_load(self, tmp_path: Path):
        path = str(tmp_path / "conviction.json")
        sizer = ConvictionSizer(max_position_pct=0.15, kelly_fraction=0.4)
        sizer.set_conviction("005930", base_conviction=8.0, catalyst_score=3.0)
        sizer.set_conviction("000660", base_conviction=6.0, kill_conditions_active=1)
        sizer.save(path)

        loaded = ConvictionSizer.load(path)
        assert loaded.max_position_pct == 0.15
        assert loaded.kelly_fraction == 0.4
        assert set(loaded.symbols) == {"005930", "000660"}

    def test_load_conviction_values_preserved(self, tmp_path: Path):
        path = str(tmp_path / "conviction.json")
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=7.0, catalyst_score=5.0)
        sizer.save(path)

        loaded = ConvictionSizer.load(path)
        level = loaded.get_conviction("005930")
        assert level is not None
        assert level.base_conviction == 7.0
        assert level.catalyst_boost == 1.5
        assert level.final_conviction == 8.5

    def test_load_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            ConvictionSizer.load("/nonexistent/path.json")

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        path = str(tmp_path / "sub" / "dir" / "conviction.json")
        sizer = ConvictionSizer()
        sizer.save(path)
        assert Path(path).exists()

    def test_json_has_version(self, tmp_path: Path):
        path = str(tmp_path / "conviction.json")
        sizer = ConvictionSizer()
        sizer.save(path)
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert "version" in data
        assert data["version"] == "1.0.0"

    def test_to_dict_structure(self):
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=7.0)
        d = sizer.to_dict()
        assert "version" in d
        assert "max_position_pct" in d
        assert "min_position_pct" in d
        assert "kelly_fraction" in d
        assert "convictions" in d
        assert "saved_at" in d
        assert "005930" in d["convictions"]

    def test_round_trip_sizing_consistent(self, tmp_path: Path):
        """저장/복원 후 사이징 결과 동일"""
        path = str(tmp_path / "conviction.json")
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=8.0, catalyst_score=4.0)
        pos_before = sizer.size_position("005930", 100_000_000)
        sizer.save(path)

        loaded = ConvictionSizer.load(path)
        pos_after = loaded.size_position("005930", 100_000_000)
        assert pos_before.weight == pos_after.weight
        assert pos_before.amount == pos_after.amount
        assert pos_before.kelly_raw == pos_after.kelly_raw

    def test_repr(self):
        sizer = ConvictionSizer()
        sizer.set_conviction("005930", base_conviction=7.0)
        r = repr(sizer)
        assert "ConvictionSizer" in r
        assert "symbols=1" in r
