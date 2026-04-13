"""페이퍼 트레이더 단위 테스트."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from luxon_paper_trader import extract_buys, run_from_ticket  # noqa: E402


def test_extract_buys_filters_actions():
    items = [
        {"ticker": "005930", "action": "BUY", "position_size_pct": 2.5},
        {"ticker": "000660", "action": "WATCH"},
        {"ticker": "035720", "action": "buy", "position_size_pct": 1.5},
    ]
    buys = extract_buys(items)
    assert len(buys) == 2
    tickers = [b["ticker"] for b in buys]
    assert "005930" in tickers and "035720" in tickers


def test_extract_buys_defaults_missing_size():
    items = [{"ticker": "005930", "action": "BUY"}]
    buys = extract_buys(items)
    assert buys[0]["position_size_pct"] == 1.0


def test_run_from_ticket_dry_run_saves(tmp_path, monkeypatch):
    import luxon_paper_trader as pt
    monkeypatch.setattr(pt, "FILL_DIR", tmp_path / "fills")

    ticket = tmp_path / "test.json"
    ticket.write_text(json.dumps([
        {"ticker": "005930", "action": "BUY", "position_size_pct": 2.0},
    ]), encoding="utf-8")

    result = pt.run_from_ticket(ticket, dry_run=True)
    assert result["dry_run"] is True
    assert len(result["executed"]) == 1
    assert (tmp_path / "fills" / "test.json").exists()


def test_run_from_ticket_no_buys():
    import luxon_paper_trader as pt
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as td:
        ticket = Path(td) / "t.json"
        ticket.write_text(json.dumps([{"ticker": "A", "action": "WATCH"}]), encoding="utf-8")

        # redirect FILL_DIR
        pt.FILL_DIR = Path(td) / "fills"
        result = pt.run_from_ticket(ticket, dry_run=True)
        assert "note" in result
