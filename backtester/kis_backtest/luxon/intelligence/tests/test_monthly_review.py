"""월간/분기 복기 테스트."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from luxon_monthly_review import extract_tickers  # noqa: E402
from luxon_quarterly_review import QUARTER_MONTHS, current_quarter_str  # noqa: E402


def test_extract_tickers_counts_occurrences():
    tickets = [
        {"data": [{"ticker": "005930", "action": "BUY"}, {"ticker": "000660"}]},
        {"data": {"ticker": "005930"}},
    ]
    c = extract_tickers(tickets)
    assert c["005930"] == 2
    assert c["000660"] == 1


def test_extract_tickers_handles_empty():
    assert extract_tickers([]) == Counter()


def test_quarter_months_mapping():
    assert QUARTER_MONTHS[1] == ["01", "02", "03"]
    assert QUARTER_MONTHS[4] == ["10", "11", "12"]


def test_current_quarter_format():
    q = current_quarter_str()
    assert "-Q" in q
    year, qn = q.split("-Q")
    assert int(qn) in (1, 2, 3, 4)
    assert len(year) == 4


def test_monthly_dry_run_empty(tmp_path, monkeypatch):
    import luxon_monthly_review as mr
    monkeypatch.setattr(mr, "TICKET_DIR", tmp_path / "empty")
    monkeypatch.setattr(mr, "FILL_DIR", tmp_path / "empty2")
    monkeypatch.setattr(mr, "REPORT_DIR", tmp_path / "reports")
    mr.generate_report("2026-04", dry_run=True)
