"""페이퍼 트레이딩 자동 루프.

매시간 loop에서 호출 또는 수동:
    python scripts/luxon_paper_trader.py --ticket=tickets/hourly/최근.json

티켓 JSON → BUY 추출 → KIS paper 계좌 주문. 실패 무해화(dry_run 기본).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

BACKTESTER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKTESTER))

FILL_DIR = BACKTESTER / "fills" / "paper"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("luxon.paper")


def load_ticket(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else [data]
    return [it for it in items if isinstance(it, dict)]


def extract_buys(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """action=BUY 종목만 추출. 없으면 빈 리스트."""
    out = []
    for it in items:
        action = str(it.get("action", "")).upper()
        if action == "BUY" and "ticker" in it:
            out.append({
                "ticker": str(it["ticker"]),
                "rationale": it.get("rationale", ""),
                "position_size_pct": float(it.get("position_size_pct", 1.0)),
            })
    return out


def run_from_ticket(
    ticket_path: Path,
    *,
    dry_run: bool = True,
    mode: str = "paper",
) -> dict[str, Any]:
    """티켓 1개 → 페이퍼 주문. 반환: fill summary."""
    items = load_ticket(ticket_path)
    buys = extract_buys(items)

    run_id = ticket_path.stem
    result: dict[str, Any] = {
        "run_id": run_id,
        "ticket": str(ticket_path),
        "mode": mode,
        "dry_run": dry_run,
        "buys": buys,
        "executed": [],
        "skipped": [],
        "timestamp": datetime.now().isoformat(),
    }

    if not buys:
        result["note"] = "BUY 시그널 없음"
        _save(result, run_id)
        return result

    # dry_run 기본 — 실거래 전환은 mode=prod + env KIS_LIVE_APPROVE=1 필요
    if dry_run or mode != "prod":
        for b in buys:
            result["executed"].append({
                "ticker": b["ticker"],
                "status": "dry_planned",
                "size_pct": b["position_size_pct"],
            })
        logger.info(f"dry_run: {len(buys)}건 계획 (실주문 없음)")
        _save(result, run_id)
        return result

    # 실주문 경로
    if os.getenv("KIS_LIVE_APPROVE") != "1":
        result["skipped"].append("KIS_LIVE_APPROVE 미설정 — 실주문 차단")
        _save(result, run_id)
        return result

    try:
        from kis_backtest.providers.kis.brokerage import KISBrokerageProvider  # type: ignore
        from kis_backtest.execution.order_executor import LiveOrderExecutor
        # 실주문은 추가 인프라 필요 — 현재는 placeholder
        logger.warning("실주문 경로는 KIS 인증 필요 — placeholder")
        result["skipped"].append("live-order placeholder")
    except Exception as exc:  # noqa: BLE001
        result["skipped"].append(f"import 실패: {exc}")

    _save(result, run_id)
    return result


def _save(result: dict, run_id: str) -> None:
    FILL_DIR.mkdir(parents=True, exist_ok=True)
    out = FILL_DIR / f"{run_id}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"fill 저장: {out}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticket", required=True, help="티켓 JSON 경로")
    parser.add_argument("--mode", default="paper", choices=["paper", "prod"])
    parser.add_argument("--live", action="store_true", help="dry_run 해제")
    args = parser.parse_args()

    path = Path(args.ticket)
    if not path.exists():
        logger.error(f"티켓 없음: {path}")
        return 1

    run_from_ticket(path, dry_run=not args.live, mode=args.mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
