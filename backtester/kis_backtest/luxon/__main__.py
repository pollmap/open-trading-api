"""
Luxon Terminal CLI — 찬희 개인용 한 줄 호출.

사용:
    python -m kis_backtest.luxon 005930 000660 035420
    python -m kis_backtest.luxon 005930 --conviction 8 --capital 50000000

옵션 없으면 전 종목 기본 확신도 5.0 / 총 자본 1억 KRW. 카탈리스트 없음 —
카탈리스트 필요하면 scripts/luxon_run.py 를 사본으로 만들어서 직접 추가.
"""
from __future__ import annotations

import argparse
import sys

# Windows cp949 콘솔에서 한글/em-dash 출력 깨짐 방지
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from kis_backtest.luxon.orchestrator import LuxonOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kis_backtest.luxon",
        description="Luxon Terminal — 1인 헤지펀드 워크플로우 (찬희 개인용)",
    )
    parser.add_argument(
        "symbols", nargs="+", help="분석할 종목 코드 (예: 005930 000660)",
    )
    parser.add_argument(
        "--capital", type=float, default=100_000_000.0,
        help="총 투자 자본 KRW (기본 1억)",
    )
    parser.add_argument(
        "--conviction", type=float, default=5.0,
        help="모든 종목 기본 확신도 1-10 (기본 5.0)",
    )
    parser.add_argument(
        "--weekly", type=str, default=None, metavar="PATH",
        help="주간 레터를 지정 경로에 저장",
    )
    parser.add_argument(
        "--paper", action="store_true",
        help="모의투자 주문 실행 (KIS 모의 계좌)",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="실전 주문 실행 (위험! Y/n 승인 필요)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="주문 계획만 보기 (실제 주문 X)",
    )
    args = parser.parse_args()

    orch = LuxonOrchestrator(total_capital=args.capital)
    convictions = {s: args.conviction for s in args.symbols}

    if args.weekly:
        from pathlib import Path
        saved = orch.generate_weekly_letter(
            args.symbols, args.weekly, base_convictions=convictions,
        )
        print(f"주간 레터 저장: {saved}")
    elif args.paper or args.live:
        from kis_backtest.providers.kis.brokerage import KISBrokerageProvider
        mode = "prod" if args.live else "paper"
        brokerage = KISBrokerageProvider()
        report, exec_report = orch.run_and_execute(
            args.symbols,
            base_convictions=convictions,
            brokerage=brokerage,
            price_provider=brokerage,
            mode=mode,
            dry_run=args.dry_run,
        )
        print(report.summary())
        print()
        print(exec_report.summary() if hasattr(exec_report, "summary") else exec_report)
    else:
        report = orch.run_workflow(args.symbols, base_convictions=convictions)
        print(report.summary())


if __name__ == "__main__":
    main()
