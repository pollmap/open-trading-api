"""Sprint 1 수동 스모크 스크립트.

실제 Nexus MCP에 연결하여 10개 FRED 시리즈를 로드하고
다크 대시보드 PNG + HTML을 생성한다.

사용법:
    cd C:\\Users\\lch68\\Desktop\\open-trading-api\\backtester
    .venv\\Scripts\\python.exe scripts/smoke_sprint1.py [--out OUT_PNG] [--html OUT_HTML]

환경:
    NEXUS_MCP_TOKEN 환경 변수 또는 ~/.mcp.json에 nexus-finance 등록 필수
    (MCPDataProvider가 자동 로드)

DoD:
    - MCP 연결 성공 + 10개 시리즈 실데이터 수신
    - PNG + HTML 파일 생성 (non-empty)
    - staleness 리포트 콘솔 출력
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def main(out_png: Path, out_html: Path, verbose: bool) -> int:
    _configure_logging(verbose)
    log = logging.getLogger("smoke_sprint1")

    # 지연 import (스크립트 실행 시 패키지 경로 문제 방지)
    try:
        from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
        from kis_backtest.luxon.stream.fred_hub import FREDHub
        from kis_backtest.luxon.stream.schema import FredSeriesId
        from kis_backtest.luxon.ui.macro_dashboard import MacroDashboard
    except ImportError as e:
        log.error("패키지 import 실패: %s", e)
        log.error("backtester 루트에서 실행하세요 (또는 .venv 활성화)")
        return 1

    log.info("=" * 60)
    log.info("Luxon Terminal Sprint 1 — FRED Macro Dashboard Smoke")
    log.info("=" * 60)

    # MCP 연결
    log.info("MCPDataProvider 초기화...")
    mcp = MCPDataProvider()

    # FREDHub
    log.info("FREDHub 초기화 (Nexus MCP fred_get_series 주 경로)")
    hub = FREDHub(mcp=mcp)

    # 레지스트리 확인
    all_metas = hub.registry.all_series()
    log.info("레지스트리 로드: %d개 시리즈", len(all_metas))
    for meta in all_metas:
        log.info(
            "  · %s (%s) — %s [%s]",
            meta.id.value,
            meta.fred_code,
            meta.label_ko,
            meta.category.value,
        )

    # 전체 시리즈 로드
    log.info("MCP fred_get_series 10개 시리즈 실데이터 수집 시작...")
    try:
        all_series = await hub.load_all()
    except Exception as e:
        log.exception("FRED 시리즈 수집 실패: %s", e)
        return 2

    log.info("수집 완료: %d개 시리즈", len(all_series))

    # Staleness 리포트
    log.info("\n--- Staleness Report ---")
    stale_count = 0
    for sid, series in all_series.items():
        report = hub.detect_staleness(series)
        status = "🟢 OK" if not report.is_stale else "🔴 STALE"
        log.info(
            "  %s %s (%s) — 마지막 관측: %s (%d일 지연)",
            status,
            sid.value,
            series.meta.label_ko,
            report.last_observation.isoformat(),
            report.business_days_stale,
        )
        if report.is_stale:
            stale_count += 1
    if stale_count:
        log.warning("⚠️ %d개 시리즈 staleness 초과", stale_count)

    # 대시보드 렌더
    log.info("\n--- Rendering Dashboard ---")
    dashboard = MacroDashboard()

    out_png.parent.mkdir(parents=True, exist_ok=True)
    dashboard.render_png(all_series, out_png)
    log.info("PNG 저장: %s", out_png.absolute())

    dashboard.render_html(all_series, out_html)
    log.info("HTML 저장: %s", out_html.absolute())

    # 캐시 현황
    stats = hub.cache.stats()
    log.info("\n--- Cache Stats ---")
    log.info("  dir: %s", stats["cache_dir"])
    log.info("  ttl: %sh", stats["ttl_hours"])
    log.info("  cached: %s 시리즈, %s bytes", stats["cached_series"], stats["total_bytes"])

    log.info("\n✅ Sprint 1 스모크 완료")
    log.info("다음: 브라우저에서 %s 열기", out_html)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Luxon Terminal Sprint 1 FRED 대시보드 스모크"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("./out") / f"macro_{datetime.now():%Y%m%d}.png",
        help="PNG 출력 경로 (기본: ./out/macro_YYYYMMDD.png)",
    )
    parser.add_argument(
        "--html",
        type=Path,
        default=Path("./out") / f"macro_{datetime.now():%Y%m%d}.html",
        help="HTML 출력 경로",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    exit_code = asyncio.run(main(args.out, args.html, args.verbose))
    sys.exit(exit_code)
