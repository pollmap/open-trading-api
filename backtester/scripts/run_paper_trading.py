#!/usr/bin/env python
"""페이퍼 트레이딩 통합 엔트리포인트

QuantPipeline -> RiskGateway -> LiveOrderExecutor -> FillTracker -> LiveMonitor

Usage:
    python scripts/run_paper_trading.py                    # 시그널 기반 실행
    python scripts/run_paper_trading.py --dry-run          # 주문 계획만
    python scripts/run_paper_trading.py --monitor-only     # 모니터링만
    python scripts/run_paper_trading.py --review           # 실행 후 복기
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# 프로젝트 루트를 path에 추가
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import kis_auth as ka

from kis_backtest.core.pipeline import PipelineResult, QuantPipeline
from kis_backtest.execution.alerts import AlertSystem
from kis_backtest.execution.capital_ladder import CapitalLadder, LadderConfig
from kis_backtest.execution.fill_tracker import FillTracker
from kis_backtest.execution.kill_switch import KillSwitch
from kis_backtest.execution.live_monitor import LiveMonitor
from kis_backtest.execution.order_executor import LiveOrderExecutor
from kis_backtest.execution.review_scheduler import ReviewScheduler
from kis_backtest.execution.risk_gateway import RiskGateway
from kis_backtest.execution.vault_writer import VaultWriter
from kis_backtest.portfolio.review_engine import ReviewEngine
from kis_backtest.providers.kis.auth import KISAuth
from kis_backtest.providers.kis.brokerage import KISBrokerageProvider
from kis_backtest.providers.kis.data import KISDataProvider
from kis_backtest.providers.kis.websocket import KISWebSocket

logger = logging.getLogger("run_paper_trading")

# 글로벌 정리용 참조
_ws_client: Optional[KISWebSocket] = None
_monitor: Optional[LiveMonitor] = None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="페이퍼 트레이딩 통합 엔트리포인트",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="주문 계획만 생성 (실행 안 함)",
    )
    parser.add_argument(
        "--monitor-only",
        action="store_true",
        help="모니터링만 실행 (WebSocket 실시간 추적)",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="실행 후 일일 복기 수행",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=5_000_000,
        help="초기 투자금 (기본 5,000,000원)",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="대상 종목 코드 (예: 005930 000660)",
    )
    parser.add_argument(
        "--ladder",
        action="store_true",
        help="Capital Ladder 사용 (점진적 자본 배포)",
    )
    return parser.parse_args()


class _KISPriceAdapter:
    """KISDataProvider -> PriceProvider 어댑터

    LiveOrderExecutor가 요구하는 PriceProvider Protocol을 충족시킨다.
    KISDataProvider.get_quote()를 get_current_price()로 매핑.
    """

    def __init__(self, data_provider: KISDataProvider) -> None:
        self._data = data_provider

    def get_current_price(self, symbol: str) -> float:
        quote = self._data.get_quote(symbol)
        return float(quote.last_price)


def _load_latest_pipeline_result() -> Optional[PipelineResult]:
    """최신 PipelineResult 로드 시도

    results/ 디렉토리에서 최신 JSON을 읽어 PipelineResult를 복원한다.
    실패 시 None 반환 (더미로 대체됨).
    """
    results_dir = _ROOT / "results"
    if not results_dir.exists():
        return None

    json_files = sorted(results_dir.glob("*.json"), reverse=True)
    if not json_files:
        return None

    import json

    try:
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        return PipelineResult(
            order=None,
            risk_passed=data.get("risk_passed", False),
            risk_details=data.get("risk_details", []),
            vol_adjustments={},
            turb_index=data.get("turb_index", 0.0),
            dd_state=None,
            estimated_annual_cost=data.get("annual_cost", 0.0),
            kelly_allocation=data.get("kelly_allocation", 0.0),
        )
    except Exception as exc:
        logger.warning("PipelineResult 로드 실패: %s -- 더미 사용", exc)
        return None


def _make_dummy_pipeline_result() -> PipelineResult:
    """테스트용 더미 PipelineResult 생성"""
    return PipelineResult(
        order=None,
        risk_passed=True,
        risk_details=["DUMMY: 실제 파이프라인 결과 없음"],
        vol_adjustments={},
        turb_index=0.0,
        dd_state=None,
        estimated_annual_cost=0.0,
        kelly_allocation=1.0,
    )


def _handle_signal(signum: int, _frame: object) -> None:
    """Ctrl+C 시그널 핸들러 -- WebSocket 정리 후 최종 상태 출력"""
    sig_name = signal.Signals(signum).name
    print(f"\n[{sig_name}] 종료 시그널 수신 -- 정리 중...")

    if _monitor is not None:
        state = _monitor.state
        print(state.summary())

    if _ws_client is not None:
        try:
            _ws_client.stop()
            logger.info("WebSocket 연결 종료")
        except Exception:
            pass

    print("\n페이퍼 트레이딩 종료.")
    sys.exit(0)


def main() -> int:
    global _ws_client, _monitor

    args = _parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 시그널 핸들러 등록
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    print("=" * 60)
    print("  Luxon Quant -- 페이퍼 트레이딩")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    mode_str = (
        "DRY-RUN" if args.dry_run
        else "MONITOR-ONLY" if args.monitor_only
        else "LIVE (paper)"
    )
    print(f"  모드: {mode_str}")
    print(f"  초기 자본: {args.capital:,.0f}원")
    if args.symbols:
        print(f"  대상 종목: {', '.join(args.symbols)}")
    if args.ladder:
        print(f"  래더 모드: ON (점진적 자본 배포)")
    print("=" * 60)

    try:
        # ── a) KIS 인증 ──────────────────────────────────────
        logger.info("KIS 인증 시작 (paper trading)...")
        ka.auth(svr="vps")
        auth = KISAuth.from_env(mode="paper")
        logger.info("KIS 인증 완료")

        # ── b) 프로바이더 생성 ────────────────────────────────
        brokerage = KISBrokerageProvider.from_auth(auth)
        data_provider = KISDataProvider(auth)
        price_provider = _KISPriceAdapter(data_provider)

        kill_switch = KillSwitch()
        if kill_switch.is_active:
            print("\n[KILL SWITCH] 활성 상태 -- 모든 주문 차단됨")
            print("  해제: ~/kis_kill_switch.lock 파일 삭제")
            return 1

        # Capital Ladder 연동
        ladder = None
        effective_capital = args.capital
        if args.ladder:
            ladder_state_file = str(_ROOT / "data" / "ladder_state.json")
            ladder = CapitalLadder(LadderConfig(
                total_capital=args.capital,
                state_file=ladder_state_file,
            ))
            effective_capital = ladder.get_pipeline_capital()
            status = ladder.status()
            print(f"\n[래더] 단계: {status.stage_label}")
            print(f"  배포 자본: {status.deployed_capital:,.0f}원 ({status.capital_pct})")
            print(f"  현재 Sharpe: {status.current_sharpe:.3f}")
            print(f"  현재 DD: {status.current_dd}")
            if status.can_promote:
                print("  승격 가능!")
            elif status.promote_blockers:
                print(f"  승격 차단: {'; '.join(status.promote_blockers)}")

        # ── monitor-only 모드 ────────────────────────────────
        if args.monitor_only:
            return _run_monitor_only(auth, brokerage, kill_switch)

        # ── c) PipelineResult 로드 ───────────────────────────
        print("\n[1/4] PipelineResult 로드...")
        pipeline_result = _load_latest_pipeline_result()
        if pipeline_result is None:
            pipeline_result = _make_dummy_pipeline_result()
            print("  최신 결과 없음 -- 더미 사용")
        else:
            status = "PASS" if pipeline_result.risk_passed else "FAIL"
            print(f"  리스크 판정: {status}")
            print(f"  터뷸런스:   {pipeline_result.turb_index:.2f}")

        # ── d) RiskGateway 체크 ──────────────────────────────
        print("\n[2/4] RiskGateway 체크...")
        gateway = RiskGateway(
            mode="paper",
            kill_switch=kill_switch,
            require_market_hours=False,  # 페이퍼 모드: 시간 체크 완화
        )

        balance = brokerage.get_balance()

        # order가 없으면 게이트웨이 체크 스킵
        if pipeline_result.order is None:
            print("  PortfolioOrder 없음 -- 게이트웨이 체크 스킵")
            print("  (QuantPipeline 실행 후 다시 시도하세요)")

            if args.review:
                return _run_review(brokerage, args.capital)

            return 0

        # planned_trades 계산을 위해 executor 먼저 생성
        executor = LiveOrderExecutor(
            brokerage=brokerage,
            price_provider=price_provider,
        )
        plan_report = executor.plan(pipeline_result.order)

        decision = gateway.check(
            planned_trades=plan_report.planned,
            balance=balance,
            pipeline_result=pipeline_result,
        )
        print(decision.summary())

        if not decision.approved:
            print("\n리스크 게이트 차단 -- 주문 실행 불가")

            if args.review:
                return _run_review(brokerage, args.capital)

            return 1

        # ── e) 주문 실행 ─────────────────────────────────────
        print(f"\n[3/4] 주문 {'계획' if args.dry_run else '실행'}...")

        if args.dry_run:
            report = plan_report
        else:
            report = executor.execute(pipeline_result.order)

        # ── f) ExecutionReport 출력 ──────────────────────────
        print(f"\n[4/4] 실행 결과")
        print(f"  계획:   {len(report.planned)}건")
        if report.executed:
            print(f"  체결:   {len(report.executed)}건")
        if report.rejected:
            print(f"  거절:   {len(report.rejected)}건")
        if report.skipped:
            print(f"  스킵:   {len(report.skipped)}건")
        if report.total_commission:
            print(f"  수수료: {report.total_commission:,.0f}원")

        if report.planned:
            print("\n  --- 주문 상세 ---")
            for trade in report.planned:
                print(f"  {trade.summary_line()}")

        # ── h) 복기 ──────────────────────────────────────────
        if args.review:
            return _run_review(brokerage, args.capital)

        print("\n" + "=" * 60)
        print("  페이퍼 트레이딩 완료!")
        print("=" * 60)
        return 0

    except Exception:
        logger.exception("페이퍼 트레이딩 실행 중 오류 발생")
        print("\n[오류] 실행 실패 -- 로그를 확인하세요.")
        return 1


def _run_monitor_only(
    auth: KISAuth,
    brokerage: KISBrokerageProvider,
    kill_switch: KillSwitch,
) -> int:
    """모니터링 전용 모드 -- WebSocket 실시간 추적

    Ctrl+C로 종료 시 최종 상태를 출력한다.
    """
    global _ws_client, _monitor

    print("\n[모니터링 모드] WebSocket 실시간 추적 시작")
    print("  종료: Ctrl+C\n")

    alerts = AlertSystem()
    _monitor = LiveMonitor(
        brokerage=brokerage,
        kill_switch=kill_switch,
        alert_system=alerts,
    )

    # REST로 초기 상태 로드
    state = _monitor.initialize()
    print(state.summary())

    if not state.positions:
        print("\n  보유 종목 없음 -- WebSocket 구독 대상 없음")
        return 0

    # WebSocket 생성 + 콜백 등록
    _ws_client = KISWebSocket.from_auth(auth)
    _monitor.setup_websocket(_ws_client)

    print(f"\n  {len(state.positions)}개 종목 실시간 추적 중...")
    print("  Ctrl+C로 종료\n")

    try:
        _ws_client.start()  # 블로킹 -- on_price/on_fill 콜백 호출
    except KeyboardInterrupt:
        pass

    # 최종 상태 출력
    if _monitor is not None:
        final_state = _monitor.state
        print("\n" + final_state.summary())

    return 0


def _run_review(brokerage: KISBrokerageProvider, capital: float) -> int:
    """일일 복기 실행 후 종료"""
    print("\n[복기] 일일 스냅샷 실행...")

    review_engine = ReviewEngine(
        initial_capital=capital,
        risk_free_rate=0.035,
    )
    vault_writer = VaultWriter()
    scheduler = ReviewScheduler(
        brokerage=brokerage,
        review_engine=review_engine,
        vault_writer=vault_writer,
        initial_capital=capital,
    )

    snapshot = scheduler.run_daily()
    print(f"  날짜:    {snapshot.date}")
    print(f"  자산:    {snapshot.equity:,.0f}원")
    print(f"  DD:      {snapshot.dd_from_peak * 100:.2f}%")
    if snapshot.file_path:
        print(f"  저장:    {snapshot.file_path}")

    print("\n" + "=" * 60)
    print("  페이퍼 트레이딩 + 복기 완료!")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
