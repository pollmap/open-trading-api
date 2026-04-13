"""월간 복기 — tickets/hourly/ + fills/paper/ 집계 → reports/monthly/.

매월 말일 18:00 자동 실행. 수동:
    python scripts/luxon_monthly_review.py --month=2026-04
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

BACKTESTER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKTESTER))

TICKET_DIR = BACKTESTER / "tickets" / "hourly"
FILL_DIR = BACKTESTER / "fills" / "paper"
REPORT_DIR = BACKTESTER / "reports" / "monthly"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("luxon.monthly")


def collect_tickets(month: str) -> list[dict]:
    """YYYY-MM 형식 → 해당 월 티켓 전수."""
    yyyymm = month.replace("-", "")
    out = []
    for p in sorted(TICKET_DIR.glob(f"{yyyymm}*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append({"file": p.name, "data": data})
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return out


def collect_fills(month: str) -> list[dict]:
    yyyymm = month.replace("-", "")
    out = []
    if not FILL_DIR.exists():
        return out
    for p in sorted(FILL_DIR.glob(f"{yyyymm}*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append({"file": p.name, "data": data})
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return out


def extract_tickers(tickets: list[dict]) -> Counter:
    counter: Counter = Counter()
    for t in tickets:
        d = t["data"]
        items = d if isinstance(d, list) else [d]
        for it in items:
            if isinstance(it, dict) and "ticker" in it:
                counter[str(it["ticker"])] += 1
    return counter


def generate_report(month: str, *, dry_run: bool = False) -> Path:
    tickets = collect_tickets(month)
    fills = collect_fills(month)
    top_tickers = extract_tickers(tickets)

    lines = [
        f"# Luxon 월간 복기 — {month}",
        "",
        f"- 생성: {datetime.now().isoformat()}",
        f"- 티켓: {len(tickets)}건",
        f"- 페이퍼 체결: {len(fills)}건",
        "",
        "## 관심 종목 Top 10",
        "",
    ]
    for sym, cnt in top_tickers.most_common(10):
        lines.append(f"- {sym}: {cnt}회 언급")

    lines.extend(["", "## Simons 12원칙 자가평가", ""])
    try:
        from kis_backtest.luxon.intelligence.tasks.simons import evaluate_trade_ticket
        if tickets:
            sample = tickets[0]["data"]
            sample_one = sample[0] if isinstance(sample, list) and sample else sample
            if isinstance(sample_one, dict):
                ev = evaluate_trade_ticket(sample_one)
                lines.append(f"샘플 평가: {ev}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"Simons 평가 스킵: {exc}")

    lines.extend(["", "## Kronos 21일 예측 (상위 종목)", ""])
    for sym, _ in list(top_tickers.most_common(3)):
        lines.append(f"- {sym}: (Kronos 모델 로드 필요 — 수동 실행 권장)")

    report_text = "\n".join(lines) + "\n"

    if dry_run:
        logger.info("[DRY RUN] 미저장")
        logger.info(report_text[:500])
        return Path("/dev/null")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"{month}.md"
    out.write_text(report_text, encoding="utf-8")
    logger.info(f"저장: {out}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default=datetime.now().strftime("%Y-%m"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    generate_report(args.month, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
