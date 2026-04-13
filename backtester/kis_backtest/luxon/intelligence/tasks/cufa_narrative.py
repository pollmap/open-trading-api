"""
CUFA §1~§7 내러티브 생성 — Evaluator v3 12 binary 조건 통과 설계.

각 섹션은 DEFAULT 티어(qwen3:14b)로 생성.
§2 Thesis는 Falsifiable 조건 엄격성 때문에 HEAVY(gemma4:26b) 옵션 제공(ctx 4096).

반환 스키마:
    {
      "bluf":     "<p>...</p>",
      "thesis":   "<h4>...</h4>...",
      "business": "...",
      "numbers":  "...",
      "risks":    "<h4>Kill Conditions</h4><ul>...</ul>",
      "trade":    "...",
      "appendix": "..."
    }

CUFA `sections/base.py` → SectionData.narrative_html 로 주입.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from kis_backtest.luxon.intelligence.prompts import load_prompt, split_system_user
from kis_backtest.luxon.intelligence.router import Tier, call

# ── 섹션 스펙 ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class SectionSpec:
    key: str  # narratives 딕셔너리 키
    prompt: str  # 프롬프트 파일명 (확장자 제외)
    tier: Tier
    max_tokens: int
    extract: Callable[[dict[str, Any]], dict[str, str]]


def _fmt_thesis_summary(config: dict) -> str:
    axes = config.get("THESIS", [])
    if not axes:
        return "논지 데이터 미제공"
    return " | ".join(
        f"{a.get('title', '')} — {a.get('summary', '')[:80]}"
        for a in axes[:3]
    )


def _fmt_thesis_axes(config: dict) -> str:
    axes = config.get("THESIS", [])
    lines = []
    for i, a in enumerate(axes[:3], 1):
        lines.append(f"{i}. {a.get('title', '')}")
        lines.append(f"   근거: {a.get('evidence', '')}")
        lines.append(f"   반증: {a.get('falsify', '')}")
    return "\n".join(lines) if lines else "축 데이터 미제공"


def _fmt_catalyst_list(config: dict) -> str:
    items = config.get("CATALYST_TIMELINE", [])
    if not items:
        return "Catalyst 데이터 미제공 — 최소 3건 합리적 추정 허용"
    lines = []
    for c in items[:8]:
        date = c.get("date", "TBD")
        event = c.get("event", "")
        delta = c.get("upside_delta_pct", 0)
        lines.append(f"{date} - {event} (기대 영향: +{delta}%)")
    return "\n".join(lines)


def _fmt_segments(config: dict) -> str:
    biz = config.get("BUSINESS", {})
    segs = biz.get("segments", [])
    if not segs:
        return "세그먼트 데이터 미제공"
    return ", ".join(
        f"{s.get('name', '')}({s.get('revenue_pct', 0)}%)"
        for s in segs
    )


def _fmt_scenarios(config: dict) -> str:
    scens = config.get("VALUATION_SCENARIOS", {})
    out = []
    for name in ("bear", "base", "bull"):
        sc = scens.get(name, {})
        out.append(f"{name}: {sc.get('price', '?')}원 (확률 {sc.get('prob_pct', '?')}%)")
    return ", ".join(out)


def _fmt_sources(config: dict) -> str:
    srcs = config.get("DATA_SOURCES", ["DART", "KRX", "Nexus MCP"])
    return ", ".join(srcs) if srcs else "DART, KRX, Nexus MCP"


# Sprint E 바벨 전략 재매핑:
#   Thesis/Numbers/Risks → HEAVY (정밀 요구)
#   Business → LONG (세그먼트 데이터 풍부, 32k)
#   BLUF/Trade/Appendix → DEFAULT (템플릿성)
SECTION_SPECS: tuple[SectionSpec, ...] = (
    SectionSpec(
        key="bluf",
        prompt="cufa_bluf",
        tier=Tier.DEFAULT,
        max_tokens=600,
        extract=lambda c: {
            "company_name": str(c.get("META", {}).get("company_name", "")),
            "ticker": str(c.get("META", {}).get("ticker", "")),
            "current_price": str(c.get("PRICE", {}).get("current", 0)),
            "opinion": str(c.get("trade_ticket", {}).get("opinion", "HOLD")),
            "target_price": str(c.get("TARGET_PRICE", {}).get("weighted", 0)),
            "stop_loss": str(c.get("trade_ticket", {}).get("stop_loss", 0)),
            "thesis_summary": _fmt_thesis_summary(c),
        },
    ),
    SectionSpec(
        key="thesis",
        prompt="cufa_thesis",
        tier=Tier.HEAVY,
        max_tokens=1200,
        extract=lambda c: {
            "company_name": str(c.get("META", {}).get("company_name", "")),
            "thesis_axes": _fmt_thesis_axes(c),
            "catalyst_list": _fmt_catalyst_list(c),
        },
    ),
    SectionSpec(
        key="business",
        prompt="cufa_business",
        tier=Tier.LONG,
        max_tokens=1500,
        extract=lambda c: {
            "company_name": str(c.get("META", {}).get("company_name", "")),
            "segments": _fmt_segments(c),
            "key_metrics": json.dumps(
                c.get("BUSINESS", {}).get("key_metrics", {}), ensure_ascii=False
            ),
            "moat_keys": ", ".join(c.get("BUSINESS", {}).get("moat_keys", [])),
        },
    ),
    SectionSpec(
        key="numbers",
        prompt="cufa_numbers",
        tier=Tier.HEAVY,
        max_tokens=900,
        extract=lambda c: {
            "company_name": str(c.get("META", {}).get("company_name", "")),
            "current_price": str(c.get("PRICE", {}).get("current", 0)),
            "target_price": str(c.get("TARGET_PRICE", {}).get("weighted", 0)),
            "bear_price": str(c.get("VALUATION_SCENARIOS", {}).get("bear", {}).get("price", 0)),
            "bear_prob": str(c.get("VALUATION_SCENARIOS", {}).get("bear", {}).get("prob_pct", 0)),
            "base_price": str(c.get("VALUATION_SCENARIOS", {}).get("base", {}).get("price", 0)),
            "base_prob": str(c.get("VALUATION_SCENARIOS", {}).get("base", {}).get("prob_pct", 0)),
            "bull_price": str(c.get("VALUATION_SCENARIOS", {}).get("bull", {}).get("price", 0)),
            "bull_prob": str(c.get("VALUATION_SCENARIOS", {}).get("bull", {}).get("prob_pct", 0)),
            "peer_summary": str(c.get("PEERS", {}).get("summary", "Peer 데이터 미제공")),
            "wacc": str(c.get("WACC", 9.0)),
        },
    ),
    SectionSpec(
        key="risks",
        prompt="cufa_risks",
        tier=Tier.HEAVY,
        max_tokens=900,
        extract=lambda c: {
            "company_name": str(c.get("META", {}).get("company_name", "")),
            "thesis_summary": _fmt_thesis_summary(c),
            "risk_factors": "\n".join(c.get("RISK_MATRIX", {}).get("factors", [])) or "리스크 데이터 미제공",
            "bear_scenario": str(c.get("VALUATION_SCENARIOS", {}).get("bear", {}).get("condition", "")),
            "eps_sensitivity": json.dumps(
                c.get("EPS_SENSITIVITY", {}), ensure_ascii=False
            ),
        },
    ),
    SectionSpec(
        key="trade",
        prompt="cufa_trade",
        tier=Tier.DEFAULT,
        max_tokens=700,
        extract=lambda c: {
            "company_name": str(c.get("META", {}).get("company_name", "")),
            "entry_price": str(c.get("trade_ticket", {}).get("entry_price", 0)),
            "target_price": str(c.get("TARGET_PRICE", {}).get("weighted", 0)),
            "stop_loss": str(c.get("trade_ticket", {}).get("stop_loss", 0)),
            "position_size_pct": str(c.get("trade_ticket", {}).get("position_size_pct", 5)),
            "risk_reward": str(c.get("TARGET_PRICE", {}).get("risk_reward", 2.0)),
            "horizon_months": str(c.get("trade_ticket", {}).get("horizon_months", 12)),
        },
    ),
    SectionSpec(
        key="appendix",
        prompt="cufa_appendix",
        tier=Tier.DEFAULT,
        max_tokens=500,
        extract=lambda c: {
            "company_name": str(c.get("META", {}).get("company_name", "")),
            "data_sources": _fmt_sources(c),
            "valuation_methods": ", ".join(
                c.get("VALUATION", {}).get("methods", ["DCF", "Peer Multiple"])
            ),
        },
    ),
)


# ── 렌더링 ────────────────────────────────────────────────────────


def _render_user_prompt(template: str, variables: dict[str, str]) -> str:
    out = template
    for key, value in variables.items():
        out = out.replace("{" + key + "}", value)
    return out


def _config_to_dict(config: Any) -> dict[str, Any]:
    """Python module/객체/dict 전부 수용."""
    if isinstance(config, dict):
        return config
    out: dict[str, Any] = {}
    for name in (
        "META", "PRICE", "TARGET_PRICE", "THESIS", "BUSINESS", "INDUSTRY_DATA",
        "IS_CFS", "PEERS", "WACC", "VALUATION_SCENARIOS", "RISK_MATRIX",
        "EPS_SENSITIVITY", "CATALYST_TIMELINE", "trade_ticket", "DATA_SOURCES",
        "VALUATION", "KILL_CONDITIONS",
    ):
        if hasattr(config, name):
            out[name] = getattr(config, name)
    return out


# ── 상위 API ──────────────────────────────────────────────────────


def generate_section(
    section_key: str,
    config: Any,
    *,
    tier_override: Tier | None = None,
) -> str:
    """단일 섹션 HTML 내러티브 생성."""
    spec = next((s for s in SECTION_SPECS if s.key == section_key), None)
    if spec is None:
        raise ValueError(f"Unknown section key: {section_key}")

    cfg = _config_to_dict(config)
    prompt_md = load_prompt(spec.prompt)
    system, user_template = split_system_user(prompt_md)
    variables = spec.extract(cfg)
    user = _render_user_prompt(user_template, variables)

    return call(
        tier_override or spec.tier,
        system=system,
        user=user,
        max_tokens=spec.max_tokens,
        temperature=0.3,
        auto_fallback=True,
    ).strip()


@dataclass
class NarrativeResult:
    """7섹션 생성 결과 + 실패 로그."""
    sections: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        keys = {s.key for s in SECTION_SPECS}
        return set(self.sections.keys()) == keys and not self.errors


def generate_all(
    config: Any,
    *,
    skip_on_error: bool = False,
    force_all_heavy: bool = False,
    heavy_for_thesis: bool = False,  # 하위호환 (Sprint E 후 불필요)
) -> NarrativeResult:
    """7섹션 전부 순차 생성 — 바벨 하이브리드 라우팅 (Sprint E).

    섹션별 기본 티어 (SECTION_SPECS):
        BLUF / Trade / Appendix → DEFAULT (qwen3:14b)
        Thesis / Numbers / Risks → HEAVY (gemma4:26b)
        Business → LONG (gemma4-e4b iGPU)

    Args:
        config: CUFA config 모듈/dict.
        skip_on_error: True 시 섹션 실패해도 다음 섹션 진행.
        force_all_heavy: True 시 모든 섹션을 HEAVY로 (최고 품질, 느림).
        heavy_for_thesis: 하위호환 — thesis는 이미 HEAVY라 무효.

    Returns:
        NarrativeResult(sections={key: html}, errors={key: msg}).
    """
    result = NarrativeResult()
    for spec in SECTION_SPECS:
        tier = Tier.HEAVY if force_all_heavy else spec.tier
        try:
            html = generate_section(spec.key, config, tier_override=tier)
            result.sections[spec.key] = html
        except Exception as exc:  # noqa: BLE001
            result.errors[spec.key] = f"{type(exc).__name__}: {exc}"
            if not skip_on_error:
                raise
    return result
