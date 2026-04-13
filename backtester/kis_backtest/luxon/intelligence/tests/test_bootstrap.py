"""Bootstrap 모듈 단위 테스트 — 실 엔드포인트 불필요."""
from __future__ import annotations

import pytest

from kis_backtest.luxon.intelligence import bootstrap as bm
from kis_backtest.luxon.intelligence.bootstrap import (
    BootstrapReport,
    MCPStatus,
    TierStatus,
    bootstrap,
    probe_mcp,
    warmup_tier,
)
from kis_backtest.luxon.intelligence.router import Tier


class TestWarmupTier:
    def test_unreachable_tier_returns_not_warmed(self, monkeypatch):
        monkeypatch.setattr(bm, "health_check", lambda t, timeout=3.0: False)
        status = warmup_tier(Tier.FAST)
        assert status.reachable is False
        assert status.warmed_up is False
        assert "unreachable" in status.error

    def test_reachable_tier_warms_up(self, monkeypatch):
        monkeypatch.setattr(bm, "health_check", lambda t, timeout=3.0: True)
        monkeypatch.setattr(bm, "call", lambda *a, **k: "pong")
        status = warmup_tier(Tier.DEFAULT)
        assert status.reachable
        assert status.warmed_up
        assert status.warmup_ms >= 0

    def test_warmup_error_captured(self, monkeypatch):
        monkeypatch.setattr(bm, "health_check", lambda t, timeout=3.0: True)

        def fake_call(*a, **k):
            raise RuntimeError("model crashed")

        monkeypatch.setattr(bm, "call", fake_call)
        status = warmup_tier(Tier.HEAVY)
        assert status.reachable
        assert not status.warmed_up
        assert "model crashed" in status.error


class TestProbeMCP:
    def test_successful_probe(self, monkeypatch):
        class StubClient:
            def __init__(self, server, timeout=5.0):
                self.server = server

            def list_tools(self):
                return [None, None, None]

        monkeypatch.setattr(bm, "MCPClient", StubClient)
        from kis_backtest.luxon.intelligence.mcp_registry import MCPServerInfo
        srv = MCPServerInfo(
            name="t", url="http://stub", transport="http",
            default_tier=Tier.DEFAULT,
        )
        status = probe_mcp(srv)
        assert status.reachable
        assert status.tool_count == 3

    def test_unavailable_captured(self, monkeypatch):
        from kis_backtest.luxon.intelligence.mcp_bridge import MCPUnavailableError

        class FailClient:
            def __init__(self, server, timeout=5.0):
                self.server = server

            def list_tools(self):
                raise MCPUnavailableError("down")

        monkeypatch.setattr(bm, "MCPClient", FailClient)
        from kis_backtest.luxon.intelligence.mcp_registry import MCPServerInfo
        srv = MCPServerInfo(
            name="t", url="http://stub", transport="http",
            default_tier=Tier.DEFAULT,
        )
        status = probe_mcp(srv)
        assert not status.reachable
        assert "down" in status.error


class TestBootstrapIntegration:
    def test_report_structure(self, monkeypatch):
        monkeypatch.setattr(bm, "health_check", lambda t, timeout=3.0: True)
        monkeypatch.setattr(bm, "call", lambda *a, **k: "ok")

        class StubClient:
            def __init__(self, server, timeout=5.0):
                self.server = server

            def list_tools(self):
                return []

        monkeypatch.setattr(bm, "MCPClient", StubClient)

        # 스크립트 실행은 mock
        monkeypatch.setattr(bm, "invoke_stack_script", lambda timeout=10.0: True)

        rep = bootstrap(
            auto_start_stack=False,
            warmup_timeout=5.0,
            wait_after_start=0.0,
        )
        assert isinstance(rep, BootstrapReport)
        assert len(rep.tiers) == 4  # FAST/DEFAULT/HEAVY/LONG
        assert rep.any_llm_ready
        assert "Luxon Bootstrap Report" in rep.format_report()

    def test_fully_down_stack(self, monkeypatch):
        monkeypatch.setattr(bm, "health_check", lambda t, timeout=3.0: False)

        class FailClient:
            def __init__(self, server, timeout=5.0):
                self.server = server

            def list_tools(self):
                from kis_backtest.luxon.intelligence.mcp_bridge import MCPUnavailableError
                raise MCPUnavailableError("all down")

        monkeypatch.setattr(bm, "MCPClient", FailClient)
        monkeypatch.setattr(bm, "invoke_stack_script", lambda timeout=10.0: False)

        rep = bootstrap(
            auto_start_stack=False,  # 스크립트 미호출
            warmup_timeout=2.0,
            wait_after_start=0.0,
        )
        assert not rep.any_llm_ready
        assert not rep.any_mcp_ready
