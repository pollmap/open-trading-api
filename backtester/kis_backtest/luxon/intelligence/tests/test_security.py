"""보안 모듈 단위 테스트."""
from __future__ import annotations

import pytest

from kis_backtest.luxon.intelligence.security import (
    SecurityCheckFailed,
    audit_endpoint,
    check_tokens,
    preflight,
    redact,
    sanitize_tool_args,
)


class TestRedact:
    def test_short_string_fully_masked(self):
        assert redact("abc") == "***"

    def test_long_string_keeps_head_and_tail(self):
        out = redact("abcdefghijklmnop", keep=4)
        assert out.startswith("abcd")
        assert out.endswith("mnop")

    def test_empty_returns_placeholder(self):
        assert redact("") == "<empty>"


class TestAuditEndpoint:
    def test_loopback_ok(self):
        a = audit_endpoint("http://127.0.0.1:11434")
        assert a.verdict == "ok"
        assert a.is_loopback

    def test_localhost_ok(self):
        a = audit_endpoint("http://localhost:5001")
        assert a.verdict == "ok"

    def test_external_https_ok(self):
        a = audit_endpoint("https://62.171.141.206:8100")
        assert a.verdict == "ok"
        assert a.is_external_https

    def test_external_http_warn(self):
        a = audit_endpoint("http://62.171.141.206:8100")
        assert a.verdict == "warn"


class TestCheckTokens:
    def test_missing_token_flagged(self, monkeypatch):
        monkeypatch.delenv("MCP_VPS_TOKEN", raising=False)
        status = check_tokens()
        assert status.get("MCP_VPS_TOKEN") == "MISSING"

    def test_present_token_redacted(self, monkeypatch):
        monkeypatch.setenv("MCP_VPS_TOKEN", "abcdefgh12345678abcdefgh")
        status = check_tokens()
        val = status.get("MCP_VPS_TOKEN", "")
        assert val.startswith("present(")
        assert "abcdefgh12345678" not in val  # 전체 노출 금지


class TestSanitizeToolArgs:
    def test_plain_args_pass(self):
        out = sanitize_tool_args({"ticker": "005930", "qty": 100})
        assert out["ticker"] == "005930"

    def test_path_traversal_blocked(self):
        with pytest.raises(SecurityCheckFailed, match="Dangerous"):
            sanitize_tool_args({"path": "../../../etc/passwd"})

    def test_shell_metacharacters_blocked(self):
        with pytest.raises(SecurityCheckFailed):
            sanitize_tool_args({"cmd": "ls; rm -rf /"})

    def test_non_string_values_ignored(self):
        # 숫자/리스트 등은 검사 대상 아님
        out = sanitize_tool_args({"n": 42, "items": [1, 2]})
        assert out["n"] == 42


class TestPreflight:
    def test_returns_report_structure(self):
        rep = preflight()
        assert isinstance(rep.endpoints, list)
        assert isinstance(rep.token_status, dict)
        assert isinstance(rep.warnings, list)

    def test_format_report_string(self):
        rep = preflight()
        s = rep.format_report()
        assert "Security Preflight" in s
        assert "Endpoints" in s
