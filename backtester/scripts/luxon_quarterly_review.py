"""분기 복기 — 3개월 누적 티켓/체결/Lean 결과 종합.

매 분기 말일 18:30 자동 실행. 수동:
    python scripts/luxon_quarterly_review.py --quarter=2026-Q1
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

BACKTESTER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKTESTER))

from luxon_monthly_review import collect_fills, collect_tickets, extract_tickers  # noqa: E402

REPORT_DIR = BACKTESTER / "reports" / "quarterly"
LEAN_DIR = BACKTESTER / "reports" / "lean"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("luxon.quarterly")


QUARTER_MONTHS = {
    1: ["01", "02", "03"],
    2: ["04", "05", "06"],
    3: ["07", "08", "09"],
    4: ["10", "11", "12"],
}


def current_quarter_str() -> str:
    now = datetime.now()
    q = (now.month - 1) // 3 + 1
    return f"{now.year}-Q{q}"


def generate_report(quarter: str, *, dry_run: bool = False) -> Path:
    year_str, q_str = quarter.split("-Q")
    q = int(q_str)
    months = [f"{year_str}-{m}" for m in QUARTER_MONTHS[q]]

    all_tickets: list[dict] = []
    all_fills: list[dict] = []
    for m in months:
        all_tickets.extend(collect_tickets(m))
        all_fills.extend(collect_fills(m))

    top = extract_tickers(all_tickets)
    lean_files = sorted(LEAN_DIR.glob("*_summary.json")) if LEAN_DIR.exists() else []

    lines = [
        f"# Luxon 분기 복기 — {quarter}",
        "",
        f"- 생성: {datetime.now().isoformat()}",
        f"- 기간: {months[0]} ~ {months[-1]}",
        f"- 총 티켓: {len(all_tickets)}건",
        f"- 페이퍼 체결: {len(all_fills)}건",
        f"- Lean 백테스트: {len(lean_files)}건",
        "",
        "## 분기 관심 종목 Top 15",
        "",
    ]
    for sym, cnt in top.most_common(15):
        lines.append(f"- {sym}: {cnt}회")

    lines.extend([
        "",
        "## 시그널 Hit/Miss 통계 (Simons 5번 원칙: 실패=데이터)",
        "",
        "| 지표 | 값 |",
        "|---|---|",
        f"| 총 시그널 건수 | {len(all_tickets)} |",
        f"| 체결 전환률 | {len(all_fills) / max(len(all_tickets), 1) * 100:.1f}% |",
        f"| 고유 종목 | {len(top)} |",
        "",
        "## 다음 분기 관찰 대상",
        "",
    ])
    for sym, _ in list(top.most_common(5)):
        lines.append(f"- {sym}")

    report_text = "\n".join(lines) + "\n"

    if dry_run:
        logger.info("[DRY RUN]")
        logger.info(report_text[:500])
        return Path("/dev/null")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"{quarter}.md"
    out.write_text(report_text, encoding="utf-8")
    logger.info(f"저장: {out}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quarter", default=current_quarter_str())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    generate_report(args.quarter, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
