"""
찬희 개인용 Luxon 워크플로우 1회 실행 스크립트.

사용:
    .venv/Scripts/python.exe backtester/scripts/luxon_run.py

기본값 하드코딩 — 개인 사용만, 인자 받지 않음. 바꾸고 싶으면 이 파일 직접 수정.
MCP 연결은 skip (Windows 에서 VPS IP 제한 걸림). 필요하면 mcp 변수 주석 해제.
"""
from __future__ import annotations

import sys

# Windows cp949 콘솔에서 한글/em-dash 출력 깨짐 방지
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from kis_backtest.luxon.orchestrator import LuxonOrchestrator
from kis_backtest.portfolio.catalyst_tracker import CatalystType


def main() -> None:
    # MCP 없이 로컬 전용 (Windows IP 제한 회피)
    # 필요하면 이 두 줄 주석 해제:
    # from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
    # mcp = MCPDataProvider()
    orch = LuxonOrchestrator(mcp=None)

    # 찬희 관심종목 10개 (2026-04 시점)
    symbols = [
        "005930",  # 삼성전자
        "000660",  # SK하이닉스
        "035420",  # NAVER
        "373220",  # LG에너지솔루션
        "207940",  # 삼성바이오로직스
        "035720",  # 카카오
        "068270",  # 셀트리온
        "105560",  # KB금융
        "000270",  # 기아
        "005380",  # 현대차
    ]

    # 예시 카탈리스트 1개 — 필요 시 더 추가
    orch.add_catalyst(
        symbol="005930",
        name="HBM4 양산 본격화",
        catalyst_type=CatalystType.INDUSTRY,
        expected_date="2026-05-15",
        probability=0.7,
        impact=8.0,
    )

    # 관심도 수동 지정 — 카탈리스트 있는 종목 가중
    convictions = {s: 5.0 for s in symbols}
    convictions["005930"] = 8.0

    report = orch.run_workflow(symbols, base_convictions=convictions)
    print(report.summary())


if __name__ == "__main__":
    main()
