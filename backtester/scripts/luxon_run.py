"""
찬희 개인용 Luxon 상세 워크플로우 실행 스크립트.

사용:
    .venv/Scripts/python.exe backtester/scripts/luxon_run.py

기본값 하드코딩 — 개인 사용만. 바꾸고 싶으면 이 파일 직접 수정.
MCP 자동 시도: 접근 가능하면 매크로 지표 로드 (20초 추가), 실패 시 로컬 모드.

빠른 한 줄 호출을 원하면:
    .venv/Scripts/python.exe -m kis_backtest.luxon 005930 000660
"""
from __future__ import annotations

import asyncio
import logging
import sys

# Windows cp949 콘솔에서 한글/em-dash 출력 깨짐 방지
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# MCP provider 의 "VPS MCP 세션 ID 미설정" 노이즈 경고 억제
logging.getLogger("kis_backtest.portfolio.mcp_data_provider").setLevel(
    logging.ERROR
)

from pathlib import Path

from kis_backtest.luxon.graph import render_graph_html
from kis_backtest.luxon.graph.parsers.cufa_html_parser import CufaHtmlParser
from kis_backtest.luxon.orchestrator import LuxonOrchestrator
from kis_backtest.portfolio.catalyst_tracker import CatalystType


def _try_init_mcp() -> tuple[object | None, bool]:
    """MCP 프로바이더 생성 + health check. 실패 시 (None, False)."""
    try:
        from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
        mcp = MCPDataProvider()
        health = mcp.health_check_sync()
        if health.get("status") == "ok":
            return mcp, True
        print(f"[info] MCP health 실패 ({health}), 로컬 모드로 진행")
        return None, False
    except Exception as exc:  # noqa: BLE001
        print(f"[info] MCP 초기화 실패 ({exc!r}), 로컬 모드로 진행")
        return None, False


async def _main() -> None:
    mcp, use_mcp = _try_init_mcp()
    if use_mcp:
        print("[info] MCP 연결됨, 매크로 지표 로드 중 (최대 30초)...")

    orch = LuxonOrchestrator(mcp=mcp)

    if use_mcp:
        try:
            await orch.refresh_macro()
            print("[info] MCP 매크로 지표 로드 완료")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] refresh_macro 실패 ({exc!r}), regime 디폴트 사용")

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

    # CUFA 보고서 자동 스캔+주입 (Desktop 에 있는 보고서들 → 인물/섹터/테마 노드)
    parser = CufaHtmlParser()
    cufa_paths = list(Path.home().glob("Desktop/*CUFA*보고서*.html"))
    cufa_paths += list(
        Path.home().glob("Desktop/06_CUFA보고서/cufa_report_*/output/*.html")
    )
    for p in cufa_paths:
        try:
            digest = parser.parse_file(p)
            orch.add_cufa_digest(digest)
            print(f"[cufa] {p.name} → {digest.symbol} ({digest.sector})")
        except (ValueError, FileNotFoundError) as exc:  # noqa: BLE001
            print(f"[cufa] skip {p.name}: {exc}")

    # 관심도 수동 지정 — 카탈리스트 있는 종목 가중
    convictions = {s: 5.0 for s in symbols}
    convictions["005930"] = 8.0

    report = orch.run_workflow(symbols, base_convictions=convictions)
    print()
    print(report.summary())

    # GothamGraph HTML 시각화 자동 생성
    graph_html = Path("out/luxon_watchlist.html")
    graph_html.parent.mkdir(parents=True, exist_ok=True)
    render_graph_html(orch.graph, str(graph_html), title="Luxon Watchlist")
    print(f"\n[graph] file:///{graph_html.resolve().as_posix()}")


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
