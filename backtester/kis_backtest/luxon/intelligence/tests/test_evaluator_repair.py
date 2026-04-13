"""Evaluator repair 루프 단위 테스트."""
from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest

from kis_backtest.luxon.intelligence.tasks import evaluator_repair
from kis_backtest.luxon.intelligence.tasks.evaluator_repair import (
    FAIL_TO_SECTIONS,
    RepairAttempt,
    RepairResult,
    failing_sections,
    repair,
    repair_loop,
)
from kis_backtest.luxon.intelligence.tests.fixtures.sample_config import (
    build_sample_config,
)


# ── FAIL 매핑 ────────────────────────────────────────────────────


class TestFailingSections:
    def test_all_12_eval_keys_mapped(self):
        required = {
            "opinion", "target_price", "stop_loss", "position_size",
            "bear_floor", "kill_conditions", "catalyst_timeline",
            "trade_ticket", "data_sources", "backtest_hook",
            "falsifiable_thesis", "risk_reward",
        }
        assert required == set(FAIL_TO_SECTIONS.keys())

    def test_kill_conditions_maps_to_risks(self):
        assert "risks" in FAIL_TO_SECTIONS["kill_conditions"]

    def test_catalyst_timeline_maps_to_thesis(self):
        assert "thesis" in FAIL_TO_SECTIONS["catalyst_timeline"]

    def test_falsifiable_thesis_maps_to_two_sections(self):
        sections = FAIL_TO_SECTIONS["falsifiable_thesis"]
        assert "thesis" in sections and "risks" in sections

    def test_deduplicates_across_keys(self):
        # stop_loss → (bluf, trade), position_size → (trade,)
        # 결과: bluf, trade (중복 제거)
        result = failing_sections(("stop_loss", "position_size"))
        assert result == ("bluf", "trade")

    def test_unknown_key_ignored(self):
        result = failing_sections(("unknown_key",))
        assert result == ()


# ── repair() 단일 패스 ────────────────────────────────────────────


class TestRepair:
    def test_regenerates_only_failing_sections(self, monkeypatch):
        calls: list[str] = []

        def fake_generate(section_key, config, tier_override=None):
            calls.append(section_key)
            return f"<p>regenerated {section_key}</p>"

        monkeypatch.setattr(
            evaluator_repair.cufa_narrative, "generate_section", fake_generate
        )
        narratives = {
            "bluf": "old bluf",
            "thesis": "old thesis",
            "risks": "old risks",
            "trade": "old trade",
        }
        updated = repair(
            narratives, ("kill_conditions",), build_sample_config(), use_heavy=False
        )
        assert calls == ["risks"]
        assert updated["risks"] == "<p>regenerated risks</p>"
        assert updated["bluf"] == "old bluf"  # 변경 없음

    def test_uses_heavy_tier_when_requested(self, monkeypatch):
        captured_tier = []

        def fake_generate(section_key, config, tier_override=None):
            captured_tier.append(tier_override)
            return "<p>x</p>"

        monkeypatch.setattr(
            evaluator_repair.cufa_narrative, "generate_section", fake_generate
        )
        repair({}, ("kill_conditions",), build_sample_config(), use_heavy=True)
        from kis_backtest.luxon.intelligence.router import Tier

        assert captured_tier[0] == Tier.HEAVY

    def test_exception_leaves_placeholder(self, monkeypatch):
        def fake_generate(section_key, config, tier_override=None):
            raise RuntimeError("model down")

        monkeypatch.setattr(
            evaluator_repair.cufa_narrative, "generate_section", fake_generate
        )
        updated = repair({}, ("opinion",), build_sample_config())
        assert "repair error" in updated["bluf"]


# ── repair_loop() ────────────────────────────────────────────────


@dataclass
class _FakeEvalResult:
    _failing: tuple[str, ...]

    def failing_keys(self) -> tuple[str, ...]:
        return self._failing


class TestRepairLoop:
    def test_stops_immediately_on_all_pass(self):
        narratives = {"bluf": "x"}
        evals = [(_FakeEvalResult(()))]
        attempts_seen = []

        def evaluate_fn(html):
            return evals.pop(0)

        def assemble_fn(sections):
            return "<html>" + sections.get("bluf", "") + "</html>"

        result = repair_loop(
            narratives, build_sample_config(),
            evaluate_fn=evaluate_fn, assemble_fn=assemble_fn,
            max_iterations=3,
        )
        assert result.success
        assert result.sections == narratives
        assert result.attempts == []

    def test_repairs_until_pass(self, monkeypatch):
        def fake_generate(section_key, config, tier_override=None):
            return f"<p>fixed-{section_key}</p>"

        monkeypatch.setattr(
            evaluator_repair.cufa_narrative, "generate_section", fake_generate
        )

        # 1차: kill_conditions FAIL, 2차: ALL PASS
        eval_sequence = [
            _FakeEvalResult(("kill_conditions",)),
            _FakeEvalResult(()),
        ]

        def evaluate_fn(html):
            return eval_sequence.pop(0)

        def assemble_fn(sections):
            return "<html></html>"

        result = repair_loop(
            {"risks": "broken"},
            build_sample_config(),
            evaluate_fn=evaluate_fn, assemble_fn=assemble_fn,
            max_iterations=3,
        )
        assert result.success
        assert len(result.attempts) == 1
        assert "risks" in result.sections
        assert result.sections["risks"] == "<p>fixed-risks</p>"

    def test_gives_up_after_max_iterations(self, monkeypatch):
        def fake_generate(section_key, config, tier_override=None):
            return "still broken"

        monkeypatch.setattr(
            evaluator_repair.cufa_narrative, "generate_section", fake_generate
        )

        # 계속 FAIL
        def evaluate_fn(html):
            return _FakeEvalResult(("opinion",))

        def assemble_fn(sections):
            return "<html></html>"

        result = repair_loop(
            {},
            build_sample_config(),
            evaluate_fn=evaluate_fn, assemble_fn=assemble_fn,
            max_iterations=2,
        )
        assert not result.success
        assert result.final_failing == ("opinion",)
        assert len(result.attempts) == 2
