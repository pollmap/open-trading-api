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
        "--catalyst", action="append", default=[], metavar="TICKER:NAME:TYPE:DATE:PROB:IMPACT",
        help=(
            "카탈리스트 추가 (반복 가능). "
            "예: --catalyst 005930:HBM4양산:INDUSTRY:2026-05-15:0.7:8.0 "
            "DATE: YYYY-MM-DD  TYPE: EARNINGS|INDUSTRY|MACRO|REGULATORY|TECHNICAL"
        ),
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
    parser.add_argument(
        "--backtest", action="store_true",
        help="리스크 파이프라인 통과 (vol 타겟팅, DD 체크, Kelly)",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Walk-Forward OOS 검증 (5-fold)",
    )
    args = parser.parse_args()

    orch = LuxonOrchestrator(total_capital=args.capital)
    convictions = {s: args.conviction for s in args.symbols}

    # --catalyst TICKER:NAME:TYPE:PROB:IMPACT 파싱 + 주입
    if args.catalyst:
        for raw in args.catalyst:
            parts = raw.split(":")
            if len(parts) != 6:
                print(f"[warn] catalyst 형식 오류 (건너뜀): {raw}")
                print("       형식: TICKER:NAME:TYPE:DATE:PROB:IMPACT")
                print("       예시: 005930:HBM4양산:INDUSTRY:2026-05-15:0.7:8.0")
                continue
            ticker, name, ctype, date, prob, impact = parts
            try:
                orch.add_catalyst(
                    symbol=ticker,
                    name=name,
                    catalyst_type=ctype.lower(),  # INDUSTRY → industry
                    expected_date=date,
                    probability=float(prob),
                    impact=float(impact),
                )
                print(f"[catalyst] {ticker}: {name} ({ctype}) 예정={date} P={prob} I={impact}")
            except Exception as e:
                print(f"[warn] catalyst 추가 실패 {ticker}: {e}")

    if args.backtest or args.validate:
        import random
        report = orch.run_workflow(args.symbols, base_convictions=convictions)
        print(report.summary())
        # 일간 수익률: MCP 없으면 합성 데이터로 검증 구조만 확인
        random.seed(42)
        returns_dict = {
            s: [random.gauss(0.0003, 0.015) for _ in range(300)]
            for s in args.symbols
        }
        if args.backtest:
            print("\n## Risk Pipeline")
            pr = orch.backtest(report, returns_dict=returns_dict)
            print(f"  risk_passed: {pr.risk_passed}")
            print(f"  kelly: {pr.kelly_allocation:.2f}")
            for d in pr.risk_details:
                print(f"  · {d}")
        if args.validate:
            print("\n## Walk-Forward OOS Validation")
            wf = orch.validate(report, returns_dict=returns_dict)
            print(f"  verdict: {wf.verdict}")
            print(f"  mean_oos_sharpe: {wf.oos_mean_sharpe:.3f}")
            print(f"  win_rate: {wf.win_rate:.0%}")
            for row in wf.summary_table():
                print(f"  fold {row['fold']}: IS={row['is_sharpe']} → OOS={row['oos_sharpe']} ({row['pass']})")
    elif args.weekly:
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
