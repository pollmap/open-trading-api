"""
Luxon Intelligence Layer — 바벨 전략 4-티어 로컬 LLM + MCP 브리지.

티어:
    FAST    (NPU qwen3.5:4b, ctx 4k)      — 시그널·분류·알림
    DEFAULT (CPU qwen3:14b,  ctx 16k)     — 일반 섹션·에이전트
    HEAVY   (CPU gemma4:26b, ctx 8k, KVq8) — 정밀·반증·Kill condition
    LONG    (iGPU gemma4-e4b, ctx 32k)    — 긴 문서·RAG·MCP 대량 호출

사용:
    from kis_backtest.luxon.intelligence import Tier, call
    text = call(Tier.DEFAULT, system="...", user="...")

    # Sprint F tool-calling:
    from kis_backtest.luxon.intelligence import call_with_tools, ChatResult
    result = call_with_tools(Tier.HEAVY, messages=[...], tools=[...])

클라우드 폴백 없음. 로컬 실패 = LocalLLMError raise.
"""
# .env 자동 로드 (모듈 import 시점, 1회만)
from kis_backtest.luxon.intelligence._env import autoload as _autoload_env

_autoload_env()

from kis_backtest.luxon.intelligence.router import (
    ChatResult,
    ContextLimitExceededError,
    LocalLLMError,
    Tier,
    TierConfig,
    TierUnavailableError,
    ToolCall,
    call,
    call_with_tools,
    estimate_tokens,
    health_check,
    health_check_all,
)

# Sprint F: MCP 브리지 + agentic loop
from kis_backtest.luxon.intelligence.agentic import (
    AgenticLoopExhausted,
    AgenticResult,
    AgenticStep,
    agentic_run,
)
from kis_backtest.luxon.intelligence.mcp_bridge import (
    MCPClient,
    MCPError,
    MCPTool,
    MCPUnavailableError,
    ToolCallError,
    call_qualified_tool,
    collect_tools,
    tools_to_openai_format,
)
from kis_backtest.luxon.intelligence.mcp_registry import (
    MCPServerInfo,
    get_server,
    list_known_servers,
)

__all__ = [
    # router
    "Tier", "TierConfig", "ChatResult", "ToolCall",
    "call", "call_with_tools",
    "health_check", "health_check_all", "estimate_tokens",
    "LocalLLMError", "TierUnavailableError", "ContextLimitExceededError",
    # mcp_registry
    "MCPServerInfo", "get_server", "list_known_servers",
    # mcp_bridge
    "MCPClient", "MCPTool", "MCPError", "MCPUnavailableError", "ToolCallError",
    "collect_tools", "call_qualified_tool", "tools_to_openai_format",
    # agentic
    "agentic_run", "AgenticResult", "AgenticStep", "AgenticLoopExhausted",
]
