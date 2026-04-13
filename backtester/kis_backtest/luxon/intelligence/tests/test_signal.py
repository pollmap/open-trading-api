"""시그널 태스크 단위 테스트 — 라우터 모킹 기반."""
from __future__ import annotations

import httpx
import pytest

from kis_backtest.luxon.intelligence import Tier
from kis_backtest.luxon.intelligence.tasks import signal


def _fake_ok(content: str, url: str = ""):
    is_ollama = "api/chat" in url
    class _R:
        status_code = 200

        def json(self):
            if is_ollama:
                return {"message": {"role": "assistant", "content": content}}
            return {"choices": [{"message": {"content": content}}]}

        def raise_for_status(self):
            pass

    return _R()


class TestSignalCommentary:
    def test_commentary_returns_stripped_string(self, monkeypatch):
        def fake_post(self, url, json=None, **kwargs):
            return _fake_ok("  매수 검토. 삼성전자 RSI 28.  ", url)

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        out = signal.commentary({"ticker": "005930", "rsi": 28})
        assert out == "매수 검토. 삼성전자 RSI 28."

    def test_passes_signal_json_to_prompt(self, monkeypatch):
        captured = {}

        def fake_post(self, url, json=None, **kwargs):
            captured["payload"] = json
            return _fake_ok("ok", url)

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        signal.commentary({"ticker": "BTCUSDT", "volatility_sigma": 2.3})
        user_msg = captured["payload"]["messages"][1]["content"]
        assert "BTCUSDT" in user_msg
        assert "2.3" in user_msg

    def test_uses_fast_tier_by_default(self, monkeypatch):
        captured = {}

        def fake_post(self, url, json=None, **kwargs):
            captured["url"] = url
            return _fake_ok("x", url)

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        signal.commentary({"x": 1})
        assert "52625" in captured["url"]  # FAST = FLM NPU

    def test_can_override_tier_to_default(self, monkeypatch):
        captured = {}

        def fake_post(self, url, json=None, **kwargs):
            captured["url"] = url
            return _fake_ok("x", url)

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        signal.commentary({"x": 1}, tier=Tier.DEFAULT)
        assert "11434" in captured["url"]
