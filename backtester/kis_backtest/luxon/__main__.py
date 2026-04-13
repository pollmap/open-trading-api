"""
Luxon Terminal CLI — 찬희 개인용 한 줄 호출.

사용:
    python -m kis_backtest.luxon 005930 000660 035420
    python -m kis_backtest.luxon 005930 --conviction 8 --capital 50000000
    python -m kis_backtest.luxon 005930 000660 --backtest --validate
    python -m kis_backtest.luxon 005930 --catalyst "005930:HBM4:INDUSTRY:2026-05-15:0.7:8.0"

MCP 자동연결: 가능하면 실 매크로/수익률/TA 신호 사용, 실패 시 로컬 모드.
TA 신호: RSI/MACD/Bollinger → 자동 카탈리스트 주입 (수동 --catalyst 불필요한 경우 多)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
from pathlib import Path

# Windows cp949 콘솔 인코딩 깨짐 방지
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# MCP 노이즈 억제
logging.getLogger("kis_backtest.portfolio.mcp_data_provider").setLevel(logging.ERROR)

from kis_backtest.luxon.orchestrator import LuxonOrchestrator


# ── MCP 초기화 ───────────────────────────────────────────────────────

def _try_init_mcp():
    """MCP 연결 시도. 실패 시 (None, False)."""
    try:
        from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
        mcp = MCPDataProvider()
        health = mcp.health_check_sync()
        if health.get("status") == "ok":
            return mcp, True
        print(f"[info] MCP health 실패, 로컬 모드")
        return None, False
    except Exception as exc:
        print(f"[info] MCP 없음 ({type(exc).__name__}), 로컬 모드")
        return None, False


def _fetch_returns(mcp, symbols: list[str], min_days: int = 120) -> dict[str, list[float]]:
    """MCP 실 일간 수익률 fetch. 실패 시 빈 dict."""
    if mcp is None:
        return {}
    result: dict[str, list[float]] = {}
    for sym in symbols:
        try:
            rets = mcp.get_stock_returns_sync(sym)
            if rets and len(rets) >= min_days:
                result[sym] = list(rets)
                print(f"[returns] {sym}: {len(rets)}일 실데이터")
            else:
                print(f"[returns] {sym}: {len(rets) if rets else 0}일 (부족, 합성 사용)")
        except Exception as exc:
            print(f"[returns] {sym} fetch 실패: {type(exc).__name__}")
    return result


def _inject_ta_signals(mcp, orch: LuxonOrchestrator, symbols: list[str]) -> int:
    """MCP TA 신호 → GothamGraph + CatalystTracker 자동 주입. 주입된 신호 수 반환."""
    if mcp is None:
        return 0
    try:
        from kis_backtest.luxon.graph.ingestors.ta_signal_ingestor import TASignalIngestor
        ingestor = TASignalIngestor(orch.graph, orch.tracker)
        result = ingestor.ingest_sync(mcp, symbols)
        total = sum(len(v) for v in result.values())
        if total:
            print(f"[TA] {total}개 기술적 신호 자동 주입")
            for sym, sigs in result.items():
                for s in sigs:
                    print(f"  · {sym}: {s.name} ({'+' if s.impact > 0 else ''}{s.impact:.0f})")
        return total
    except Exception as exc:
        print(f"[info] TA 신호 주입 실패: {exc}")
        return 0


# ── CLI ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kis_backtest.luxon",
        description="Luxon Terminal — 1인 헤지펀드 워크플로우 (찬희 개인용)",
    )
    parser.add_argument("symbols", nargs="+", help="종목 코드 (예: 005930 000660)")
    parser.add_argument("--capital", type=float, default=100_000_000.0,
                        help="총 투자 자본 KRW (기본 1억)")
    parser.add_argument("--conviction", type=float, default=5.0,
                        help="기본 확신도 1-10 (기본 5.0)")
    parser.add_argument("--no-mcp", action="store_true",
                        help="MCP 연결 시도 없이 로컬 모드 강제")
    parser.add_argument("--no-ta", action="store_true",
                        help="TA 신호 자동 주입 비활성화")
    parser.add_argument(
        "--catalyst", action="append", default=[],
        metavar="TICKER:NAME:TYPE:DATE:PROB:IMPACT",
        help=(
            "수동 카탈리스트 추가 (반복 가능). "
            "예: --catalyst 005930:HBM4:INDUSTRY:2026-05-15:0.7:8.0 "
            "TYPE: EARNINGS|INDUSTRY|MACRO|TECHNICAL"
        ),
    )
    parser.add_argument("--weekly", type=str, default=None, metavar="PATH",
                        help="주간 레터를 지정 경로에 저장")
    parser.add_argument("--paper", action="store_true",
                        help="모의투자 주문 (KIS paper)")
    parser.add_argument("--live", action="store_true",
                        help="실전 주문 (위험!)")
    parser.add_argument("--dry-run", action="store_true",
                        help="주문 계획만 출력 (실제 주문 X)")
    parser.add_argument("--backtest", action="store_true",
                        help="리스크 파이프라인 검증")
    parser.add_argument("--validate", action="store_true",
                        help="Walk-Forward OOS 검증 (5-fold)")
    args = parser.parse_args()

    # MCP 초기화
    mcp, use_mcp = (None, False) if args.no_mcp else _try_init_mcp()
    if use_mcp:
        print(f"[info] MCP 연결됨")

    orch = LuxonOrchestrator(total_capital=args.capital)
    convictions = {s: args.conviction for s in args.symbols}

    # 매크로 레짐 갱신 (MCP 있을 때만)
    if use_mcp:
        try:
            asyncio.run(orch.refresh_macro())
            print("[info] 매크로 레짐 갱신 완료")
        except Exception as e:
            print(f"[info] 매크로 갱신 실패: {type(e).__name__}")

    # 수동 카탈리스트 주입
    for raw in args.catalyst:
        parts = raw.split(":")
        if len(parts) != 6:
            print(f"[warn] catalyst 형식 오류: {raw}")
            print("       형식: TICKER:NAME:TYPE:DATE:PROB:IMPACT")
            continue
        ticker, name, ctype, dt, prob, impact = parts
        try:
            orch.add_catalyst(
                symbol=ticker, name=name, catalyst_type=ctype.lower(),
                expected_date=dt, probability=float(prob), impact=float(impact),
            )
            print(f"[catalyst] {ticker}: {name} ({ctype}) P={prob} I={impact}")
        except Exception as e:
            print(f"[warn] catalyst 추가 실패 {ticker}: {e}")

    # TA 신호 자동 주입 (MCP 있고 --no-ta 아닐 때)
    if use_mcp and not args.no_ta:
        _inject_ta_signals(mcp, orch, args.symbols)

    # ── 모드 분기 ─────────────────────────────────────────────────────

    if args.backtest or args.validate:
        report = orch.run_workflow(args.symbols, base_convictions=convictions)
        print(report.summary())

        # 실 수익률 우선, 없으면 합성
        returns_dict = _fetch_returns(mcp, args.symbols)
        if not returns_dict:
            print("[info] 합성 수익률 사용 (MCP 실데이터 없음)")
            random.seed(42)
            returns_dict = {
                s: [random.gauss(0.0003, 0.015) for _ in range(300)]
                for s in args.symbols
            }
        else:
            print(f"[info] 실데이터 {len(returns_dict)}종목 검증")

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
                print(
                    f"  fold {row['fold']}: "
                    f"IS={row['is_sharpe']} → OOS={row['oos_sharpe']} ({row['pass']})"
                )

    elif args.weekly:
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
