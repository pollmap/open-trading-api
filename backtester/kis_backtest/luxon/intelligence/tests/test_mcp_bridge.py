"""MCP 브리지 단위 테스트 — httpx + JSON-RPC 모킹."""
from __future__ import annotations

import json

import httpx
import pytest

from kis_backtest.luxon.intelligence import MCPClient, MCPTool
from kis_backtest.luxon.intelligence.mcp_bridge import (
    MCPError,
    MCPUnavailableError,
    ToolCallError,
    call_qualified_tool,
    collect_tools,
    tool_result_message,
    tools_to_openai_format,
)
from kis_backtest.luxon.intelligence.mcp_registry import (
    MCPServerInfo,
    get_server,
)
from kis_backtest.luxon.intelligence.router import Tier


# ── 모킹 유틸 ────────────────────────────────────────────────────


def _rpc_result(result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": "x", "result": result}


def _rpc_error(code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": "x", "error": {"code": code, "message": message}}


class FakeResp:
    def __init__(self, status: int = 200, data: dict | None = None, headers: dict | None = None):
        self.status_code = status
        self._data = data or {}
        self.headers = headers or {}
        self.text = json.dumps(data or {})

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "http://test"),
                response=httpx.Response(self.status_code, text=self.text),
            )


def _test_server(name: str = "test-srv") -> MCPServerInfo:
    return MCPServerInfo(
        name=name,
        url="http://127.0.0.1:9999",
        transport="http",
        default_tier=Tier.DEFAULT,
    )


# ── MCPClient 초기화 + list_tools ────────────────────────────────


class TestMCPClientInitAndList:
    def test_initialize_sends_jsonrpc(self, monkeypatch):
        captured = []

        def fake_post(self, url, json=None, headers=None, **kw):
            captured.append(json)
            return FakeResp(200, _rpc_result({"capabilities": {}}))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        client = MCPClient(_test_server())
        client.initialize()
        assert captured[0]["method"] == "initialize"
        assert captured[0]["jsonrpc"] == "2.0"

    def test_list_tools_parses_schema(self, monkeypatch):
        responses = iter([
            _rpc_result({"capabilities": {}}),  # initialize
            _rpc_result({
                "tools": [
                    {
                        "name": "get_price",
                        "description": "현재가 조회",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"ticker": {"type": "string"}},
                        },
                    },
                    {
                        "name": "get_news",
                        "description": "뉴스 검색",
                        "inputSchema": {"type": "object"},
                    },
                ]
            }),
        ])

        def fake_post(self, url, json=None, headers=None, **kw):
            return FakeResp(200, next(responses))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        client = MCPClient(_test_server())
        tools = client.list_tools()
        assert len(tools) == 2
        assert tools[0].name == "get_price"
        assert tools[0].qualified_name == "test-srv__get_price"
        assert "ticker" in tools[0].input_schema.get("properties", {})

    def test_connect_error_raises_unavailable(self, monkeypatch):
        def fake_post(self, url, json=None, headers=None, **kw):
            raise httpx.ConnectError("refused", request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        client = MCPClient(_test_server())
        with pytest.raises(MCPUnavailableError, match="unreachable"):
            client.initialize()

    def test_rpc_error_raises_mcp_error(self, monkeypatch):
        def fake_post(self, url, json=None, headers=None, **kw):
            return FakeResp(200, _rpc_error(-32601, "Method not found"))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        client = MCPClient(_test_server())
        with pytest.raises(MCPError, match="Method not found"):
            client.initialize()

    def test_session_id_header_captured_and_reused(self, monkeypatch):
        calls = []

        def fake_post(self, url, json=None, headers=None, **kw):
            calls.append(dict(headers or {}))
            return FakeResp(
                200, _rpc_result({"capabilities": {}}),
                headers={"Mcp-Session-Id": "sess-123"},
            )

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        client = MCPClient(_test_server())
        client.initialize()
        # 다음 호출 시 세션 ID 전송
        client._rpc("ping", {})
        assert calls[1].get("Mcp-Session-Id") == "sess-123"


# ── call_tool ────────────────────────────────────────────────────


class TestCallTool:
    def test_text_content_json_parsed(self, monkeypatch):
        responses = iter([
            _rpc_result({"capabilities": {}}),
            _rpc_result({
                "content": [
                    {"type": "text", "text": '{"price": 75000, "ticker": "005930"}'}
                ]
            }),
        ])

        def fake_post(self, url, json=None, headers=None, **kw):
            return FakeResp(200, next(responses))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        client = MCPClient(_test_server())
        result = client.call_tool("get_price", {"ticker": "005930"})
        assert result == {"price": 75000, "ticker": "005930"}

    def test_non_json_text_returned_as_string(self, monkeypatch):
        responses = iter([
            _rpc_result({"capabilities": {}}),
            _rpc_result({"content": [{"type": "text", "text": "단순 응답"}]}),
        ])

        def fake_post(self, url, json=None, headers=None, **kw):
            return FakeResp(200, next(responses))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        client = MCPClient(_test_server())
        result = client.call_tool("any", {})
        assert result == "단순 응답"

    def test_is_error_raises_tool_call_error(self, monkeypatch):
        responses = iter([
            _rpc_result({"capabilities": {}}),
            _rpc_result({"isError": True, "content": [{"type": "text", "text": "fail"}]}),
        ])

        def fake_post(self, url, json=None, headers=None, **kw):
            return FakeResp(200, next(responses))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        client = MCPClient(_test_server())
        with pytest.raises(ToolCallError, match="returned error"):
            client.call_tool("bad", {})


# ── OpenAI 포맷 변환 ─────────────────────────────────────────────


class TestOpenAITransform:
    def test_to_openai_tool_format(self):
        t = MCPTool(
            server="kis-backtest",
            name="run_backtest",
            description="백테스트 실행",
            input_schema={"type": "object", "properties": {"strategy": {"type": "string"}}},
        )
        out = t.to_openai_tool()
        assert out["type"] == "function"
        assert out["function"]["name"] == "kis-backtest__run_backtest"
        assert out["function"]["description"] == "백테스트 실행"
        assert out["function"]["parameters"]["properties"]["strategy"]["type"] == "string"

    def test_empty_schema_defaults_to_empty_object(self):
        t = MCPTool(server="s", name="n", description="", input_schema={})
        out = t.to_openai_tool()
        assert out["function"]["parameters"] == {"type": "object", "properties": {}}


# ── 멀티 서버 집계 ────────────────────────────────────────────────


class TestCollectTools:
    def test_skip_unavailable_servers(self, monkeypatch):
        from kis_backtest.luxon.intelligence import mcp_bridge as mb

        def fake_init(self):
            raise MCPUnavailableError(f"{self.server.name} down")

        monkeypatch.setattr(mb.MCPClient, "initialize", fake_init)

        def fake_list(self):
            # initialize가 실패해도 list_tools 호출되기 전에 raise
            self.initialize()
            return []

        monkeypatch.setattr(mb.MCPClient, "list_tools", fake_list)
        out = collect_tools()
        assert out == []  # 모든 서버 다운 시 빈 리스트

    def test_tools_to_openai_format(self):
        tools = [
            MCPTool(server="a", name="t1", description="d1", input_schema={}),
            MCPTool(server="b", name="t2", description="d2", input_schema={}),
        ]
        out = tools_to_openai_format(tools)
        assert len(out) == 2
        assert out[0]["function"]["name"] == "a__t1"
        assert out[1]["function"]["name"] == "b__t2"


# ── call_qualified_tool ──────────────────────────────────────────


class TestCallQualifiedTool:
    def test_splits_server_and_tool_name(self, monkeypatch):
        from kis_backtest.luxon.intelligence import mcp_bridge as mb

        captured = {}

        class StubClient:
            def __init__(self, server):
                self.server = server

            def call_tool(self, name, args):
                captured["server"] = self.server.name
                captured["name"] = name
                captured["args"] = args
                return {"ok": True}

        monkeypatch.setattr(mb, "MCPClient", StubClient)
        out = call_qualified_tool("kis-backtest__run", {"x": 1})
        assert captured["server"] == "kis-backtest"
        assert captured["name"] == "run"
        assert captured["args"] == {"x": 1}
        assert out == {"ok": True}

    def test_rejects_missing_separator(self):
        with pytest.raises(ValueError, match="must contain"):
            call_qualified_tool("no_separator_here", {})

    def test_reuses_client_from_dict(self, monkeypatch):
        class StubClient:
            calls = 0

            def call_tool(self, name, args):
                StubClient.calls += 1
                return "x"

        clients = {"s": StubClient()}
        call_qualified_tool("s__n", {}, clients=clients)
        call_qualified_tool("s__n", {}, clients=clients)
        assert StubClient.calls == 2


# ── tool_result_message ──────────────────────────────────────────


class TestToolResultMessage:
    def test_dict_result_serialized_json(self):
        msg = tool_result_message("tc_1", "get_price", {"price": 75000})
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "tc_1"
        assert msg["name"] == "get_price"
        assert '"price": 75000' in msg["content"]

    def test_string_result_passed_through(self):
        msg = tool_result_message("tc_2", "n", "hello")
        assert msg["content"] == "hello"

    def test_truncates_extreme_length(self):
        huge = {"x": "a" * 20000}
        msg = tool_result_message("t", "n", huge)
        assert len(msg["content"]) == 8000
