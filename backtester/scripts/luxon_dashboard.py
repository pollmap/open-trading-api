"""
Luxon Terminal Dashboard — Bloomberg 스타일 터미널 대시보드.

사용:
    python scripts/luxon_dashboard.py
    python scripts/luxon_dashboard.py 005930 000660 035420
    python scripts/luxon_dashboard.py --refresh 30   # 30초마다 갱신
    python scripts/luxon_dashboard.py --no-mcp       # 로컬 모드

레이아웃 (Bloomberg 4분할):
    ┌─────────────────────┬─────────────────────┐
    │  포트폴리오 현황    │  TA 신호 피드       │
    │  (Ackman 결정)      │  (RSI/MACD/BB)      │
    ├─────────────────────┼─────────────────────┤
    │  모의매매 Fill 기록 │  백테스트 결과      │
    │  (paper fills)      │  (WF + Risk)        │
    └─────────────────────┴─────────────────────┘
    하단: 매크로 레짐 + 카탈리스트 요약
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# 경로 주입
BACKTESTER = Path(__file__).resolve().parent.parent
if str(BACKTESTER) not in sys.path:
    sys.path.insert(0, str(BACKTESTER))

# Windows 인코딩
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kis_backtest.luxon.orchestrator import LuxonOrchestrator

console = Console()

FILL_DIR = BACKTESTER / "fills" / "paper"
TICKET_DIR = BACKTESTER / "tickets" / "hourly"
REPORT_DIR = BACKTESTER / "reports" / "hourly"

# 기본 관심 종목
DEFAULT_SYMBOLS = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "035420",  # NAVER
    "373220",  # LG에너지솔루션
    "207940",  # 삼성바이오로직스
]

# 종목 이름 매핑 (표시용)
TICKER_NAMES = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "NAVER",
    "373220": "LG에솔",
    "207940": "삼바",
    "035720": "카카오",
    "068270": "셀트리온",
    "105560": "KB금융",
    "000270": "기아",
    "005380": "현대차",
}


# ── MCP 초기화 ───────────────────────────────────────────────────────

def _try_init_mcp():
    try:
        from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
        mcp = MCPDataProvider()
        health = mcp.health_check_sync()
        if health.get("status") == "ok":
            return mcp, True
        return None, False
    except Exception:
        return None, False


# ── 데이터 수집 ──────────────────────────────────────────────────────

def _load_recent_fills(n: int = 10) -> list[dict]:
    """최근 fill 레코드 N개 로드."""
    fills = []
    if FILL_DIR.exists():
        for f in sorted(FILL_DIR.glob("*.json"), reverse=True)[:n]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    fills.extend(data)
                elif isinstance(data, dict):
                    fills.append(data)
            except Exception:
                pass
    return fills[:n]


def _load_recent_tickets(n: int = 5) -> list[dict]:
    """최근 티켓 N개 로드."""
    tickets = []
    if TICKET_DIR.exists():
        for f in sorted(TICKET_DIR.glob("*.json"), reverse=True)[:n]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                items = data if isinstance(data, list) else [data]
                for it in items:
                    if isinstance(it, dict):
                        it["_source"] = f.stem
                        tickets.append(it)
            except Exception:
                pass
    return tickets[:n]


# ── 패널 렌더러 ──────────────────────────────────────────────────────

def render_portfolio_panel(report, symbols: list[str]) -> Panel:
    """Ackman 결정 + 포지션 사이즈 테이블."""
    t = Table(
        show_header=True,
        header_style="bold cyan",
        box=box.SIMPLE_HEAVY,
        expand=True,
        show_lines=False,
    )
    t.add_column("TICKER", style="bold white", width=8)
    t.add_column("이름", width=8)
    t.add_column("결정", width=8)
    t.add_column("확신도", justify="right", width=6)
    t.add_column("비중%", justify="right", width=6)
    t.add_column("금액(만)", justify="right", width=8)

    if report is None:
        t.add_row("-", "-", "[yellow]데이터 없음[/]", "-", "-", "-")
    else:
        # PortfolioDecision.decisions: list[InvestmentDecision]
        decisions = {d.symbol: d for d in report.portfolio.decisions}
        sizes = {ps.symbol: ps for ps in report.position_sizes}

        for sym in symbols:
            d = decisions.get(sym)
            ps = sizes.get(sym)
            name = TICKER_NAMES.get(sym, sym)

            if d is None:
                t.add_row(sym, name, "[dim]분석중[/]", "-", "-", "-")
                continue

            action = d.action.upper() if hasattr(d, "action") else str(d.decision)
            if "buy" in action.lower() or "long" in action.lower():
                action_str = f"[bold green]{action}[/]"
            elif "skip" in action.lower() or "hold" in action.lower():
                action_str = f"[yellow]{action}[/]"
            else:
                action_str = f"[red]{action}[/]"

            conviction = f"{d.conviction_score:.1f}" if hasattr(d, "conviction_score") else "-"

            if ps:
                pct = f"{ps.weight_pct:.1f}"
                amt = f"{ps.amount_krw / 10000:.0f}"
            else:
                pct = "-"
                amt = "-"

            t.add_row(sym, name, action_str, conviction, pct, amt)

    regime = getattr(report, "regime", "N/A") if report else "N/A"
    conf = getattr(report, "regime_confidence", 0) if report else 0
    title = f"[bold cyan]PORTFOLIO[/]  [dim]Regime:[/] [yellow]{regime.upper()}[/] [dim]({conf:.0%})[/]"
    return Panel(t, title=title, border_style="cyan", box=box.HEAVY_HEAD)


def render_signal_panel(mcp, symbols: list[str]) -> Panel:
    """MCP TA 신호 피드."""
    t = Table(
        show_header=True,
        header_style="bold magenta",
        box=box.SIMPLE_HEAVY,
        expand=True,
        show_lines=False,
    )
    t.add_column("TICKER", width=8)
    t.add_column("지표", width=8)
    t.add_column("신호", width=14)
    t.add_column("방향", width=6, justify="center")
    t.add_column("Impact", width=6, justify="right")

    if mcp is None:
        t.add_row("-", "-", "[dim]MCP 미연결[/]", "-", "-")
    else:
        try:
            from kis_backtest.luxon.graph.graph import GothamGraph
            from kis_backtest.portfolio.catalyst_tracker import CatalystTracker
            from kis_backtest.luxon.graph.ingestors.ta_signal_ingestor import TASignalIngestor
            g = GothamGraph()
            tr = CatalystTracker()
            ingestor = TASignalIngestor(g, tr)
            result = ingestor.ingest_sync(mcp, symbols)
            if not result:
                t.add_row("-", "-", "[dim]신호 없음 (중립)[/]", "-", "-")
            else:
                for sym, sigs in result.items():
                    for sig in sigs:
                        dir_str = "[green]▲ 강세[/]" if sig.impact > 0 else "[red]▼ 약세[/]"
                        impact_str = f"[green]+{sig.impact:.0f}[/]" if sig.impact > 0 else f"[red]{sig.impact:.0f}[/]"
                        t.add_row(sym, sig.source, sig.name, dir_str, impact_str)
        except Exception as e:
            t.add_row("-", "-", f"[red]오류: {e}[/]", "-", "-")

    ts = datetime.now().strftime("%H:%M:%S")
    return Panel(t, title=f"[bold magenta]TA SIGNALS[/]  [dim]{ts}[/]", border_style="magenta", box=box.HEAVY_HEAD)


def render_fills_panel() -> Panel:
    """페이퍼 트레이딩 fill 기록."""
    fills = _load_recent_fills(8)
    tickets = _load_recent_tickets(5)

    t = Table(
        show_header=True,
        header_style="bold green",
        box=box.SIMPLE_HEAVY,
        expand=True,
        show_lines=False,
    )
    t.add_column("시각", width=12)
    t.add_column("TICKER", width=8)
    t.add_column("상태", width=10)
    t.add_column("유형", width=6)
    t.add_column("비고", width=20)

    if not fills and not tickets:
        t.add_row("[dim]기록 없음[/]", "", "", "", "")
        t.add_row("[dim]fills/paper/ 또는 tickets/hourly/ 확인[/]", "", "", "", "")
    else:
        for fill in fills[:4]:
            ts = fill.get("timestamp", fill.get("run_id", "?"))[:12]
            ticker = fill.get("ticker", "?")
            status = fill.get("status", fill.get("result", "?"))
            ftype = fill.get("type", "FILL")
            note = fill.get("order_no", fill.get("rationale", ""))[:20]
            if "ok" in str(status).lower() or "success" in str(status).lower():
                status_str = f"[green]{status}[/]"
            else:
                status_str = f"[yellow]{status}[/]"
            t.add_row(str(ts), str(ticker), status_str, str(ftype), str(note))

        for tk in tickets[:4]:
            src = tk.get("_source", "?")[:12]
            ticker = tk.get("ticker", "?")
            action = tk.get("action", "?")
            if action.upper() == "BUY":
                action_str = "[green]BUY[/]"
            elif action.upper() == "AVOID":
                action_str = "[red]AVOID[/]"
            else:
                action_str = f"[yellow]{action}[/]"
            rationale = tk.get("rationale", "")[:20]
            t.add_row(str(src), str(ticker), action_str, "TICKET", str(rationale))

    return Panel(t, title="[bold green]PAPER FILLS & TICKETS[/]", border_style="green", box=box.HEAVY_HEAD)


def render_backtest_panel(report) -> Panel:
    """백테스트 / Walk-Forward 결과."""
    t = Table(
        show_header=True,
        header_style="bold yellow",
        box=box.SIMPLE_HEAVY,
        expand=True,
        show_lines=False,
    )
    t.add_column("항목", width=18)
    t.add_column("값", width=20)
    t.add_column("평가", width=10)

    if report is None:
        t.add_row("[dim]리포트 없음[/]", "", "")
    else:
        # 매크로 레짐
        regime = getattr(report, "regime", "N/A")
        conf = getattr(report, "regime_confidence", 0)
        conf_color = "green" if conf >= 0.7 else "yellow" if conf >= 0.4 else "red"
        t.add_row("매크로 레짐", f"[bold]{regime.upper()}[/]", f"[{conf_color}]{conf:.0%}[/]")

        # 포지션 요약
        ps_list = getattr(report, "position_sizes", [])
        total_deployed = sum(ps.amount_krw for ps in ps_list if hasattr(ps, "amount_krw")) / 1e8
        t.add_row("투자집행", f"{total_deployed:.2f}억", "[cyan]배분완료[/]" if total_deployed > 0 else "[dim]SKIP[/]")

        # 종목별 Catalyst Score
        portfolio = getattr(report, "portfolio", None)
        if portfolio and hasattr(portfolio, "decisions"):
            for d in portfolio.decisions[:4]:
                sym = d.symbol
                score = getattr(d, "catalyst_score", None)
                if score is not None:
                    score_color = "green" if score >= 3 else "yellow" if score >= 1 else "red"
                    t.add_row(
                        f"catalyst({TICKER_NAMES.get(sym, sym)})",
                        f"{score:.2f}",
                        f"[{score_color}]{'GO' if score >= 1 else 'SKIP'}[/]"
                    )

        # 생성시각
        gen_at = getattr(report, "generated_at", "")[:19]
        t.add_row("[dim]생성시각[/]", f"[dim]{gen_at}[/]", "")

    # 최근 리포트 파일 확인
    recent_rpt = None
    if REPORT_DIR.exists():
        rpts = sorted(REPORT_DIR.glob("*.md"), reverse=True)
        if rpts:
            recent_rpt = rpts[0]

    footer = f"[dim]최근 리포트: {recent_rpt.name if recent_rpt else 'None'}[/]"
    return Panel(t, title=f"[bold yellow]BACKTEST & ANALYSIS[/]  {footer}", border_style="yellow", box=box.HEAVY_HEAD)


def render_macro_bar(report) -> Panel:
    """하단 매크로 + 카탈리스트 요약 바."""
    regime = "N/A"
    conf = 0.0
    cats_summary = []

    if report:
        regime = getattr(report, "regime", "N/A")
        conf = getattr(report, "regime_confidence", 0.0)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S KST")
    regime_color = {"expansion": "green", "recovery": "cyan", "contraction": "red", "stagflation": "magenta"}.get(
        regime.lower(), "white"
    )

    parts = [
        Text.from_markup(f"[bold]LUXON TERMINAL[/]  "),
        Text.from_markup(f"[dim]{now_str}[/]  "),
        Text.from_markup(f"[dim]Regime:[/] [{regime_color}]{regime.upper()}[/] [{conf:.0%}]  "),
        Text.from_markup("[dim]MCP:[/] [green]OK[/]" if report else "[dim]MCP:[/] [red]OFFLINE[/]"),
        Text.from_markup("  [dim]| Paper: [/][green]ACTIVE[/]" if FILL_DIR.exists() else "  [dim]| Paper: [/][red]OFF[/]"),
        Text.from_markup("  [dim]| Press Ctrl+C to exit[/]"),
    ]
    combined = Text()
    for p in parts:
        combined.append_text(p)

    return Panel(combined, border_style="dim white", box=box.HORIZONTALS, padding=(0, 1))


# ── 전체 레이아웃 ────────────────────────────────────────────────────

def build_layout(report, mcp, symbols: list[str]) -> Layout:
    """Bloomberg 4분할 레이아웃 구성."""
    layout = Layout()

    layout.split_column(
        Layout(name="main", ratio=9),
        Layout(name="footer", ratio=1),
    )

    layout["main"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="right", ratio=1),
    )

    layout["left"].split_column(
        Layout(name="portfolio", ratio=1),
        Layout(name="fills", ratio=1),
    )

    layout["right"].split_column(
        Layout(name="signals", ratio=1),
        Layout(name="backtest", ratio=1),
    )

    layout["portfolio"].update(render_portfolio_panel(report, symbols))
    layout["signals"].update(render_signal_panel(mcp, symbols))
    layout["fills"].update(render_fills_panel())
    layout["backtest"].update(render_backtest_panel(report))
    layout["footer"].update(render_macro_bar(report))

    return layout


# ── 메인 ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="luxon_dashboard",
        description="Luxon Terminal Dashboard — Bloomberg 스타일 실시간 대시보드",
    )
    parser.add_argument("symbols", nargs="*", default=DEFAULT_SYMBOLS,
                        help="종목 코드 (기본: 삼전/하이닉스/NAVER/LG에솔/삼바)")
    parser.add_argument("--refresh", type=int, default=60,
                        help="자동 갱신 주기 (초, 기본 60)")
    parser.add_argument("--capital", type=float, default=100_000_000.0)
    parser.add_argument("--conviction", type=float, default=5.0)
    parser.add_argument("--no-mcp", action="store_true")
    parser.add_argument("--once", action="store_true",
                        help="한 번만 출력하고 종료 (CI/스냅샷용)")
    args = parser.parse_args()

    symbols = args.symbols or DEFAULT_SYMBOLS

    # MCP 초기화
    mcp, use_mcp = (None, False) if args.no_mcp else _try_init_mcp()

    # 오케스트레이터 + 초기 리포트
    orch = LuxonOrchestrator(total_capital=args.capital)
    convictions = {s: args.conviction for s in symbols}

    report = None
    try:
        report = orch.run_workflow(symbols, base_convictions=convictions)
    except Exception as e:
        console.print(f"[yellow][warn] run_workflow 실패: {e}[/]")

    if args.once:
        layout = build_layout(report, mcp if use_mcp else None, symbols)
        console.print(layout)
        return

    # Live 대시보드 루프
    console.print("[dim]Luxon Dashboard 시작 — Ctrl+C 로 종료[/]")
    with Live(
        build_layout(report, mcp if use_mcp else None, symbols),
        console=console,
        refresh_per_second=1,
        screen=True,
    ) as live:
        tick = 0
        try:
            while True:
                time.sleep(1)
                tick += 1

                if tick % args.refresh == 0:
                    # 데이터 갱신
                    try:
                        report = orch.run_workflow(symbols, base_convictions=convictions)
                    except Exception:
                        pass
                    live.update(build_layout(report, mcp if use_mcp else None, symbols))

        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
