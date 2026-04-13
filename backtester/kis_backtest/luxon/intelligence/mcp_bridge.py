"""
MCP 브리지 — MCP 서버 tool 스키마를 OpenAI function-calling 포맷으로 변환
+ 동기 HTTP JSON-RPC 클라이언트.

지원 전송: HTTP (Streamable HTTP). stdio는 Sprint G 예정.

MCP JSON-RPC 2.0 메서드:
    - initialize
    - tools/list     → {tools: [{name, description, inputSchema}]}
    - tools/call     → {content: [{type, text}]} 또는 에러
    - ping

OpenAI tools 포맷:
    {"type": "function", "function": {"name": ..., "description": ..., "parameters": {JSON schema}}}

각 tool 이름은 "{server}__{tool}" 형식 — 다중 서버 collision 회피.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from kis_backtest.luxon.intelligence.mcp_registry import (
    MCPServerInfo,
    get_auth_header,
    get_server,
    list_known_servers,
)

SEPARATOR = "__"  # server-name 구분자


# ── 예외 ──────────────────────────────────────────────────────────


class MCPError(RuntimeError):
    pass


class MCPUnavailableError(MCPError):
    pass


class ToolCallError(MCPError):
    pass


# ── 클라이언트 ────────────────────────────────────────────────────


@dataclass(frozen=True)
class MCPTool:
    server: str
    name: str  # 순수 tool 이름 (서버 prefix 제외)
    description: str
    input_schema: dict[str, Any]

    @property
    def qualified_name(self) -> str:
        return f"{self.server}{SEPARATOR}{self.name}"

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.qualified_name,
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }


class MCPClient:
    """동기 HTTP MCP 클라이언트 (Streamable HTTP JSON-RPC 2.0)."""

    def __init__(self, server: MCPServerInfo, *, timeout: float = 30.0):
        self.server = server
        self.timeout = timeout
        self._session_id: str | None = None
        self._initialized = False

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        headers.update(get_auth_header(self.server))
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        req = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
        }
        if params is not None:
            req["params"] = params
        try:
            with httpx.Client(timeout=self.timeout, verify=False) as client:
                resp = client.post(self.server.url, json=req, headers=self._headers())
                if sid := resp.headers.get("Mcp-Session-Id"):
                    self._session_id = sid
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            raise MCPUnavailableError(
                f"MCP {self.server.name} unreachable at {self.server.url}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise MCPUnavailableError(
                f"MCP {self.server.name} timeout at {self.server.url}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise MCPError(
                f"MCP {self.server.name} HTTP {exc.response.status_code}: "
                f"{exc.response.text[:200]}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise MCPError(f"MCP {self.server.name} invalid JSON: {exc}") from exc

        if "error" in data:
            err = data["error"]
            raise MCPError(
                f"MCP {self.server.name} RPC error: "
                f"{err.get('code')} {err.get('message', '')}"
            )
        return data.get("result", {})

    def initialize(self) -> None:
        if self._initialized:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "luxon-intelligence", "version": "0.1.0"},
            },
        )
        self._initialized = True

    def list_tools(self) -> list[MCPTool]:
        if not self._initialized:
            self.initialize()
        result = self._rpc("tools/list", {})
        raw_tools = result.get("tools") or []
        out: list[MCPTool] = []
        for t in raw_tools:
            if not isinstance(t, dict):
                continue
            name = t.get("name")
            if not name:
                continue
            out.append(
                MCPTool(
                    server=self.server.name,
                    name=name,
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema") or {},
                )
            )
        return out

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """순수 tool 이름 (prefix 없음) + args → 결과."""
        if not self._initialized:
            self.initialize()
        try:
            result = self._rpc(
                "tools/call",
                {"name": name, "arguments": arguments or {}},
            )
        except MCPError as exc:
            raise ToolCallError(
                f"{self.server.name}.{name} failed: {exc}"
            ) from exc

        # MCP 표준 응답: {content: [{type, text|data}], isError?: bool}
        if result.get("isError"):
            raise ToolCallError(
                f"{self.server.name}.{name} returned error: {result}"
            )
        content = result.get("content")
        if isinstance(content, list) and content:
            # 첫 번째 text 청크 우선
            for chunk in content:
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("type") == "text":
                    text = chunk.get("text", "")
                    # JSON 파싱 시도 (대부분 MCP 서버가 text로 JSON 반환)
                    try:
                        return json.loads(text)
                    except (json.JSONDecodeError, TypeError):
                        return text
            return content
        return result


# ── 멀티 서버 레지스트리 ──────────────────────────────────────────


def collect_tools(server_names: list[str] | None = None) -> list[MCPTool]:
    """지정 서버들의 모든 tool을 OpenAI 포맷 변환 가능한 형태로 수집.

    Args:
        server_names: None이면 known servers 전부.
    """
    names = server_names or list(list_known_servers().keys())
    out: list[MCPTool] = []
    for n in names:
        srv = get_server(n)
        client = MCPClient(srv, timeout=3.0)
        try:
            out.extend(client.list_tools())
        except (MCPUnavailableError, MCPError):
            continue  # 다운 서버는 건너뛰고 가용 서버만 제공
    return out


def tools_to_openai_format(tools: list[MCPTool]) -> list[dict[str, Any]]:
    return [t.to_openai_tool() for t in tools]


def call_qualified_tool(
    qualified_name: str,
    arguments: dict[str, Any],
    *,
    clients: dict[str, MCPClient] | None = None,
) -> Any:
    """`server__toolname` 형식의 qualified name으로 호출.

    Args:
        clients: 재사용할 MCPClient 매핑. 없으면 즉석 생성.
    """
    if SEPARATOR not in qualified_name:
        raise ValueError(f"Qualified name must contain '{SEPARATOR}': {qualified_name}")
    server_name, tool_name = qualified_name.split(SEPARATOR, 1)
    if clients and server_name in clients:
        client = clients[server_name]
    else:
        client = MCPClient(get_server(server_name))
    return client.call_tool(tool_name, arguments)


def tool_result_message(tool_call_id: str, name: str, result: Any) -> dict[str, Any]:
    """OpenAI messages 배열에 추가할 tool role 메시지."""
    if isinstance(result, (dict, list)):
        content = json.dumps(result, ensure_ascii=False)
    else:
        content = str(result)
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": name,
        "content": content[:8000],  # 극단 길이 방지
    }
