#!/usr/bin/env python
"""LuxonTerminal 자동 실행 엔트리포인트 (v0.9 STEP 5).

Windows Task Scheduler / cron에서 매일 장 마감 후 실행.
CapitalLadder 단계에 따라 자동으로 주기와 리스크 게이트 조정.

Usage:
    # 1회 사이클 (Task Scheduler 권장)
    python scripts/luxon_terminal_run.py --max-cycles 1

    # 무한 루프 (systemd 서비스용)
    python scripts/luxon_terminal_run.py

    # 실전 모드 (paper_mode=False, KIS 실 주문)
    python scripts/luxon_terminal_run.py --live --max-cycles 1

    # CUFA digest 디렉토리 주입
    python scripts/luxon_terminal_run.py --cufa-digests ~/cufa_reports --max-cycles 1
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LuxonTerminal 자동 실행")
    p.add_argument("--symbols", nargs="+",
                   default=["005930", "000660", "035420"],
                   help="대상 종목 (기본: 삼성전자/SK하이닉스/NAVER)")
    p.add_argument("--capital", type=float, default=10_000_000,
                   help="총 자본 (기본 10,000,000원)")
    p.add_argument("--live", action="store_true",
                   help="실 주문 모드 (기본 paper)")
    p.add_argument("--kis-live", action="store_true",
                   help="KIS 실전 API 사용 (기본 모의)")
    p.add_argument("--max-cycles", type=int, default=None,
                   help="최대 사이클 수 (None=무한)")
    p.add_argument("--cufa-digests", type=str, default=None,
                   help="CUFA digest JSON 디렉토리")
    p.add_argument("--mcp-host", default="127.0.0.1:8100",
                   help="MCP 호스트")
    p.add_argument("--log-file", type=str, default=None,
                   help="로그 파일 경로")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

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
    logger = logging.getLogger("luxon_terminal_run")

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
        "LuxonTerminal 시작: symbols=%s capital=%.0f paper_mode=%s kis_paper=%s",
        config.symbols, config.capital, config.paper_mode, config.kis_paper,
    )

    terminal = LuxonTerminal(config)
    try:
        terminal.run_loop(max_cycles=args.max_cycles, stage_aware_interval=True)
    except KeyboardInterrupt:
        logger.info("사용자 중단")
        return 0
    except Exception:
        logger.exception("run_loop 치명적 실패")
        return 1

    logger.info("LuxonTerminal 정상 종료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
