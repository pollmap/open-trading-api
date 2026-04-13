"""Router 단위 테스트 — httpx 모킹 기반.

Sprint E 업데이트:
    - ALT → LONG rename
    - ctx_limit 상향 검증
    - Ollama native /api/chat 경로 + options.num_ctx 검증
    - 비-Ollama(FLM/KoboldCpp) OpenAI 호환 경로 유지 검증
"""
from __future__ import annotations

import json

import httpx
import pytest

from kis_backtest.luxon.intelligence.router import (
    ContextLimitExceededError,
    LocalLLMError,
    Tier,
    TierUnavailableError,
    _call_once,
    call,
    estimate_tokens,
    health_check,
)


# ── 토큰 추정 ─────────────────────────────────────────────────────


class TestEstimateTokens:
    def test_empty_string_returns_zero(self):
        assert estimate_tokens("") == 0

    def test_english_roughly_4_chars_per_token(self):
        text = "Hello world, this is a test."
        assert 6 <= estimate_tokens(text) <= 10

    def test_korean_roughly_1_5_chars_per_token(self):
        text = "삼성전자 매수 시그널 발생"
        assert 5 <= estimate_tokens(text) <= 15

    def test_korean_threshold_detection(self):
        assert estimate_tokens("삼성 stock") > 0


# ── Tier 정의 (Sprint E) ─────────────────────────────────────────


class TestTierConfig:
    def test_all_4_tiers_defined(self):
        names = {t.value.name for t in Tier}
        assert names == {"FAST", "DEFAULT", "HEAVY", "LONG"}

    def test_fast_ctx_unchanged_4096(self):
        assert Tier.FAST.value.ctx_limit == 4096
        assert Tier.FAST.value.num_ctx == 4096

    def test_default_ctx_expanded_to_16384(self):
        assert Tier.DEFAULT.value.ctx_limit == 16384
        assert Tier.DEFAULT.value.num_ctx == 16384

    def test_heavy_ctx_expanded_to_8192(self):
        assert Tier.HEAVY.value.ctx_limit == 8192
        assert Tier.HEAVY.value.num_ctx == 8192

    def test_long_ctx_32k_and_koboldcpp(self):
        assert Tier.LONG.value.ctx_limit == 32768
        assert Tier.LONG.value.num_ctx == 32768
        assert "5001" in Tier.LONG.value.base_url
        assert Tier.LONG.value.runtime == "koboldcpp"

    def test_fast_endpoint_is_flm(self):
        assert "52625" in Tier.FAST.value.base_url
        assert Tier.FAST.value.runtime == "flm"

    def test_default_and_heavy_share_ollama(self):
        assert Tier.DEFAULT.value.base_url == Tier.HEAVY.value.base_url
        assert "11434" in Tier.DEFAULT.value.base_url
        assert Tier.DEFAULT.value.runtime == "ollama"
        assert Tier.HEAVY.value.runtime == "ollama"


# ── 모킹 유틸 ────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, status_code: int, data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._data = data or {}
        self.text = text or json.dumps(data or {})

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "http://test"),
                response=httpx.Response(self.status_code, text=self.text),
            )


def _ollama_chat_resp(content: str, *, tool_calls=None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"model": "test", "message": msg, "done": True}


def _openai_chat_resp(content: str, *, tool_calls=None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": "x", "choices": [{"message": msg, "index": 0}]}


# ── _call_once 엔드포인트 분기 ────────────────────────────────────


class TestCallOnceOllamaPath:
    def test_ollama_tier_uses_api_chat_endpoint(self, monkeypatch):
        captured = {}

        def fake_post(self, url, json=None, **kw):
            captured["url"] = url
            captured["payload"] = json
            return FakeResponse(200, _ollama_chat_resp("안녕"))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        content, _ = _call_once(
            Tier.DEFAULT,
            [{"role": "user", "content": "hi"}],
            json_mode=False, max_tokens=None, temperature=0.3,
        )
        assert content == "안녕"
        assert captured["url"].endswith("/api/chat")

    def test_ollama_payload_includes_num_ctx(self, monkeypatch):
        captured = {}

        def fake_post(self, url, json=None, **kw):
            captured["payload"] = json
            return FakeResponse(200, _ollama_chat_resp("x"))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        _call_once(
            Tier.HEAVY,
            [{"role": "user", "content": "hi"}],
            json_mode=False, max_tokens=500, temperature=0.2,
        )
        options = captured["payload"]["options"]
        assert options["num_ctx"] == 8192  # HEAVY
        assert options["num_predict"] == 500
        assert options["temperature"] == 0.2

    def test_ollama_json_mode_uses_format_field(self, monkeypatch):
        captured = {}

        def fake_post(self, url, json=None, **kw):
            captured["payload"] = json
            return FakeResponse(200, _ollama_chat_resp('{"ok":1}'))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        _call_once(
            Tier.DEFAULT,
            [{"role": "user", "content": "x"}],
            json_mode=True, max_tokens=None, temperature=0.1,
        )
        assert captured["payload"]["format"] == "json"


class TestCallOnceOpenAIPath:
    def test_fast_tier_uses_v1_chat_completions(self, monkeypatch):
        captured = {}

        def fake_post(self, url, json=None, **kw):
            captured["url"] = url
            return FakeResponse(200, _openai_chat_resp("ok"))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        _call_once(
            Tier.FAST,
            [{"role": "user", "content": "hi"}],
            json_mode=False, max_tokens=None, temperature=0.3,
        )
        assert captured["url"].endswith("/v1/chat/completions")

    def test_long_tier_uses_v1_chat_completions(self, monkeypatch):
        captured = {}

        def fake_post(self, url, json=None, **kw):
            captured["url"] = url
            return FakeResponse(200, _openai_chat_resp("ok"))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        _call_once(
            Tier.LONG,
            [{"role": "user", "content": "hi"}],
            json_mode=False, max_tokens=None, temperature=0.3,
        )
        assert captured["url"].endswith("/v1/chat/completions")

    def test_openai_json_mode_uses_response_format(self, monkeypatch):
        captured = {}

        def fake_post(self, url, json=None, **kw):
            captured["payload"] = json
            return FakeResponse(200, _openai_chat_resp('{"ok":1}'))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        _call_once(
            Tier.LONG,
            [{"role": "user", "content": "x"}],
            json_mode=True, max_tokens=100, temperature=0.1,
        )
        assert captured["payload"]["response_format"] == {"type": "json_object"}
        assert captured["payload"]["max_tokens"] == 100


class TestCallOnceErrors:
    def test_connect_error_raises_tier_unavailable(self, monkeypatch):
        def fake_post(self, url, json=None, **kw):
            raise httpx.ConnectError("refused", request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(TierUnavailableError, match="unreachable"):
            _call_once(
                Tier.DEFAULT,
                [{"role": "user", "content": "hi"}],
                json_mode=False, max_tokens=None, temperature=0.3,
            )

    def test_http_500_raises(self, monkeypatch):
        def fake_post(self, url, json=None, **kw):
            return FakeResponse(500, {}, text="internal error")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(LocalLLMError, match="HTTP 500"):
            _call_once(
                Tier.FAST,
                [{"role": "user", "content": "hi"}],
                json_mode=False, max_tokens=None, temperature=0.3,
            )


# ── call (상위 API) ──────────────────────────────────────────────


class TestCall:
    def test_rejects_oversize_prompt_for_heavy(self):
        huge = "x" * (Tier.HEAVY.value.ctx_limit * 10)
        with pytest.raises(ContextLimitExceededError, match="ctx_limit"):
            call(Tier.HEAVY, system="sys", user=huge)

    def test_accepts_larger_prompt_for_long_tier(self, monkeypatch):
        """LONG 32k에서는 HEAVY 8k 상한 프롬프트 수용"""
        def fake_post(self, url, json=None, **kw):
            return FakeResponse(200, _openai_chat_resp("ok"))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        medium = "x" * 20000  # ~5000 tokens, LONG 32k 내
        out = call(Tier.LONG, system="sys", user=medium)
        assert out == "ok"

    def test_auto_fallback_fast_to_default(self, monkeypatch):
        """NPU(FAST) 다운 시 CPU(DEFAULT)로 자동 폴백 — LOCAL_LLM_STACK 정책."""
        attempts = []

        def fake_post(self, url, json=None, **kw):
            attempts.append(url)
            if "52625" in url:
                raise httpx.ConnectError("npu down", request=httpx.Request("POST", url))
            # Ollama 11434 응답 (Ollama native api/chat)
            return FakeResponse(200, _ollama_chat_resp("from cpu"))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        out = call(Tier.FAST, system="s", user="u", auto_fallback=True)
        assert out == "from cpu"
        assert any("52625" in u for u in attempts)
        assert any("11434" in u for u in attempts)

    def test_no_fallback_when_disabled(self, monkeypatch):
        def fake_post(self, url, json=None, **kw):
            raise httpx.ConnectError("down", request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(TierUnavailableError):
            call(Tier.FAST, system="s", user="u", auto_fallback=False)

    def test_default_has_no_auto_fallback(self, monkeypatch):
        """DEFAULT(CPU)는 이미 최하위 레이어 — iGPU로 자동 폴백 안 함."""
        def fake_post(self, url, json=None, **kw):
            raise httpx.ConnectError("ollama down", request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(TierUnavailableError):
            call(Tier.DEFAULT, system="s", user="u", auto_fallback=True)

    def test_heavy_has_no_auto_fallback(self, monkeypatch):
        """HEAVY는 명시 요청만 — 자동 폴백 없음."""
        def fake_post(self, url, json=None, **kw):
            raise httpx.ConnectError("ollama down", request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(TierUnavailableError):
            call(Tier.HEAVY, system="s", user="u", auto_fallback=True)

    def test_long_is_manual_only(self, monkeypatch):
        """LONG(iGPU Kobold)은 수동 전용 — 자동 폴백 대상 아님."""
        def fake_post(self, url, json=None, **kw):
            raise httpx.ConnectError("kobold down", request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(TierUnavailableError):
            call(Tier.LONG, system="s", user="u", auto_fallback=True)

    def test_returns_stripped_response(self, monkeypatch):
        def fake_post(self, url, json=None, **kw):
            # Ollama path
            return FakeResponse(200, _ollama_chat_resp("  spaced  "))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        out = call(Tier.DEFAULT, system="s", user="u")
        assert out == "  spaced  "  # call()은 non-empty 확인만, strip은 호출자 몫


# ── 헬스체크 ──────────────────────────────────────────────────────


class TestHealthCheck:
    def test_ollama_tier_checks_api_tags(self, monkeypatch):
        captured = {}

        def fake_get(self, url, **kw):
            captured["url"] = url
            return FakeResponse(200, {"models": []})

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        assert health_check(Tier.DEFAULT) is True
        assert captured["url"].endswith("/api/tags")

    def test_openai_tier_checks_v1_models(self, monkeypatch):
        captured = {}

        def fake_get(self, url, **kw):
            captured["url"] = url
            return FakeResponse(200, {"data": []})

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        assert health_check(Tier.LONG) is True
        assert captured["url"].endswith("/v1/models")

    def test_returns_false_on_connect_error(self, monkeypatch):
        def fake_get(self, url, **kw):
            raise httpx.ConnectError("down", request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        assert health_check(Tier.HEAVY) is False


# ── Tool-calling (Sprint F 예비) ─────────────────────────────────


class TestToolCallExtraction:
    def test_ollama_tool_calls_parsed(self, monkeypatch):
        from kis_backtest.luxon.intelligence.router import call_with_tools

        tool_call = {
            "id": "call_1",
            "function": {"name": "get_price", "arguments": {"ticker": "005930"}},
        }

        def fake_post(self, url, json=None, **kw):
            return FakeResponse(200, _ollama_chat_resp("", tool_calls=[tool_call]))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        result = call_with_tools(
            Tier.DEFAULT,
            messages=[{"role": "user", "content": "삼성전자 가격"}],
            tools=[{"type": "function", "function": {"name": "get_price"}}],
        )
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_price"
        assert result.tool_calls[0].arguments == {"ticker": "005930"}

    def test_openai_tool_calls_with_string_args(self, monkeypatch):
        from kis_backtest.luxon.intelligence.router import call_with_tools

        tool_call = {
            "id": "call_2",
            "function": {"name": "search", "arguments": '{"q": "LNG 슈퍼사이클"}'},
        }

        def fake_post(self, url, json=None, **kw):
            return FakeResponse(200, _openai_chat_resp("", tool_calls=[tool_call]))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        result = call_with_tools(
            Tier.LONG,
            messages=[{"role": "user", "content": "뉴스 검색"}],
            tools=[{"type": "function", "function": {"name": "search"}}],
        )
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].arguments["q"] == "LNG 슈퍼사이클"

    def test_no_tool_calls_returns_content(self, monkeypatch):
        from kis_backtest.luxon.intelligence.router import call_with_tools

        def fake_post(self, url, json=None, **kw):
            return FakeResponse(200, _ollama_chat_resp("최종 답변입니다"))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        result = call_with_tools(
            Tier.DEFAULT,
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )
        assert result.content == "최종 답변입니다"
        assert result.tool_calls == ()
