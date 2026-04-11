"""
Luxon Terminal — 1인 AI 헤지펀드 × AaaS 퀀트 OS

서브패키지 구조:
    luxon/stream/       — Maven 레이어 (실시간 데이터 허브)
    luxon/ontology/     — Gotham 레이어 (엔티티 그래프) [Phase 2]
    luxon/intelligence/ — AIP 레이어 (LLM 에이전트) [Phase 2]
    luxon/ui/           — UI 레이어 (TUI + Chart + Web) [Phase 3]

설계 원칙:
    1. 기존 portfolio/execution/providers 모듈 무수정, 사이드카 확장
    2. 모든 주문 경로는 RiskGateway → KillSwitch → CapitalLadder 강제 통과
    3. 6-에이전트 병렬 개발 팩 + A7 감사 프로토콜
    4. 실데이터 절대 원칙, 목업 금지

참조:
    플랜: C:\\Users\\lch68\\.claude\\plans\\valiant-honking-simon.md
    세션 시작일: 2026-04-11
"""

__version__ = "0.1.0-sprint1-t0"
__all__: list[str] = []
