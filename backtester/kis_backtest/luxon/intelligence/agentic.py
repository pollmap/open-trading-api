"""
Agentic Loop — 로컬 LLM이 MCP tool을 호출하며 과제를 수행.

Sprint F 코어. Claude Code의 MCP 오케스트레이션 역할을 로컬 LLM으로 대체.

Flow:
    1. collect_tools(mcp_servers) → OpenAI tools 배열
    2. call_with_tools(tier, messages, tools) → ChatResult
    3. tool_calls 있으면 각각 실행 → tool role 메시지 추가 → 다시 call
    4. tool_calls 없거나 max_steps 도달 시 종료

바벨 전략 적용:
    tier=FAST     → 단순 단일 tool 호출 (상태 조회)
    tier=DEFAULT  → 2-3단계 체인 (분석 → 시각화)
    tier=HEAVY    → 복합 추론 체인 (재무 수집 → 계산 → 판단)
    tier=LONG     → 대량 도구 호출 + 긴 컨텍스트 유지
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kis_backtest.luxon.intelligence.mcp_bridge import (
    MCPClient,
    MCPTool,
    call_qualified_tool,
    collect_tools,
    tool_result_message,
    tools_to_openai_format,
)
from kis_backtest.luxon.intelligence.mcp_registry import get_server
from kis_backtest.luxon.intelligence.router import (
    ChatResult,
    Tier,
    ToolCall,
    call_with_tools,
)


class AgenticLoopExhausted(RuntimeError):
    """max_steps 도달 후에도 tool_calls 지속 — 무한루프 가드."""


@dataclass
class AgenticStep:
    step: int
    content: str
    tool_calls: tuple[ToolCall, ...]
    tool_results: list[Any] = field(default_factory=list)


@dataclass
class AgenticResult:
    final_content: str
    steps: list[AgenticStep] = field(default_factory=list)
    final_messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_tool_calls(self) -> int:
        return sum(len(s.tool_calls) for s in self.steps)


def agentic_run(
    user_prompt: str,
    *,
    tier: Tier = Tier.DEFAULT,
    mcp_servers: list[str] | None = None,
    system: str | None = None,
    max_steps: int = 5,
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> AgenticResult:
    """로컬 LLM이 MCP tools 호출하며 사용자 요청 처리.

    Args:
        user_prompt: 사용자 질문.
        tier: 사용할 로컬 LLM 티어.
        mcp_servers: 활성화할 MCP 서버 이름 목록. None이면 known 전체.
        system: 시스템 프롬프트. 없으면 기본 agentic 지시.
        max_steps: LLM↔tool 왕복 최대 횟수.
        temperature / max_tokens: LLM 파라미터.

    Returns:
        AgenticResult(final_content, steps, final_messages).

    Raises:
        AgenticLoopExhausted: max_steps 초과해도 tool_calls 지속.
    """
    tools = collect_tools(mcp_servers)
    tool_schema = tools_to_openai_format(tools)

    # 서버별 client 재사용
    clients: dict[str, MCPClient] = {}
    server_names = {t.server for t in tools}
    for s in server_names:
        clients[s] = MCPClient(get_server(s))

    sys_prompt = system or (
        "너는 Luxon AI 에이전트다. 사용자 질문에 답하기 위해 MCP tool을 활용하라.\n"
        "규칙:\n"
        "- 필요한 tool만 호출하라. 불필요한 호출 금지.\n"
        "- tool 결과를 받으면 사용자 언어(한국어)로 명확히 요약하라.\n"
        "- 정보가 충분하면 즉시 답하라. 더 필요하면 추가 tool 호출.\n"
        "- 도구 결과에 없는 정보는 추측하지 말고 '확인 필요'로 표기."
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]

    steps: list[AgenticStep] = []

    for step_idx in range(max_steps):
        result: ChatResult = call_with_tools(
            tier,
            messages=messages,
            tools=tool_schema,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        step = AgenticStep(
            step=step_idx + 1,
            content=result.content or "",
            tool_calls=result.tool_calls,
        )

        # 어시스턴트 응답 메시지 추가 (tool_calls 포함)
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": result.content or "",
        }
        if result.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments,
                    },
                }
                for tc in result.tool_calls
            ]
        messages.append(assistant_msg)

        if not result.tool_calls:
            # LLM이 최종 답변 생성
            steps.append(step)
            return AgenticResult(
                final_content=result.content or "",
                steps=steps,
                final_messages=messages,
            )

        # 각 tool 실행 + 결과 메시지 추가
        for tc in result.tool_calls:
            try:
                tool_out = call_qualified_tool(tc.name, tc.arguments, clients=clients)
                step.tool_results.append(tool_out)
            except Exception as exc:  # noqa: BLE001
                tool_out = {"error": f"{type(exc).__name__}: {exc}"}
                step.tool_results.append(tool_out)
            messages.append(tool_result_message(tc.id, tc.name, tool_out))

        steps.append(step)

    # max_steps 초과
    raise AgenticLoopExhausted(
        f"max_steps={max_steps} 초과. 마지막 step의 tool_calls={len(steps[-1].tool_calls) if steps else 0}"
    )
