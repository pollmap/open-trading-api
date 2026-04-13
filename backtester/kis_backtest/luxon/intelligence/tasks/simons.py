"""
Simons Protocol 평가 태스크 — Jim Simons Medallion 12원칙 기반 결정 필터.

용도:
    - CUFA 보고서 빌드 후 Trade Ticket을 Simons 프로토콜로 재평가
    - 사용자 투자 아이디어 → 12원칙 체크 → PROCEED/REDUCE/REJECT
    - 복기 루프: 실패한 거래의 어느 원칙을 위배했는지 역추적

HEAVY 티어 사용 (gemma4:26b) — 정밀 추론 필요.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from kis_backtest.luxon.intelligence.prompts import load_prompt, split_system_user
from kis_backtest.luxon.intelligence.router import Tier, call


@dataclass(frozen=True)
class PrincipleCheck:
    principle: int  # 1~12
    status: str     # "PASS" | "WARN" | "FAIL"
    reason: str


@dataclass
class SimonsEvaluation:
    simons_score: int  # 0~100
    checks: list[PrincipleCheck] = field(default_factory=list)
    critical_flaws: list[str] = field(default_factory=list)
    recommendation: str = "REDUCE"  # PROCEED | REDUCE | REJECT
    position_adjustment_pct: float = 0.0
    rationale: str = ""
    raw_response: str = ""

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "PASS")

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "FAIL")


def _parse_json(text: str) -> dict[str, Any]:
    # 코드블록 래퍼 제거
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in response")
    return json.loads(text[start : end + 1])


def evaluate(
    decision_context: str,
    facts: str,
    thesis: str,
    position_size_pct: float,
    *,
    tier: Tier = Tier.HEAVY,
) -> SimonsEvaluation:
    """투자 결정을 Simons 12원칙으로 평가.

    Args:
        decision_context: 무슨 결정? ("HD현대중공업 BUY, 목표 800K")
        facts: 구체적 수치·출처
        thesis: 투자 논지 요약
        position_size_pct: 제안 포지션 크기
        tier: HEAVY 권장 (gemma4:26b), 빠르게 하려면 DEFAULT

    Returns:
        SimonsEvaluation — score + 12 checks + recommendation
    """
    prompt_md = load_prompt("simons_protocol")
    system, user_template = split_system_user(prompt_md)
    user = (
        user_template
        .replace("{decision_context}", decision_context)
        .replace("{facts}", facts)
        .replace("{thesis}", thesis)
        .replace("{position_size_pct}", f"{position_size_pct}")
    )

    raw = call(
        tier,
        system=system,
        user=user,
        json_mode=True,
        max_tokens=2000,
        temperature=0.1,
        auto_fallback=True,
    )

    try:
        data = _parse_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        # 파싱 실패 시 안전한 기본값 (REJECT)
        return SimonsEvaluation(
            simons_score=0,
            recommendation="REJECT",
            rationale=f"평가 파싱 실패: {exc}",
            raw_response=raw,
        )

    checks: list[PrincipleCheck] = []
    for c in data.get("checks", []):
        if not isinstance(c, dict):
            continue
        try:
            checks.append(PrincipleCheck(
                principle=int(c.get("principle", 0)),
                status=str(c.get("status", "FAIL")).upper(),
                reason=str(c.get("reason", "")),
            ))
        except (TypeError, ValueError):
            continue

    return SimonsEvaluation(
        simons_score=int(data.get("simons_score", 0)),
        checks=checks,
        critical_flaws=list(data.get("critical_flaws", [])),
        recommendation=str(data.get("recommendation", "REDUCE")).upper(),
        position_adjustment_pct=float(data.get("position_adjustment_pct", 0)),
        rationale=str(data.get("rationale", "")),
        raw_response=raw,
    )


def evaluate_trade_ticket(ticket_dict: dict, config: dict) -> SimonsEvaluation:
    """CUFA Trade Ticket을 바로 Simons 평가에 투입."""
    meta = config.get("META", {})
    thesis_items = config.get("THESIS", [])
    thesis_text = "\n".join(
        f"- {t.get('title', '')}: {t.get('summary', '')}"
        for t in thesis_items[:3]
    )

    facts = (
        f"현재가: {config.get('PRICE', {}).get('current', 'N/A')}원\n"
        f"목표주가: {ticket_dict.get('target_price', 'N/A')}원\n"
        f"손절가: {ticket_dict.get('stop_loss', 'N/A')}원\n"
        f"Risk/Reward: {ticket_dict.get('risk_reward', 'N/A')}\n"
        f"WACC: {config.get('WACC', 'N/A')}%"
    )

    return evaluate(
        decision_context=f"{meta.get('company_name', '')} "
                         f"({meta.get('ticker', '')}) "
                         f"{ticket_dict.get('opinion', 'HOLD')}",
        facts=facts,
        thesis=thesis_text,
        position_size_pct=float(ticket_dict.get("position_size_pct", 0)),
    )
