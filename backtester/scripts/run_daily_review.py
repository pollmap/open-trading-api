#!/usr/bin/env python
"""일일/주간 자동 복기 스크립트

Windows Task Scheduler에서 실행:
    - 매일 16:00 KST: 일일 스냅샷
    - 매금 16:30 KST: 주간 복기

Usage:
    python scripts/run_daily_review.py              # 일일 스냅샷
    python scripts/run_daily_review.py --weekly     # 주간 복기 포함
    python scripts/run_daily_review.py --force      # 시간 체크 무시
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 path에 추가
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import kis_auth as ka

from kis_backtest.execution.review_scheduler import ReviewScheduler
from kis_backtest.execution.vault_writer import VaultWriter
from kis_backtest.portfolio.review_engine import ReviewEngine
from kis_backtest.providers.kis.auth import KISAuth
from kis_backtest.providers.kis.brokerage import KISBrokerageProvider


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="일일/주간 자동 복기 스크립트",
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="주간 복기도 함께 실행",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="시간/요일 체크를 무시하고 강제 실행",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=5_000_000,
        help="초기 투자금 (기본 5,000,000원)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("run_daily_review")

    print("=" * 60)
    print("  Luxon Quant -- 자동 복기")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  모드: {'강제 실행' if args.force else '스케줄 기반'}")
    print(f"  초기 자본: {args.capital:,.0f}원")
    print("=" * 60)

    try:
        # 1. KIS 인증 (모의투자)
        logger.info("KIS 인증 시작 (paper trading)...")
        ka.auth(svr="vps")
        auth = KISAuth.from_env(mode="paper")
        logger.info("KIS 인증 완료")

        # 2. 브로커리지 프로바이더 생성
        brokerage = KISBrokerageProvider.from_auth(auth)

        # 3. 복기 엔진 + Vault Writer 생성
        review_engine = ReviewEngine(
            initial_capital=args.capital,
            risk_free_rate=0.035,
        )
        vault_writer = VaultWriter()

        # 4. 스케줄러 생성
        scheduler = ReviewScheduler(
            brokerage=brokerage,
            review_engine=review_engine,
            vault_writer=vault_writer,
            initial_capital=args.capital,
        )

        # 5. 일일 스냅샷 실행
        if args.force or scheduler.should_run_daily():
            print("\n[일일 스냅샷] 실행 중...")
            snapshot = scheduler.run_daily()

            print(f"  날짜:     {snapshot.date}")
            print(f"  자산:     {snapshot.equity:,.0f}원")
            print(f"  현금:     {snapshot.cash:,.0f}원")
            print(f"  종목 수:  {snapshot.positions_count}개")
            print(f"  DD:       {snapshot.dd_from_peak * 100:.2f}%")
            if snapshot.file_path:
                print(f"  저장:     {snapshot.file_path}")
        else:
            print("\n[일일 스냅샷] 스킵 (실행 조건 미충족, --force로 강제 가능)")

        # 6. 주간 복기 (금요일 또는 --weekly 플래그)
        is_friday = datetime.now().weekday() == 4
        run_weekly = args.weekly or is_friday

        if run_weekly and (args.force or scheduler.should_run_weekly()):
            print("\n[주간 복기] 실행 중...")
            report = scheduler.run_weekly()
            print(report.summary())
        elif run_weekly:
            print("\n[주간 복기] 스킵 (이미 실행됨 또는 조건 미충족)")
        else:
            print("\n[주간 복기] 스킵 (금요일 아님, --weekly로 강제 가능)")

        # 7. 완료
        print("\n" + "=" * 60)
        print("  복기 완료!")
        print("=" * 60)
        return 0

    except Exception:
        logger.exception("복기 실행 중 오류 발생")
        print("\n[오류] 복기 실행 실패 -- 로그를 확인하세요.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
