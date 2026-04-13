"""
Luxon Intelligence Router — 바벨 전략 4-티어 로컬 LLM 통합.

Sprint E 변경:
    - DEFAULT ctx 8k → 16k (qwen3:14b)
    - HEAVY   ctx 4k → 8k  (gemma4:26b, KV q8 필수)
    - ALT → LONG 승격, ctx 32k (gemma4-e4b iGPU, 긴 문서 전담)
    - Ollama 엔드포인트 감지 시 native /api/chat + options.num_ctx 전달
    - 폴백 체인: DEFAULT 다운 → LONG (기존 ALT 역할 유지)

바벨 전략:
    FAST   (NPU 4k, 초경량)   — 시그널·알림·분류
    HEAVY  (CPU 8k, 초정밀)   — Falsifiable thesis, Kill condition
    LONG   (iGPU 32k, 초장문) — CUFA Business, RAG, 뉴스 대량 수집
    중간 DEFAULT는 유지 (ctx 16k — 일반 섹션/에이전트 작업)

설계 원칙:
    1. 클라우드 폴백 없음. 로컬 실패 = 명시적 raise.
    2. HEAVY/FAST/LONG ctx 엄격 가드.
    3. Ollama는 native API 사용하여 num_ctx 실효 보장.
    4. httpx 직접 호출 — openai SDK 선택 의존성.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx

# ── Tier 정의 ──────────────────────────────────────────────────────

HEAVY_CTX_LIMIT: int = 8192  # gemma4:26b KV q8 확장
DEFAULT_CTX_LIMIT: int = 16384  # qwen3:14b 실용적 상한
FAST_CTX_LIMIT: int = 4096  # qwen3.5:4b NPU 드라이버 제약
LONG_CTX_LIMIT: int = 32768  # gemma4-e4b native 128k 중 안전값


@dataclass(frozen=True)
class TierConfig:
    name: str
    base_url: str
    model: str
    ctx_limit: int
    num_ctx: int  # Ollama options.num_ctx (OpenAI 호환 엔드포인트는 무시)
    timeout: float  # seconds
    runtime: str  # "ollama" | "flm" | "koboldcpp"


class Tier(Enum):
    FAST = TierConfig(
        name="FAST",
        base_url="http://127.0.0.1:52625/v1",
        model="qwen3.5:4b",
        ctx_limit=FAST_CTX_LIMIT,
        num_ctx=FAST_CTX_LIMIT,
        timeout=60.0,
        runtime="flm",
    )
    DEFAULT = TierConfig(
        name="DEFAULT",
        base_url="http://127.0.0.1:11434",  # native Ollama 루트
        model="qwen3:14b",
        ctx_limit=DEFAULT_CTX_LIMIT,
        num_ctx=DEFAULT_CTX_LIMIT,
        timeout=900.0,
        runtime="ollama",
    )
    HEAVY = TierConfig(
        name="HEAVY",
        base_url="http://127.0.0.1:11434",
        model="gemma4:26b",
        ctx_limit=HEAVY_CTX_LIMIT,
        num_ctx=HEAVY_CTX_LIMIT,
        timeout=1200.0,
        runtime="ollama",
    )
    LONG = TierConfig(
        name="LONG",
        base_url="http://127.0.0.1:5001/v1",
        model="koboldcpp/gemma-4-e4b-it-Q4_K_M",
        ctx_limit=LONG_CTX_LIMIT,
        num_ctx=LONG_CTX_LIMIT,
        timeout=600.0,
        runtime="koboldcpp",
    )


# ── 예외 ──────────────────────────────────────────────────────────


class LocalLLMError(RuntimeError):
    """로컬 LLM 호출 실패. 클라우드 폴백 없음."""


class TierUnavailableError(LocalLLMError):
    """특정 티어 엔드포인트 도달 실패(연결 거부, 타임아웃)."""


class ContextLimitExceededError(LocalLLMError):
    """프롬프트가 티어 ctx_limit 초과."""


# ── 토큰 추정 ─────────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """대략적 토큰 카운트. tiktoken 의존성 회피.

    경험칙: 영어 ~4 chars/token, 한국어 ~1.5 chars/token.
    한글 30%+ 혼재 시 한국어 계산식 적용.
    """
    if not text:
        return 0
    total = len(text)
    kr = sum(1 for c in text if "\uac00" <= c <= "\ud7a3")
    if kr / total > 0.3:
        return int(total / 1.5) + 1
    return int(total / 4) + 1


# ── 엔드포인트별 호출 경로 ────────────────────────────────────────


def _ollama_chat_url(cfg: TierConfig) -> str:
    return f"{cfg.base_url}/api/chat"


def _openai_chat_url(cfg: TierConfig) -> str:
    base = cfg.base_url.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return f"{base}/chat/completions"


def _health_url(cfg: TierConfig) -> str:
    if cfg.runtime == "ollama":
        return f"{cfg.base_url}/api/tags"
    base = cfg.base_url.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return f"{base}/models"


# ── Ollama native 호출 ────────────────────────────────────────────


def _ollama_payload(
    cfg: TierConfig,
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None,
    temperature: float,
    json_mode: bool,
    tools: list[dict[str, Any]] | None = None,
    think: bool = False,
) -> dict[str, Any]:
    options: dict[str, Any] = {"num_ctx": cfg.num_ctx, "temperature": temperature}
    if max_tokens is not None:
        options["num_predict"] = max_tokens
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "options": options,
        "stream": False,
        "think": think,  # qwen3 등 추론 모델 thinking 모드 off (content 직접 받기)
    }
    if json_mode:
        payload["format"] = "json"
    if tools:
        payload["tools"] = tools
    return payload


def _extract_ollama_content(data: dict[str, Any]) -> str:
    """Ollama 응답에서 최종 답 추출.

    우선순위: content > thinking (think=False여도 일부 모델이 thinking에 답 배치).
    둘 다 비어있으면 "" 반환.
    """
    msg = data.get("message") or {}
    content = msg.get("content", "")
    if isinstance(content, str) and content.strip():
        return content
    # thinking fallback (모델이 content 없이 thinking에만 답 넣는 케이스)
    thinking = msg.get("thinking", "")
    if isinstance(thinking, str) and thinking.strip():
        return thinking
    return content if isinstance(content, str) else ""


# ── OpenAI 호환 호출 ──────────────────────────────────────────────


def _openai_payload(
    cfg: TierConfig,
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None,
    temperature: float,
    json_mode: bool,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if tools:
        payload["tools"] = tools
    return payload


def _extract_openai_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = (choices[0] or {}).get("message") or {}
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    return ""


# ── 단일 호출 ─────────────────────────────────────────────────────


def _call_once(
    tier: Tier,
    messages: list[dict[str, str]],
    *,
    json_mode: bool,
    max_tokens: int | None,
    temperature: float,
    tools: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """단일 티어 엔드포인트 호출.

    Returns:
        (content, raw_response_dict). raw_response_dict는 tool_calls 추출용.
    """
    cfg = tier.value
    if cfg.runtime == "ollama":
        url = _ollama_chat_url(cfg)
        payload = _ollama_payload(
            cfg, messages,
            max_tokens=max_tokens, temperature=temperature,
            json_mode=json_mode, tools=tools,
        )
    else:
        url = _openai_chat_url(cfg)
        payload = _openai_payload(
            cfg, messages,
            max_tokens=max_tokens, temperature=temperature,
            json_mode=json_mode, tools=tools,
        )

    try:
        with httpx.Client(timeout=cfg.timeout) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError as exc:
        raise TierUnavailableError(
            f"{cfg.name} unreachable at {cfg.base_url}: {exc}"
        ) from exc
    except httpx.TimeoutException as exc:
        raise TierUnavailableError(
            f"{cfg.name} timed out after {cfg.timeout}s: {exc}"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise LocalLLMError(
            f"{cfg.name} HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        ) from exc

    if cfg.runtime == "ollama":
        content = _extract_ollama_content(data)
    else:
        content = _extract_openai_content(data)

    if not isinstance(content, str):
        raise LocalLLMError(f"{cfg.name} malformed content type: {type(content).__name__}")
    return content, data


# ── 상위 API: call ───────────────────────────────────────────────


def call(
    tier: Tier,
    *,
    system: str,
    user: str,
    json_mode: bool = False,
    max_tokens: int | None = None,
    temperature: float = 0.3,
    auto_fallback: bool = True,
) -> str:
    """로컬 LLM 호출 — 텍스트 생성 전용.

    Args:
        tier: FAST / DEFAULT / HEAVY / LONG.
        system, user: 프롬프트.
        json_mode: Ollama는 format=json, OpenAI는 response_format.
        max_tokens: 응답 상한 (Ollama num_predict).
        temperature: 0.0 결정론적, 0.7 창작적.
        auto_fallback: DEFAULT 실패 시 LONG(gemma4-e4b) 자동 시도.

    Raises:
        ContextLimitExceededError / TierUnavailableError / LocalLLMError.
    """
    cfg = tier.value
    prompt_tokens = estimate_tokens(system) + estimate_tokens(user)
    budget = cfg.ctx_limit - (max_tokens or 1024)
    if prompt_tokens > budget:
        raise ContextLimitExceededError(
            f"{cfg.name} ctx_limit={cfg.ctx_limit}, prompt≈{prompt_tokens} tokens "
            f"(+ max_tokens={max_tokens or 1024}). Use LONG tier or split prompt."
        )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    try:
        content, _ = _call_once(
            tier, messages,
            json_mode=json_mode, max_tokens=max_tokens, temperature=temperature,
        )
    except TierUnavailableError:
        # Sprint E 정책: NPU(FAST) 실패 시 CPU(DEFAULT) 자동 폴백.
        # LONG/HEAVY는 수동 전용 (사용자 정책: iGPU Kobold = manual path).
        if auto_fallback and tier == Tier.FAST:
            content, _ = _call_once(
                Tier.DEFAULT, messages,
                json_mode=json_mode, max_tokens=max_tokens, temperature=temperature,
            )
        else:
            raise

    if not content.strip():
        raise LocalLLMError(f"{cfg.name} returned empty content")
    return content


# ── Sprint F 예비: tool-calling ──────────────────────────────────


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatResult:
    content: str
    tool_calls: tuple[ToolCall, ...]
    raw: dict[str, Any]


def _extract_tool_calls(data: dict[str, Any], runtime: str) -> tuple[ToolCall, ...]:
    """Ollama / OpenAI 응답에서 tool_calls 파싱. 없으면 빈 튜플."""
    if runtime == "ollama":
        msg = data.get("message") or {}
        raw_calls = msg.get("tool_calls") or []
    else:
        choices = data.get("choices") or []
        msg = (choices[0] or {}).get("message") or {} if choices else {}
        raw_calls = msg.get("tool_calls") or []

    out: list[ToolCall] = []
    for i, tc in enumerate(raw_calls):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = fn.get("name") or tc.get("name") or ""
        args_raw = fn.get("arguments") or tc.get("arguments") or {}
        if isinstance(args_raw, str):
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {"_raw": args_raw}
        elif isinstance(args_raw, dict):
            args = args_raw
        else:
            args = {}
        tc_id = tc.get("id") or f"tc_{i}"
        if name:
            out.append(ToolCall(id=tc_id, name=name, arguments=args))
    return tuple(out)


def call_with_tools(
    tier: Tier,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> ChatResult:
    """tool-calling 지원 호출 (Sprint F agentic loop용).

    Args:
        tier: DEFAULT/HEAVY/LONG 권장. FAST는 제한적.
        messages: OpenAI 메시지 배열 (role: system/user/assistant/tool).
        tools: OpenAI tools schema (list of {type: "function", function: {...}}).
        max_tokens, temperature: 동일.

    Returns:
        ChatResult(content, tool_calls, raw).
    """
    cfg = tier.value
    try:
        content, raw = _call_once(
            tier, messages,
            json_mode=False, max_tokens=max_tokens,
            temperature=temperature, tools=tools,
        )
    except TierUnavailableError:
        raise

    tool_calls = _extract_tool_calls(raw, cfg.runtime)
    return ChatResult(content=content or "", tool_calls=tool_calls, raw=raw)


# ── 헬스체크 ──────────────────────────────────────────────────────


def health_check(tier: Tier, timeout: float = 3.0) -> bool:
    """티어 엔드포인트 도달 가능성만 빠르게 확인."""
    url = _health_url(tier.value)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
            return resp.status_code < 500
    except httpx.HTTPError:
        return False


def health_check_all() -> dict[str, bool]:
    return {t.value.name: health_check(t) for t in Tier}


# ── 환경 오버라이드 ───────────────────────────────────────────────


def _env_override_base_url(tier: Tier) -> str:
    env_key = f"LUXON_LLM_{tier.value.name}_URL"
    return os.environ.get(env_key, tier.value.base_url)
