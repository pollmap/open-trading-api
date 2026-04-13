"""
로컬 LLM 스택 벤치마크 — 3 티어 TPS + 레이턴시 측정.

실 엔드포인트 필요. 헬스체크 실패 시 해당 티어 스킵.

사용:
    python -m kis_backtest.luxon.intelligence.bench
    python -m kis_backtest.luxon.intelligence.bench --tier FAST
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass

from kis_backtest.luxon.intelligence.router import (
    LocalLLMError,
    Tier,
    call,
    estimate_tokens,
    health_check,
)

_PROMPTS = [
    ("시그널 요약", "삼성전자 RSI 28, 볼밴 하단 터치. 한 문장으로 요약."),
    ("분류", "이 뉴스는 호재/악재/중립 중 하나로만 답해: 매출 +45% YoY."),
    ("짧은 서술", "PER 15배 기업의 밸류에이션 정당성을 2문장으로."),
]


@dataclass
class TierBenchResult:
    tier_name: str
    reachable: bool
    latencies_ms: list[float]
    tokens_out: list[int]
    errors: list[str]

    @property
    def mean_latency_ms(self) -> float:
        return statistics.mean(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def tps(self) -> float:
        total_tokens = sum(self.tokens_out)
        total_sec = sum(self.latencies_ms) / 1000.0
        return total_tokens / total_sec if total_sec > 0 else 0.0


def bench_tier(tier: Tier, prompts=_PROMPTS) -> TierBenchResult:
    result = TierBenchResult(
        tier_name=tier.value.name,
        reachable=health_check(tier, timeout=3.0),
        latencies_ms=[],
        tokens_out=[],
        errors=[],
    )
    if not result.reachable:
        result.errors.append("endpoint unreachable")
        return result

    for _label, user in prompts:
        t0 = time.perf_counter()
        try:
            out = call(
                tier,
                system="간결하게 한국어로 답변.",
                user=user,
                max_tokens=200,
                temperature=0.3,
                auto_fallback=False,
            )
            dt = (time.perf_counter() - t0) * 1000.0
            result.latencies_ms.append(dt)
            result.tokens_out.append(estimate_tokens(out))
        except LocalLLMError as exc:
            result.errors.append(f"{_label}: {exc}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tier",
        choices=["FAST", "DEFAULT", "HEAVY", "ALT", "ALL"],
        default="ALL",
    )
    args = parser.parse_args()

    tiers = [Tier[args.tier]] if args.tier != "ALL" else list(Tier)
    print(f"{'Tier':<10s} {'Reachable':<10s} {'Mean(ms)':>12s} {'TPS':>10s}  Errors")
    print("-" * 80)
    for t in tiers:
        r = bench_tier(t)
        err_s = "none" if not r.errors else f"{len(r.errors)} err"
        print(
            f"{r.tier_name:<10s} {str(r.reachable):<10s} "
            f"{r.mean_latency_ms:>12.1f} {r.tps:>10.2f}  {err_s}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
