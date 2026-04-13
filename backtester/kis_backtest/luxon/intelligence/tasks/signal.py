"""
시그널 코멘터리 — FAST 티어(NPU qwen3.5:4b) 용.

퀀트 시그널 JSON → 한 문장 한국어 요약.
"""
from __future__ import annotations

import json
from typing import Any

from kis_backtest.luxon.intelligence.prompts import load_prompt, split_system_user
from kis_backtest.luxon.intelligence.router import Tier, call


def _build_user_prompt(signal: dict[str, Any], template: str) -> str:
    signal_json = json.dumps(signal, ensure_ascii=False, indent=None)
    return template.replace("{signal_json}", signal_json)


def commentary(signal: dict[str, Any], *, tier: Tier = Tier.FAST) -> str:
    """
    시그널 → 한 문장 코멘터리.

    Args:
        signal: {ticker, action, rsi, ...} 같은 딕셔너리.
        tier: 기본 FAST(NPU). 네트워크 느릴 때 DEFAULT로 승격 가능.

    Returns:
        한 문장 한국어 코멘터리 (60자 이내 권장, 검증 안 함).

    Raises:
        LocalLLMError: 엔드포인트 실패 또는 빈 응답.
    """
    prompt_md = load_prompt("signal")
    system, user_template = split_system_user(prompt_md)
    user = _build_user_prompt(signal, user_template)
    return call(
        tier,
        system=system,
        user=user,
        max_tokens=128,
        temperature=0.2,
        auto_fallback=False,  # FAST는 NPU 전용
    ).strip()
