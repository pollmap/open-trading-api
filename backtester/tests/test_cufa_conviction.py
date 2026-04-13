"""CUFA digest → conviction 자동 계산 테스트 (STEP 3 / v0.7).

선순환 입구: CUFA 보고서의 IP + Kill Condition 트리거 여부를 conviction(1-10)으로
변환하여 FeedbackAdapter에 주입.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kis_backtest.luxon.integration.cufa_conviction import (
    CufaConviction,
    build_convictions_from_digests,
    compute_conviction_from_digest,
    load_cufa_digests_from_dir,
)


# ── compute_conviction_from_digest ─────────────────────────────────


def test_compute_conviction_base_no_ip_no_kill():
    """IP 0, Kill 0 → base 5.0 유지."""
    digest = {"ticker": "005930", "investment_points": [], "kill_conditions": []}
    result = compute_conviction_from_digest(digest)
    assert result is not None
    assert result.symbol == "005930"
    assert result.conviction == 5.0
    assert result.ip_count == 0
    assert result.triggered_kill_count == 0


def test_compute_conviction_with_ips():
    """IP 3개 → 5 + 3 = 8.0."""
    digest = {
        "ticker": "005930",
        "investment_points": [
            {"id": 1, "type": "growth"},
            {"id": 2, "type": "value"},
            {"id": 3, "type": "momentum"},
        ],
        "kill_conditions": [],
    }
    result = compute_conviction_from_digest(digest)
    assert result.conviction == 8.0
    assert result.ip_count == 3


def test_compute_conviction_ip_bonus_capped_at_4():
    """IP 6개여도 +4까지만 반영 (diminishing return) → 5 + 4 = 9.0."""
    digest = {
        "ticker": "000660",
        "investment_points": [{"id": i, "type": "growth"} for i in range(6)],
        "kill_conditions": [],
    }
    result = compute_conviction_from_digest(digest)
    assert result.conviction == 9.0
    assert result.ip_count == 6


def test_compute_conviction_with_triggered_kill():
    """IP 2 + triggered kill 1 → 5 + 2 - 2 = 5.0."""
    digest = {
        "ticker": "005930",
        "investment_points": [{"id": 1}, {"id": 2}],
        "kill_conditions": [
            {
                "condition": "OPM < 10%",
                "metric": "opm",
                "trigger": 0.10,
                "current": 0.08,  # triggered (current < trigger)
            },
        ],
    }
    result = compute_conviction_from_digest(digest)
    assert result.triggered_kill_count == 1
    assert result.conviction == 5.0


def test_compute_conviction_clamped_to_min():
    """IP 0 + triggered kill 5 → clamp 1.0."""
    kcs = [
        {"condition": f"opm<{i}", "metric": "opm", "trigger": 0.5, "current": 0.1}
        for i in range(5)
    ]
    digest = {"ticker": "005930", "investment_points": [], "kill_conditions": kcs}
    result = compute_conviction_from_digest(digest)
    assert result.triggered_kill_count == 5
    assert result.conviction == 1.0  # clamp(5 + 0 - 10, 1, 10) = 1.0


def test_compute_conviction_clamped_to_max():
    """IP 10 + kill 0 → min(10, 5 + 4) = 9.0 (max bonus 4이므로 10 미만)."""
    digest = {
        "ticker": "005930",
        "investment_points": [{"id": i} for i in range(10)],
        "kill_conditions": [],
    }
    result = compute_conviction_from_digest(digest)
    assert result.conviction == 9.0  # cap이 IP bonus에서 걸림


def test_compute_conviction_missing_ticker():
    """ticker/symbol 없으면 None 반환."""
    digest = {"investment_points": [], "kill_conditions": []}
    assert compute_conviction_from_digest(digest) is None


def test_compute_conviction_uses_symbol_field():
    """'ticker' 없고 'symbol' 있으면 symbol 사용."""
    digest = {"symbol": "000660", "investment_points": [], "kill_conditions": []}
    result = compute_conviction_from_digest(digest)
    assert result.symbol == "000660"


# ── load_cufa_digests_from_dir ─────────────────────────────────────


def test_load_cufa_digests_from_dir(tmp_path):
    """디렉토리에서 JSON 파일 로드."""
    d1 = {"ticker": "005930", "investment_points": [{"id": 1}]}
    d2 = {"ticker": "000660", "investment_points": [{"id": 1}, {"id": 2}]}
    (tmp_path / "a.json").write_text(json.dumps(d1), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(d2), encoding="utf-8")

    digests = load_cufa_digests_from_dir(tmp_path)
    assert len(digests) == 2
    tickers = {d["ticker"] for d in digests}
    assert tickers == {"005930", "000660"}


def test_load_cufa_digests_nonexistent_dir(tmp_path):
    """디렉토리 없으면 빈 리스트 (raise 금지)."""
    digests = load_cufa_digests_from_dir(tmp_path / "nonexistent")
    assert digests == []


def test_load_cufa_digests_skips_invalid_json(tmp_path):
    """손상된 JSON은 스킵하고 나머지 성공분 반환."""
    (tmp_path / "valid.json").write_text(
        json.dumps({"ticker": "005930"}), encoding="utf-8"
    )
    (tmp_path / "broken.json").write_text("not valid json {", encoding="utf-8")

    digests = load_cufa_digests_from_dir(tmp_path)
    assert len(digests) == 1
    assert digests[0]["ticker"] == "005930"


# ── build_convictions_from_digests ─────────────────────────────────


def test_build_convictions_from_digests():
    digests = [
        {"ticker": "005930", "investment_points": [{"id": 1}]},
        {"ticker": "000660", "investment_points": [{"id": 1}, {"id": 2}]},
    ]
    result = build_convictions_from_digests(digests)
    assert result == {"005930": 6.0, "000660": 7.0}


def test_build_convictions_last_wins_for_duplicates():
    """동일 symbol이 여러 digest에 → 마지막 값이 덮어씀."""
    digests = [
        {"ticker": "005930", "investment_points": []},                    # 5.0
        {"ticker": "005930", "investment_points": [{"id": 1}, {"id": 2}]},  # 7.0
    ]
    result = build_convictions_from_digests(digests)
    assert result == {"005930": 7.0}


# ── LuxonTerminal boot() CUFA 통합 ─────────────────────────────────


def test_terminal_boot_ingests_cufa_convictions(tmp_path, monkeypatch):
    """boot() 호출 시 cufa_digests_dir가 지정되면 conviction이 저장됨."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    # CUFA digest 준비
    cufa_dir = tmp_path / "cufa_digests"
    cufa_dir.mkdir()
    digest = {
        "ticker": "005930",
        "investment_points": [{"id": 1}, {"id": 2}, {"id": 3}],
        "kill_conditions": [],
    }
    (cufa_dir / "samsung.json").write_text(json.dumps(digest), encoding="utf-8")

    from kis_backtest.luxon.terminal import LuxonTerminal, TerminalConfig

    term = LuxonTerminal(TerminalConfig(
        symbols=["005930", "000660"],
        capital=10_000_000,
        paper_mode=True,
        cufa_digests_dir=cufa_dir,
    ))
    term.boot()

    # FeedbackAdapter가 저장한 convictions 확인
    conv_file = tmp_path / ".luxon" / "convictions.json"
    assert conv_file.exists()
    stored = json.loads(conv_file.read_text(encoding="utf-8"))
    assert stored["005930"] == 8.0  # base 5 + 3 IPs
    # 000660은 CUFA 없으므로 default 5.0로 existing에 포함
    assert stored.get("000660", 5.0) == 5.0


def test_terminal_boot_without_cufa_dir_skips_ingestion(tmp_path, monkeypatch):
    """cufa_digests_dir 미설정이면 conviction 파일 자동 생성 안 함."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    from kis_backtest.luxon.terminal import LuxonTerminal

    term = LuxonTerminal(symbols=["005930"], paper_mode=True)
    term.boot()

    # CUFA 디렉토리 없으므로 _ingest_cufa_convictions 호출 안 됨
    # (convictions.json이 없어야 하거나, 있어도 빈 상태)
    conv_file = tmp_path / ".luxon" / "convictions.json"
    if conv_file.exists():
        stored = json.loads(conv_file.read_text(encoding="utf-8"))
        # 자동 주입이 없으므로 비어있거나 사전 세션 데이터
        assert "005930" not in stored or stored["005930"] == 5.0
