"""Catalyst 추출 단위 테스트."""
from __future__ import annotations

import httpx

from kis_backtest.luxon.intelligence.tasks import catalyst


def _mock_ok(content: str, url: str = ""):
    """URL에 따라 Ollama native 또는 OpenAI 호환 응답 반환."""
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


class TestCatalystExtraction:
    def test_parses_clean_json_array(self, monkeypatch):
        payload = (
            '[{"date": "Q2 2026", "event": "신공장 가동", "upside_delta_pct": 8},'
            ' {"date": "H2 2026", "event": "유럽 인증", "upside_delta_pct": 5}]'
        )
        monkeypatch.setattr(
            httpx.Client, "post", lambda self, url, json=None, **k: _mock_ok(payload, url)
        )
        result = catalyst.extract("뉴스 본문...")
        assert result.parse_error is None
        assert len(result.events) == 2
        assert result.events[0].date == "Q2 2026"
        assert result.events[0].event == "신공장 가동"
        assert result.events[0].upside_delta_pct == 8.0

    def test_parses_markdown_wrapped_json(self, monkeypatch):
        payload = (
            "```json\n"
            '[{"date": "Q1 2027", "event": "증설 완료", "upside_delta_pct": 12}]\n'
            "```"
        )
        monkeypatch.setattr(
            httpx.Client, "post", lambda self, url, json=None, **k: _mock_ok(payload, url)
        )
        result = catalyst.extract("...")
        assert len(result.events) == 1
        assert result.events[0].date == "Q1 2027"

    def test_drops_invalid_date_format(self, monkeypatch):
        # YYYY-MM-DD는 strict_date 모드에서 drop
        payload = (
            '[{"date": "2026-05-15", "event": "실적 발표", "upside_delta_pct": 5},'
            ' {"date": "Q3 2026", "event": "신공장", "upside_delta_pct": 8}]'
        )
        monkeypatch.setattr(
            httpx.Client, "post", lambda self, url, json=None, **k: _mock_ok(payload, url)
        )
        result = catalyst.extract("...", strict_date=True)
        assert len(result.events) == 1
        assert result.events[0].date == "Q3 2026"

    def test_malformed_json_returns_parse_error(self, monkeypatch):
        monkeypatch.setattr(
            httpx.Client, "post", lambda self, url, json=None, **k: _mock_ok("not json", url)
        )
        result = catalyst.extract("...")
        assert result.parse_error is not None
        assert result.events == []

    def test_caps_at_5_events(self, monkeypatch):
        big = [
            {"date": "Q1 2026", "event": f"e{i}", "upside_delta_pct": i}
            for i in range(10)
        ]
        import json as _json
        payload = _json.dumps(big, ensure_ascii=False)
        monkeypatch.setattr(
            httpx.Client, "post", lambda self, url, json=None, **k: _mock_ok(payload, url)
        )
        result = catalyst.extract("...")
        assert len(result.events) == 5
