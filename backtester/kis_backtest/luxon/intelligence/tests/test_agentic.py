"""Agentic loop 단위 테스트 — MCP + LLM 모킹."""
from __future__ import annotations

import httpx
import pytest

from kis_backtest.luxon.intelligence import (
    AgenticLoopExhausted,
    MCPTool,
    Tier,
    agentic_run,
)
from kis_backtest.luxon.intelligence import agentic as agentic_mod
from kis_backtest.luxon.intelligence import mcp_bridge as mb


# ── Stub helpers ─────────────────────────────────────────────────


def _stub_collect_tools(monkeypatch, tools: list[MCPTool]):
    monkeypatch.setattr(agentic_mod, "collect_tools", lambda names=None: tools)
    monkeypatch.setattr(mb, "collect_tools", lambda names=None: tools)


def _stub_mcp_client(monkeypatch, call_results: dict[tuple[str, str], object]):
    """(server, tool_name) → result 매핑.

    Stubs MCPClient class AND get_server (레지스트리 우회).
    """
    from kis_backtest.luxon.intelligence.mcp_registry import MCPServerInfo

    class StubClient:
        def __init__(self, server):
            self.server = server

        def call_tool(self, name, args):
            key = (self.server.name, name)
            if key in call_results:
                return call_results[key]
            return {"unhandled": True}

    def fake_get_server(name):
        return MCPServerInfo(
            name=name, url="http://stub", transport="http",
            default_tier=Tier.DEFAULT,
        )

    monkeypatch.setattr(agentic_mod, "MCPClient", StubClient)
    monkeypatch.setattr(agentic_mod, "get_server", fake_get_server)
    monkeypatch.setattr(mb, "MCPClient", StubClient)


def _ollama_resp(content: str, tool_calls: list[dict] | None = None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls

    class _R:
        status_code = 200

        def json(self):
            return {"message": msg, "done": True}

        def raise_for_status(self):
            pass

    return _R()


def _install_llm_mock(monkeypatch, responses: list):
    """responses: list of _R objects in order."""
    it = iter(responses)

    def fake_post(self, url, json=None, **kw):
        try:
            return next(it)
        except StopIteration:
            return _ollama_resp("done", [])

    monkeypatch.setattr(httpx.Client, "post", fake_post)


def _tc(name: str, args: dict, tc_id: str = "call_1") -> dict:
    return {
        "id": tc_id,
        "function": {"name": name, "arguments": args},
    }


# ── 단일 스텝 ────────────────────────────────────────────────────


class TestSingleStep:
    def test_no_tool_calls_returns_immediately(self, monkeypatch):
        _stub_collect_tools(monkeypatch, [])
        _install_llm_mock(monkeypatch, [_ollama_resp("간단한 답변입니다")])

        result = agentic_run("안녕", mcp_servers=[], max_steps=3)
        assert result.final_content == "간단한 답변입니다"
        assert len(result.steps) == 1
        assert result.total_tool_calls == 0

    def test_single_tool_call_and_final_answer(self, monkeypatch):
        tools = [
            MCPTool(server="kis", name="get_price", description="가격", input_schema={}),
        ]
        _stub_collect_tools(monkeypatch, tools)
        _stub_mcp_client(monkeypatch, {("kis", "get_price"): {"price": 75000}})
        _install_llm_mock(monkeypatch, [
            _ollama_resp("", [_tc("kis__get_price", {"ticker": "005930"})]),
            _ollama_resp("현재가 75,000원입니다."),
        ])

        result = agentic_run(
            "삼성전자 현재가", mcp_servers=["kis"], max_steps=3
        )
        assert result.final_content == "현재가 75,000원입니다."
        assert result.total_tool_calls == 1
        assert result.steps[0].tool_results == [{"price": 75000}]


# ── 다단계 체인 ──────────────────────────────────────────────────


class TestMultiStep:
    def test_three_step_chain(self, monkeypatch):
        tools = [
            MCPTool(server="kis", name="get_price", description="", input_schema={}),
            MCPTool(server="nexus", name="fetch_fs", description="", input_schema={}),
            MCPTool(server="nexus", name="calc_per", description="", input_schema={}),
        ]
        _stub_collect_tools(monkeypatch, tools)
        _stub_mcp_client(monkeypatch, {
            ("kis", "get_price"): {"price": 75000},
            ("nexus", "fetch_fs"): {"eps": 5000},
            ("nexus", "calc_per"): {"per": 15.0},
        })
        _install_llm_mock(monkeypatch, [
            _ollama_resp("", [_tc("kis__get_price", {"ticker": "005930"}, "c1")]),
            _ollama_resp("", [_tc("nexus__fetch_fs", {"ticker": "005930"}, "c2")]),
            _ollama_resp("", [_tc("nexus__calc_per", {"price": 75000, "eps": 5000}, "c3")]),
            _ollama_resp("PER은 15배로 과거 평균 대비 적정."),
        ])

        result = agentic_run(
            "삼성전자 PER 분석",
            mcp_servers=["kis", "nexus"],
            max_steps=5,
        )
        assert result.total_tool_calls == 3
        assert len(result.steps) == 4
        assert "PER" in result.final_content

    def test_parallel_tool_calls_in_single_step(self, monkeypatch):
        tools = [
            MCPTool(server="kis", name="t1", description="", input_schema={}),
            MCPTool(server="kis", name="t2", description="", input_schema={}),
        ]
        _stub_collect_tools(monkeypatch, tools)
        _stub_mcp_client(monkeypatch, {
            ("kis", "t1"): {"a": 1},
            ("kis", "t2"): {"b": 2},
        })
        _install_llm_mock(monkeypatch, [
            _ollama_resp("", [
                _tc("kis__t1", {}, "c1"),
                _tc("kis__t2", {}, "c2"),
            ]),
            _ollama_resp("두 결과 결합."),
        ])

        result = agentic_run(
            "두 정보 동시", mcp_servers=["kis"], max_steps=3
        )
        assert result.total_tool_calls == 2
        assert len(result.steps[0].tool_results) == 2


# ── 오류 경로 ────────────────────────────────────────────────────


class TestErrorPaths:
    def test_max_steps_exhausted_raises(self, monkeypatch):
        tools = [MCPTool(server="s", name="t", description="", input_schema={})]
        _stub_collect_tools(monkeypatch, tools)
        _stub_mcp_client(monkeypatch, {("s", "t"): {"loop": True}})
        # 무한 tool call 응답
        _install_llm_mock(monkeypatch, [
            _ollama_resp("", [_tc("s__t", {}, f"c{i}")]) for i in range(10)
        ])

        with pytest.raises(AgenticLoopExhausted, match="max_steps"):
            agentic_run("loop", mcp_servers=["s"], max_steps=2)

    def test_tool_error_captured_in_messages(self, monkeypatch):
        tools = [MCPTool(server="s", name="bad", description="", input_schema={})]
        _stub_collect_tools(monkeypatch, tools)

        class FailingClient:
            def __init__(self, server):
                self.server = server

            def call_tool(self, name, args):
                raise RuntimeError("tool crashed")

        from kis_backtest.luxon.intelligence.mcp_registry import MCPServerInfo

        def fake_get_server(name):
            return MCPServerInfo(
                name=name, url="http://stub", transport="http",
                default_tier=Tier.DEFAULT,
            )

        monkeypatch.setattr(agentic_mod, "MCPClient", FailingClient)
        monkeypatch.setattr(agentic_mod, "get_server", fake_get_server)
        monkeypatch.setattr(mb, "MCPClient", FailingClient)

        _install_llm_mock(monkeypatch, [
            _ollama_resp("", [_tc("s__bad", {}, "c1")]),
            _ollama_resp("도구 실패 — 재시도 필요."),
        ])

        result = agentic_run("try", mcp_servers=["s"], max_steps=3)
        # 에러가 tool_results에 기록되고 루프는 계속 진행
        assert "error" in result.steps[0].tool_results[0]
        assert "tool crashed" in result.steps[0].tool_results[0]["error"]
        assert "도구 실패" in result.final_content
