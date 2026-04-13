"""
뉴스 텍스트 → Catalyst 이벤트 JSON 추출.

DEFAULT 티어 기본(JSON 모드 지원). FAST 티어로 스루풋 우선 가능.
CUFA config의 CATALYST_TIMELINE 필드에 바로 주입 가능한 스키마.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from kis_backtest.luxon.intelligence.prompts import load_prompt, split_system_user
from kis_backtest.luxon.intelligence.router import Tier, call


@dataclass(frozen=True)
class CatalystEvent:
    date: str  # "Q2 2026" 등 Evaluator 검출 가능 형식
    event: str
    upside_delta_pct: float = 0.0


@dataclass
class CatalystExtractionResult:
    events: list[CatalystEvent] = field(default_factory=list)
    raw_response: str = ""
    parse_error: str | None = None


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    """응답에서 JSON 배열 추출. 모델이 코드블록 래핑 시 제거."""
    # ```json ... ``` 제거
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    # 첫 [ 부터 마지막 ] 까지
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON array in response")
    candidate = text[start : end + 1]
    data = json.loads(candidate)
    if not isinstance(data, list):
        raise ValueError("Root is not a JSON array")
    return data


_DATE_PATTERN = re.compile(
    r"^(Q[1-4]\s+20\d{2}|20\d{2}년\s*[1-4]분기|H[12]\s+20\d{2})$"
)


def _valid_date_format(date: str) -> bool:
    return bool(_DATE_PATTERN.match(date.strip()))


def extract(
    news_text: str,
    *,
    tier: Tier = Tier.DEFAULT,
    strict_date: bool = True,
) -> CatalystExtractionResult:
    """뉴스 텍스트 → CatalystEvent 리스트.

    Args:
        news_text: 원문 뉴스/공시.
        tier: DEFAULT 권장. FAST는 짧은 뉴스 전용.
        strict_date: True 시 날짜 형식 불일치 이벤트 drop.

    Returns:
        CatalystExtractionResult(events, raw_response, parse_error).
    """
    prompt_md = load_prompt("catalyst")
    system, user_template = split_system_user(prompt_md)
    user = user_template.replace("{news_text}", news_text[:4000])  # 안전 컷

    raw = call(
        tier,
        system=system,
        user=user,
        json_mode=False,  # 모델이 array로 응답 → json_mode는 객체 강제. 일반 모드 선호.
        max_tokens=600,
        temperature=0.1,
        auto_fallback=True,
    )

    try:
        items = _parse_json_array(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return CatalystExtractionResult(raw_response=raw, parse_error=str(exc))

    events: list[CatalystEvent] = []
    for it in items[:5]:
        if not isinstance(it, dict):
            continue
        date = str(it.get("date", "")).strip()
        event = str(it.get("event", "")).strip()
        if not date or not event:
            continue
        if strict_date and not _valid_date_format(date):
            continue
        try:
            delta = float(it.get("upside_delta_pct", 0) or 0)
        except (TypeError, ValueError):
            delta = 0.0
        events.append(CatalystEvent(date=date, event=event, upside_delta_pct=delta))

    return CatalystExtractionResult(events=events, raw_response=raw)
