"""
Luxon Terminal — 24/7/365 FRED 매크로 수집 데몬 (Sprint 1.5)

기능:
    - Nexus MCP `macro_fred` 경유 10개 거시 지표 수집
    - Parquet 캐시(pickle) 저장
    - Obsidian Vault에 마크다운 스냅샷 append (기존 VaultWriter 경로 규약)
    - AlertSystem으로 성공/실패/staleness 알림 (기존 execution/alerts.py 재사용)
    - 상태 파일 `~/.luxon/state/fred_daemon.json` 에 마지막 실행 이력 저장
    - 실패 시 지수 백오프 재시도 (최대 3회)

실행 모드:
    --mode oneshot   : 1회 실행 후 종료 (cron/Task Scheduler용)
    --mode loop      : 데몬 모드 (--interval 초마다 반복, CTRL+C로 종료)

환경 변수:
    LUXON_DISCORD_WEBHOOK : 설정 시 Discord 알림 (선택)
    LUXON_VAULT_ROOT      : Obsidian Vault 경로 (기본: ~/obsidian-vault)
    NEXUS_MCP_TOKEN       : MCPDataProvider가 자동 로드
    LUXON_CACHE_DIR       : FRED 캐시 경로 (기본: ~/.luxon/cache/fred)

사용 예:
    # 1회 실행
    python scripts/luxon_macro_daemon.py --mode oneshot
    # 60분마다 루프
    python scripts/luxon_macro_daemon.py --mode loop --interval 3600
    # 디스코드 웹훅 + 커스텀 출력
    LUXON_DISCORD_WEBHOOK=https://... python scripts/luxon_macro_daemon.py --mode oneshot --out-png ./out/macro.png
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _configure_logging(verbose: bool) -> None:
    # Windows 콘솔 cp949 방지 — stdout을 UTF-8 모드로 재구성
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except AttributeError:
            pass  # Python <3.7 or non-TextIOWrapper

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def _state_path() -> Path:
    """데몬 상태 파일 경로."""
    base = Path(os.environ.get("LUXON_STATE_DIR", Path.home() / ".luxon" / "state"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "fred_daemon.json"


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {
            "last_success": None,
            "last_failure": None,
            "total_runs": 0,
            "success_count": 0,
            "failure_count": 0,
            "consecutive_failures": 0,
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _load_state.__defaults__[0] if _load_state.__defaults__ else {}  # type: ignore


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _vault_macro_dir() -> Path:
    """Obsidian Vault의 매크로 스냅샷 디렉토리."""
    vault_root = Path(
        os.environ.get("LUXON_VAULT_ROOT", Path.home() / "obsidian-vault")
    )
    macro_dir = vault_root / "02-Areas" / "trading-ops" / "macro"
    macro_dir.mkdir(parents=True, exist_ok=True)
    return macro_dir


def _write_vault_snapshot(
    all_series: dict,
    stale_reports: list,
    out_png: Path | None,
) -> Path:
    """Obsidian Vault에 일일 매크로 스냅샷 마크다운 작성.

    경로 규약: ~/obsidian-vault/02-Areas/trading-ops/macro/YYYY-MM-DD.md
    VaultWriter와 동일한 trading-ops 영역 사용.
    """
    macro_dir = _vault_macro_dir()
    today = date.today().isoformat()
    path = macro_dir / f"{today}.md"

    lines = [
        "---",
        f"date: {today}",
        "type: macro-snapshot",
        "source: nexus-mcp-macro_fred",
        f"series_count: {len(all_series)}",
        f"stale_count: {sum(1 for r in stale_reports if r.is_stale)}",
        "tags: [luxon, macro, fred, daily]",
        "---",
        "",
        f"# FRED Macro Snapshot — {today}",
        "",
        "> Luxon Terminal Sprint 1.5 Daemon 자동 수집",
        "> Source: Nexus MCP `macro_fred` (Federal Reserve)",
        "",
        "## 📊 지표 현황",
        "",
        "| 카테고리 | 지표 | 최신값 | 관측일 | 상태 |",
        "|---------|------|--------|--------|------|",
    ]

    # 시리즈 순서 고정
    from kis_backtest.luxon.stream.schema import FredSeriesId

    stale_map = {r.series_id: r for r in stale_reports}
    for sid in FredSeriesId:
        if sid not in all_series:
            continue
        s = all_series[sid]
        latest = float(s.data["value"].iloc[-1]) if len(s.data) > 0 else float("nan")
        stale = stale_map.get(sid)
        status = "🟢 OK"
        if stale and stale.is_stale:
            status = f"🔴 {stale.business_days_stale}일 지연"
        lines.append(
            f"| {s.meta.category.value} | {s.meta.label_ko} | "
            f"{latest:.2f} {s.meta.unit} | {s.last_observation.isoformat()} | {status} |"
        )

    lines.append("")
    lines.append("## 🖼️ 대시보드")
    lines.append("")
    if out_png and out_png.exists():
        # Obsidian은 절대 경로 embed 지원
        lines.append(f"![[{out_png.name}]]")
    else:
        lines.append("_대시보드 PNG 미생성_")

    lines.append("")
    lines.append(
        f"_자동 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · "
        "Luxon Terminal Sprint 1.5 Daemon_"
    )
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


async def _run_once(
    out_png: Path,
    out_html: Path,
    verbose: bool,
) -> tuple[bool, str]:
    """데몬 1회 실행. 반환: (성공 여부, 요약 메시지)."""
    log = logging.getLogger("luxon_macro_daemon")

    try:
        from kis_backtest.luxon.stream.fred_hub import FREDHub
        from kis_backtest.luxon.stream.schema import FredSeriesId
        from kis_backtest.luxon.ui.macro_dashboard import MacroDashboard
        from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
    except ImportError as e:
        return False, f"패키지 import 실패: {e}"

    # MCP + Hub
    mcp = MCPDataProvider()
    hub = FREDHub(mcp=mcp)

    log.info("MCP macro_fred 10 시리즈 수집 시작...")
    all_series = await hub.load_all(force_refresh=True)

    if len(all_series) < 10:
        return False, (
            f"수집 불완전: {len(all_series)}/10 시리즈 "
            f"(staleness 또는 MCP 실패 가능)"
        )

    # Staleness
    stale_reports = [hub.detect_staleness(s) for s in all_series.values()]
    stale_count = sum(1 for r in stale_reports if r.is_stale)

    # 렌더링
    dashboard = MacroDashboard()
    dashboard.render_png(all_series, out_png)
    dashboard.render_html(all_series, out_html)
    log.info("대시보드 PNG + HTML 저장 완료")

    # Vault 스냅샷
    try:
        vault_path = _write_vault_snapshot(all_series, stale_reports, out_png)
        log.info("Obsidian Vault 스냅샷: %s", vault_path)
    except Exception as e:
        log.warning("Vault 스냅샷 실패 (비치명적): %s", e)

    summary = (
        f"FRED 10/10 수집 성공 · Stale {stale_count}/10 · "
        f"PNG {out_png.stat().st_size // 1024}KB"
    )
    return True, summary


def _send_alert(
    success: bool,
    summary: str,
    webhook_url: str | None,
) -> None:
    """AlertSystem 경유 알림 발송."""
    try:
        from kis_backtest.execution.alerts import AlertSystem
    except ImportError:
        logging.getLogger("luxon_macro_daemon").warning(
            "AlertSystem import 실패 (비치명적)"
        )
        return

    alerts = AlertSystem(discord_webhook_url=webhook_url)
    # 콘솔 안전: 이모지는 Discord 웹훅 embed에만 (콘솔은 평문)
    if success:
        alerts.info(
            "[OK] FRED Daemon",
            summary,
            source="luxon_macro_daemon",
        )
    else:
        alerts.critical(
            "[FAIL] FRED Daemon",
            summary,
            source="luxon_macro_daemon",
        )


async def _loop_mode(
    interval_sec: int,
    out_png: Path,
    out_html: Path,
    verbose: bool,
    webhook_url: str | None,
) -> None:
    """무한 루프 데몬."""
    log = logging.getLogger("luxon_macro_daemon")
    log.info("루프 모드 시작: %d초 간격", interval_sec)

    stop_flag = {"stop": False}

    def _shutdown_handler(*_: Any) -> None:
        log.info("종료 시그널 수신, 다음 반복 후 종료")
        stop_flag["stop"] = True

    signal.signal(signal.SIGINT, _shutdown_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown_handler)

    while not stop_flag["stop"]:
        state = _load_state()
        state["total_runs"] += 1
        start = time.time()

        success, summary = await _run_once(out_png, out_html, verbose)

        if success:
            state["last_success"] = datetime.now().isoformat()
            state["success_count"] += 1
            state["consecutive_failures"] = 0
        else:
            state["last_failure"] = datetime.now().isoformat()
            state["failure_count"] += 1
            state["consecutive_failures"] += 1
            log.error("실행 실패: %s", summary)

        _save_state(state)
        _send_alert(success, summary, webhook_url)

        elapsed = time.time() - start
        sleep_for = max(1, interval_sec - int(elapsed))
        log.info("다음 실행까지 %d초 대기 (총 실행 %d회)", sleep_for, state["total_runs"])

        for _ in range(sleep_for):
            if stop_flag["stop"]:
                break
            time.sleep(1)

    log.info("루프 종료")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Luxon Terminal FRED 매크로 수집 데몬 (Sprint 1.5)"
    )
    parser.add_argument(
        "--mode",
        choices=["oneshot", "loop"],
        default="oneshot",
        help="실행 모드",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=3600,
        help="루프 모드 반복 주기 (초, 기본 3600=1시간)",
    )
    parser.add_argument(
        "--out-png",
        type=Path,
        default=Path("./out") / f"macro_{datetime.now():%Y%m%d}.png",
    )
    parser.add_argument(
        "--out-html",
        type=Path,
        default=Path("./out") / f"macro_{datetime.now():%Y%m%d}.html",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    _configure_logging(args.verbose)
    log = logging.getLogger("luxon_macro_daemon")

    webhook = os.environ.get("LUXON_DISCORD_WEBHOOK")
    if webhook:
        log.info("Discord 웹훅 활성 (URL 감춤)")

    log.info("=" * 60)
    log.info("Luxon Terminal Sprint 1.5 — FRED Macro Daemon")
    log.info("Mode: %s", args.mode)
    log.info("=" * 60)

    if args.mode == "oneshot":
        state = _load_state()
        state["total_runs"] += 1

        success, summary = await _run_once(args.out_png, args.out_html, args.verbose)

        if success:
            state["last_success"] = datetime.now().isoformat()
            state["success_count"] += 1
            state["consecutive_failures"] = 0
            log.info("✅ 성공: %s", summary)
        else:
            state["last_failure"] = datetime.now().isoformat()
            state["failure_count"] += 1
            state["consecutive_failures"] += 1
            log.error("❌ 실패: %s", summary)

        _save_state(state)
        _send_alert(success, summary, webhook)
        return 0 if success else 1

    # loop mode
    await _loop_mode(
        args.interval,
        args.out_png,
        args.out_html,
        args.verbose,
        webhook,
    )
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
