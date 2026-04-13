"""
Evaluator v3 FAIL 조건 → 해당 섹션 HEAVY 티어 재생성 루프.

CUFA Evaluator v3 12 binary 체크 키:
    opinion, target_price, stop_loss, position_size, bear_floor,
    kill_conditions, catalyst_timeline, trade_ticket, data_sources,
    backtest_hook, falsifiable_thesis, risk_reward

각 FAIL 키 → narrative 섹션(s) 매핑 → 재생성 루프.
Trade Ticket / backtest_hook은 structured YAML(LLM 무관)에서 담당하지만
narrative 쪽 트리거로도 재생성하여 중복 안전망 확보.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from kis_backtest.luxon.intelligence.router import Tier
from kis_backtest.luxon.intelligence.tasks import cufa_narrative

# Evaluator 키 → 재생성할 섹션 매핑
FAIL_TO_SECTIONS: dict[str, tuple[str, ...]] = {
    "opinion": ("bluf",),
    "target_price": ("bluf",),
    "stop_loss": ("bluf", "trade"),
    "position_size": ("trade",),
    "bear_floor": ("numbers",),
    "kill_conditions": ("risks",),
    "catalyst_timeline": ("thesis",),
    "trade_ticket": ("trade",),
    "data_sources": ("appendix",),
    "backtest_hook": ("trade",),
    "falsifiable_thesis": ("thesis", "risks"),
    "risk_reward": ("trade",),
}


@dataclass(frozen=True)
class RepairAttempt:
    iteration: int
    regenerated_sections: tuple[str, ...]
    failing_before: tuple[str, ...]


@dataclass
class RepairResult:
    sections: dict[str, str]
    attempts: list[RepairAttempt]
    final_failing: tuple[str, ...]

    @property
    def success(self) -> bool:
        return not self.final_failing


def failing_sections(failing_keys: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """FAIL 키 집합 → 재생성 대상 섹션 키 집합(중복 제거)."""
    out: list[str] = []
    for key in failing_keys:
        for sec in FAIL_TO_SECTIONS.get(key, ()):
            if sec not in out:
                out.append(sec)
    return tuple(out)


def repair(
    narratives: dict[str, str],
    failing_keys: tuple[str, ...] | list[str],
    config: Any,
    *,
    use_heavy: bool = True,
) -> dict[str, str]:
    """단일 repair 패스 — 실패 섹션만 재생성 후 덮어쓰기.

    Args:
        narratives: 현재 섹션 HTML 딕셔너리.
        failing_keys: Evaluator failing_keys() 튜플.
        config: CUFA config 객체/dict.
        use_heavy: True 시 gemma4:26b로 재생성(엄격성 ↑).

    Returns:
        갱신된 섹션 딕셔너리 (새 dict).
    """
    updated = dict(narratives)
    targets = failing_sections(failing_keys)
    tier = Tier.HEAVY if use_heavy else Tier.DEFAULT
    for sec in targets:
        try:
            new_html = cufa_narrative.generate_section(
                sec, config, tier_override=tier
            )
            updated[sec] = new_html
        except Exception as exc:  # noqa: BLE001
            # 단일 섹션 실패는 loop에서 재시도 기회 남김
            updated.setdefault(sec, f"<!-- repair error: {exc} -->")
    return updated


def repair_loop(
    narratives: dict[str, str],
    config: Any,
    *,
    evaluate_fn: Callable[[str], Any],
    assemble_fn: Callable[[dict[str, str]], str],
    max_iterations: int = 3,
) -> RepairResult:
    """Evaluate → FAIL 섹션 재생성 → 재평가. PASS 또는 max_iterations까지 반복.

    Args:
        narratives: 초기 섹션 내러티브.
        config: CUFA config.
        evaluate_fn: (html) → EvaluationResult. CUFA evaluator.run.evaluate 연결.
        assemble_fn: (narratives_dict) → 전체 HTML 문자열. local_runner가 제공.
        max_iterations: 최대 재시도 횟수.

    Returns:
        RepairResult(sections, attempts, final_failing).
    """
    attempts: list[RepairAttempt] = []
    current = dict(narratives)

    for i in range(max_iterations + 1):
        html = assemble_fn(current)
        eval_result = evaluate_fn(html)
        failing = tuple(eval_result.failing_keys())
        if not failing:
            return RepairResult(sections=current, attempts=attempts, final_failing=())
        if i == max_iterations:
            return RepairResult(
                sections=current, attempts=attempts, final_failing=failing
            )
        # 마지막 패스는 HEAVY, 그 전은 DEFAULT
        use_heavy = i >= 1
        regen = failing_sections(failing)
        attempts.append(
            RepairAttempt(
                iteration=i + 1,
                regenerated_sections=regen,
                failing_before=failing,
            )
        )
        current = repair(current, failing, config, use_heavy=use_heavy)

    # Unreachable, but defensive
    return RepairResult(sections=current, attempts=attempts, final_failing=())
