"""카탈리스트 트래커 테스트 — Ackman의 "왜 지금?" 시스템"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kis_backtest.portfolio.catalyst_tracker import (
    Catalyst,
    CatalystScore,
    CatalystTracker,
    CatalystType,
    _normalize_date,
)


# ── 유틸 함수 테스트 ──────────────────────────────────────

class TestNormalizeDate:
    def test_yyyymmdd(self):
        assert _normalize_date("20260415") == "2026-04-15"

    def test_iso_format(self):
        assert _normalize_date("2026-04-15") == "2026-04-15"

    def test_dot_format(self):
        assert _normalize_date("2026.04.15") == "2026-04-15"

    def test_slash_format(self):
        assert _normalize_date("2026/04/15") == "2026-04-15"

    def test_unknown_format_passthrough(self):
        assert _normalize_date("April 15") == "April 15"


# ── Catalyst 데이터클래스 테스트 ─────────────────────────

class TestCatalyst:
    def _make(self, **overrides) -> Catalyst:
        defaults = dict(
            symbol="005930",
            name="테스트 카탈리스트",
            catalyst_type=CatalystType.EARNINGS,
            expected_date=(date.today() + timedelta(days=10)).strftime("%Y-%m-%d"),
            probability=0.7,
            impact=5.0,
        )
        defaults.update(overrides)
        return Catalyst(**defaults)

    def test_id_is_deterministic(self):
        c1 = self._make(name="동일 이름")
        c2 = self._make(name="동일 이름")
        assert c1.id == c2.id

    def test_id_differs_for_different_names(self):
        c1 = self._make(name="이름A")
        c2 = self._make(name="이름B")
        assert c1.id != c2.id

    def test_days_until_future(self):
        c = self._make(expected_date=(date.today() + timedelta(days=15)).strftime("%Y-%m-%d"))
        assert c.days_until == 15

    def test_days_until_past(self):
        c = self._make(expected_date=(date.today() - timedelta(days=5)).strftime("%Y-%m-%d"))
        assert c.days_until == -5

    def test_time_weight_today(self):
        c = self._make(expected_date=date.today().strftime("%Y-%m-%d"))
        assert c.time_weight == pytest.approx(1.0, abs=0.01)

    def test_time_weight_30_days(self):
        c = self._make(expected_date=(date.today() + timedelta(days=30)).strftime("%Y-%m-%d"))
        expected = math.exp(-(30 ** 2) / (2 * 60 ** 2))
        assert c.time_weight == pytest.approx(expected, abs=0.01)

    def test_time_weight_past_30_days_zero(self):
        c = self._make(expected_date=(date.today() - timedelta(days=31)).strftime("%Y-%m-%d"))
        assert c.time_weight == 0.0

    def test_weighted_score_positive(self):
        c = self._make(
            expected_date=date.today().strftime("%Y-%m-%d"),
            probability=0.8,
            impact=6.0,
        )
        # 0.8 * 6.0 * ~1.0 = ~4.8
        assert c.weighted_score == pytest.approx(4.8, abs=0.2)

    def test_weighted_score_negative(self):
        c = self._make(
            expected_date=date.today().strftime("%Y-%m-%d"),
            probability=0.5,
            impact=-4.0,
        )
        assert c.weighted_score < 0

    def test_frozen(self):
        c = self._make()
        with pytest.raises(AttributeError):
            c.probability = 0.9  # type: ignore[misc]


# ── CatalystScore 테스트 ─────────────────────────────────

class TestCatalystScore:
    def test_has_catalyst_false(self):
        score = CatalystScore(
            symbol="005930", total=0.0, positive_score=0.0,
            negative_score=0.0, catalyst_count=0, top_catalyst=None,
            urgency="none",
        )
        assert not score.has_catalyst
        assert not score.is_actionable

    def test_is_actionable_above_threshold(self):
        score = CatalystScore(
            symbol="005930", total=3.5, positive_score=3.5,
            negative_score=0.0, catalyst_count=2, top_catalyst="테스트",
            urgency="near",
        )
        assert score.is_actionable

    def test_is_actionable_below_threshold(self):
        score = CatalystScore(
            symbol="005930", total=1.5, positive_score=2.0,
            negative_score=-0.5, catalyst_count=2, top_catalyst="테스트",
            urgency="near",
        )
        assert not score.is_actionable

    def test_summary_contains_symbol(self):
        score = CatalystScore(
            symbol="005930", total=5.0, positive_score=5.0,
            negative_score=0.0, catalyst_count=1, top_catalyst="HBM4",
            urgency="imminent",
        )
        assert "005930" in score.summary()
        assert "5.0" in score.summary()


# ── CatalystTracker CRUD 테스트 ──────────────────────────

class TestCatalystTrackerCRUD:
    def test_add_and_list(self):
        tracker = CatalystTracker()
        tracker.add(
            symbol="005930",
            name="HBM4 양산",
            catalyst_type="industry",
            expected_date=(date.today() + timedelta(days=20)).strftime("%Y-%m-%d"),
            probability=0.7,
            impact=8,
        )
        catalysts = tracker.list_by_symbol("005930")
        assert len(catalysts) == 1
        assert catalysts[0].name == "HBM4 양산"
        assert catalysts[0].catalyst_type == CatalystType.INDUSTRY

    def test_add_invalid_probability(self):
        tracker = CatalystTracker()
        with pytest.raises(ValueError, match="probability"):
            tracker.add(
                symbol="005930", name="테스트",
                catalyst_type="earnings",
                expected_date="2026-06-01",
                probability=1.5, impact=5,
            )

    def test_add_invalid_impact(self):
        tracker = CatalystTracker()
        with pytest.raises(ValueError, match="impact"):
            tracker.add(
                symbol="005930", name="테스트",
                catalyst_type="earnings",
                expected_date="2026-06-01",
                probability=0.5, impact=15,
            )

    def test_remove(self):
        tracker = CatalystTracker()
        tracker.add(
            symbol="005930", name="삭제 대상",
            catalyst_type="earnings",
            expected_date="2026-06-01",
            probability=0.5, impact=5,
        )
        assert tracker.remove("005930", "삭제 대상") is True
        assert tracker.list_by_symbol("005930") == []

    def test_remove_nonexistent(self):
        tracker = CatalystTracker()
        assert tracker.remove("005930", "없는 카탈리스트") is False

    def test_resolve(self):
        tracker = CatalystTracker()
        tracker.add(
            symbol="005930", name="실적 발표",
            catalyst_type="earnings",
            expected_date="2026-06-01",
            probability=0.8, impact=6,
        )
        resolved = tracker.resolve("005930", "실적 발표", actual_impact=7.0)
        assert resolved is not None
        assert resolved.resolved is True
        assert resolved.actual_impact == 7.0

        # 해결된 카탈리스트는 active 목록에서 제외
        assert tracker.list_by_symbol("005930", active_only=True) == []

    def test_resolve_nonexistent(self):
        tracker = CatalystTracker()
        assert tracker.resolve("005930", "없는것") is None

    def test_list_all_active(self):
        tracker = CatalystTracker()
        future = (date.today() + timedelta(days=10)).strftime("%Y-%m-%d")
        tracker.add(symbol="005930", name="A", catalyst_type="earnings",
                     expected_date=future, probability=0.5, impact=5)
        tracker.add(symbol="000660", name="B", catalyst_type="industry",
                     expected_date=future, probability=0.6, impact=7)
        assert len(tracker.list_all_active()) == 2

    def test_symbols_with_catalysts(self):
        tracker = CatalystTracker()
        future = (date.today() + timedelta(days=10)).strftime("%Y-%m-%d")
        tracker.add(symbol="005930", name="A", catalyst_type="earnings",
                     expected_date=future, probability=0.5, impact=5)
        tracker.add(symbol="000660", name="B", catalyst_type="industry",
                     expected_date=future, probability=0.6, impact=7)
        symbols = tracker.symbols_with_catalysts()
        assert set(symbols) == {"005930", "000660"}


# ── 스코어링 테스트 ──────────────────────────────────────

class TestCatalystScoring:
    def test_no_catalysts_score_zero(self):
        tracker = CatalystTracker()
        score = tracker.score("005930")
        assert score.total == 0.0
        assert score.urgency == "none"
        assert not score.has_catalyst

    def test_single_positive_catalyst(self):
        tracker = CatalystTracker()
        tracker.add(
            symbol="005930", name="HBM4",
            catalyst_type="industry",
            expected_date=(date.today() + timedelta(days=5)).strftime("%Y-%m-%d"),
            probability=0.8, impact=8,
        )
        score = tracker.score("005930")
        assert score.total > 0
        assert score.positive_score > 0
        assert score.negative_score == 0
        assert score.urgency == "imminent"

    def test_mixed_catalysts(self):
        tracker = CatalystTracker()
        future = (date.today() + timedelta(days=15)).strftime("%Y-%m-%d")
        tracker.add(symbol="005930", name="긍정", catalyst_type="earnings",
                     expected_date=future, probability=0.8, impact=6)
        tracker.add(symbol="005930", name="부정", catalyst_type="regulation",
                     expected_date=future, probability=0.5, impact=-4)
        score = tracker.score("005930")
        assert score.positive_score > 0
        assert score.negative_score < 0
        assert score.catalyst_count == 2

    def test_distant_catalyst_lower_score(self):
        tracker = CatalystTracker()
        near = (date.today() + timedelta(days=5)).strftime("%Y-%m-%d")
        far = (date.today() + timedelta(days=120)).strftime("%Y-%m-%d")

        tracker_near = CatalystTracker()
        tracker_near.add(symbol="005930", name="가까운", catalyst_type="earnings",
                          expected_date=near, probability=0.7, impact=5)
        tracker_far = CatalystTracker()
        tracker_far.add(symbol="005930", name="먼", catalyst_type="earnings",
                          expected_date=far, probability=0.7, impact=5)

        score_near = tracker_near.score("005930")
        score_far = tracker_far.score("005930")
        assert score_near.total > score_far.total

    def test_score_all(self):
        tracker = CatalystTracker()
        future = (date.today() + timedelta(days=10)).strftime("%Y-%m-%d")
        tracker.add(symbol="005930", name="A", catalyst_type="earnings",
                     expected_date=future, probability=0.5, impact=5)
        tracker.add(symbol="000660", name="B", catalyst_type="industry",
                     expected_date=future, probability=0.6, impact=7)
        scores = tracker.score_all()
        assert "005930" in scores
        assert "000660" in scores


# ── 영속성 테스트 ────────────────────────────────────────

class TestCatalystPersistence:
    def test_save_and_load(self, tmp_path: Path):
        state_file = str(tmp_path / "catalysts.json")
        future = (date.today() + timedelta(days=10)).strftime("%Y-%m-%d")

        # 저장
        tracker = CatalystTracker(state_file=state_file)
        tracker.add(symbol="005930", name="HBM4", catalyst_type="industry",
                     expected_date=future, probability=0.7, impact=8)
        tracker.add(symbol="000660", name="DDR6", catalyst_type="industry",
                     expected_date=future, probability=0.6, impact=6)

        # 새 인스턴스에서 로드
        tracker2 = CatalystTracker(state_file=state_file)
        assert len(tracker2.list_all_active()) == 2

        loaded = tracker2.list_by_symbol("005930")
        assert len(loaded) == 1
        assert loaded[0].name == "HBM4"

    def test_json_file_structure(self, tmp_path: Path):
        state_file = str(tmp_path / "catalysts.json")
        future = (date.today() + timedelta(days=10)).strftime("%Y-%m-%d")

        tracker = CatalystTracker(state_file=state_file)
        tracker.add(symbol="005930", name="테스트", catalyst_type="earnings",
                     expected_date=future, probability=0.5, impact=5)

        data = json.loads(Path(state_file).read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert "updated_at" in data
        assert len(data["catalysts"]) == 1
        assert data["catalysts"][0]["symbol"] == "005930"

    def test_load_nonexistent_file(self):
        tracker = CatalystTracker(state_file="/nonexistent/path.json")
        assert tracker.list_all_active() == []

    def test_resolve_persists(self, tmp_path: Path):
        state_file = str(tmp_path / "catalysts.json")
        future = (date.today() + timedelta(days=10)).strftime("%Y-%m-%d")

        tracker = CatalystTracker(state_file=state_file)
        tracker.add(symbol="005930", name="해결됨", catalyst_type="earnings",
                     expected_date=future, probability=0.5, impact=5)
        tracker.resolve("005930", "해결됨", actual_impact=6.0)

        tracker2 = CatalystTracker(state_file=state_file)
        all_cats = tracker2.list_by_symbol("005930", active_only=False)
        assert len(all_cats) == 1
        assert all_cats[0].resolved is True


# ── DART/뉴스 파서 테스트 ────────────────────────────────

class TestDartParser:
    def test_parse_dart_ma(self):
        tracker = CatalystTracker()
        item = {
            "report_nm": "주요사항보고서(합병결정)",
            "rcept_dt": "20260408",
        }
        result = tracker._parse_dart_disclosure("005930", item)
        assert result is not None
        assert result.catalyst_type == CatalystType.MA
        assert result.probability == 0.9  # 공시 = 거의 확정

    def test_parse_dart_earnings(self):
        tracker = CatalystTracker()
        item = {
            "report_nm": "영업실적공시",
            "rcept_dt": "20260401",
        }
        result = tracker._parse_dart_disclosure("005930", item)
        assert result is not None
        assert result.catalyst_type == CatalystType.EARNINGS

    def test_parse_dart_irrelevant(self):
        tracker = CatalystTracker()
        item = {
            "report_nm": "사업보고서",
            "rcept_dt": "20260401",
        }
        result = tracker._parse_dart_disclosure("005930", item)
        assert result is None  # 키워드 매칭 안 됨

    def test_parse_dart_duplicate_skipped(self):
        tracker = CatalystTracker()
        item = {
            "report_nm": "합병결정공시",
            "rcept_dt": "20260408",
        }
        tracker._parse_dart_disclosure("005930", item)
        result = tracker._parse_dart_disclosure("005930", item)
        assert result is None  # 중복 스킵

    def test_parse_news_positive(self):
        tracker = CatalystTracker()
        item = {
            "title": "삼성전자 HBM4 대량 인수 계약 체결",
            "date": "20260408",
        }
        result = tracker._parse_news_item("005930", item)
        assert result is not None
        assert result.catalyst_type == CatalystType.MA

    def test_parse_news_irrelevant(self):
        tracker = CatalystTracker()
        item = {"title": "날씨 맑음", "date": "20260408"}
        result = tracker._parse_news_item("005930", item)
        assert result is None


# ── 비동기 스캔 테스트 (Mock) ────────────────────────────

class TestAsyncScan:
    @pytest.mark.asyncio
    async def test_scan_dart(self):
        tracker = CatalystTracker()
        mock_mcp = MagicMock()
        mock_mcp._call_vps_tool = AsyncMock(return_value={
            "result": [
                {"report_nm": "합병결정공시", "rcept_dt": "20260408"},
                {"report_nm": "사업보고서", "rcept_dt": "20260401"},
            ]
        })
        catalysts = await tracker.scan_dart("005930", mock_mcp)
        assert len(catalysts) == 1  # 합병만 매칭

    @pytest.mark.asyncio
    async def test_scan_dart_failure_graceful(self):
        tracker = CatalystTracker()
        mock_mcp = MagicMock()
        mock_mcp._call_vps_tool = AsyncMock(side_effect=Exception("연결 실패"))
        catalysts = await tracker.scan_dart("005930", mock_mcp)
        assert catalysts == []

    @pytest.mark.asyncio
    async def test_scan_news(self):
        tracker = CatalystTracker()
        mock_mcp = MagicMock()
        mock_mcp._call_vps_tool = AsyncMock(return_value={
            "result": [
                {"title": "SK하이닉스 인수 추진", "date": "20260408"},
                {"title": "날씨 좋다", "date": "20260408"},
            ]
        })
        catalysts = await tracker.scan_news("005930", mock_mcp)
        assert len(catalysts) == 1  # "인수"만 매칭
