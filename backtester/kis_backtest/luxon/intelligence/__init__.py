"""
Luxon Terminal — AIP 레이어 (LLM 에이전트 오케스트레이션)

Phase 2~7에서 구현:
    llm/            — Universal LLM Adapter (Claude/GPT/DeepSeek/Gemini/Grok/Ollama)
    copilot         — AIPCopilot (Claude + MCP 398 tools + portfolio/* tools)
    rtk_cache       — 토큰 60-90% 절감 (Sprint 11)
    conviction_adapter — AckmanDruckenmillerEngine 100% 재사용 래퍼
    feedback_loop   — walk_forward → 파라미터 제안 큐

참조:
    플랜: 섹션 13.66.2 (Universal LLM Adapter), 섹션 13.4 (6-에이전트 팩)
"""

__all__: list[str] = []
