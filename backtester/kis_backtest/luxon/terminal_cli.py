"""`luxon-run` CLI 엔트리포인트 (pyproject.toml scripts).

설치 후:
    pip install luxon-terminal
    luxon-run --max-cycles 1
    luxon-run --live --cufa-digests ~/cufa

기존 `scripts/luxon_terminal_run.py` 와 동일 로직. 배포 패키지에 포함되는 공식 CLI.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="luxon-run",
        description="Luxon Terminal — AI quant trading loop",
    )
    p.add_argument("--symbols", nargs="+",
                   default=["005930", "000660", "035420"],
                   help="Ticker symbols (default: 3 KOSPI large-caps)")
    p.add_argument("--capital", type=float, default=10_000_000,
                   help="Total capital in base currency (default: 10M KRW)")
    p.add_argument("--live", action="store_true",
                   help="Enable live order execution (default: paper mode)")
    p.add_argument("--kis-live", action="store_true",
                   help="Use KIS production API (default: paper API)")
    p.add_argument("--max-cycles", type=int, default=None,
                   help="Max cycles to run (None = infinite loop)")
    p.add_argument("--cufa-digests", type=str, default=None,
                   help="CUFA digest directory (auto-injects convictions)")
    p.add_argument("--mcp-host", default="127.0.0.1:8100",
                   help="MCP server host (default: local)")
    p.add_argument("--log-file", type=str, default=None,
                   help="Log file path (default: stderr only)")
    p.add_argument("--version", action="store_true", help="Print version and exit")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    if args.version:
        from kis_backtest import __version__
        print(f"luxon-terminal {__version__}")
        return 0

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        log_path = Path(args.log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )
    logger = logging.getLogger("luxon-run")

    from kis_backtest.luxon.terminal import LuxonTerminal, TerminalConfig

    cufa_dir = Path(args.cufa_digests).expanduser() if args.cufa_digests else None
    config = TerminalConfig(
        symbols=args.symbols,
        capital=args.capital,
        mcp_host=args.mcp_host,
        paper_mode=not args.live,
        kis_paper=not args.kis_live,
        cufa_digests_dir=cufa_dir,
    )
    logger.info(
        "LuxonTerminal start: symbols=%s capital=%.0f paper=%s kis_paper=%s",
        config.symbols, config.capital, config.paper_mode, config.kis_paper,
    )

    terminal = LuxonTerminal(config)
    try:
        terminal.run_loop(max_cycles=args.max_cycles, stage_aware_interval=True)
    except KeyboardInterrupt:
        logger.info("interrupted")
        return 0
    except Exception:
        logger.exception("fatal error in run_loop")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
