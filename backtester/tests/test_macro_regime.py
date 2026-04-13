"""매크로 레짐 대시보드 테스트 — Druckenmiller의 "큰 그림" 시스템"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kis_backtest.portfolio.macro_regime import (
    REGIME_ALLOCATION,
    MacroIndicator,
    MacroRegimeDashboard,
    Regime,
    RegimeResult,
    _extract_fred_values,
    _extract_numeric_value,
)


# ── 유틸 함수 테스트 ──────────────────────────────────────

class TestExtractNumericValue:
    def test_direct_number(self):
        assert _extract_numeric_value({"data": 2.75}) == 2.75

    def test_list_last_item(self):
        assert _extract_numeric_value({"data": [1.0, 2.0, 3.5]}) == 3.5

    def test_list_of_dicts(self):
        result = {"data": [{"value": "1.5"}, {"value": "2.75"}]}
        assert _extract_numeric_value(result) == 2.75

    def test_dict_value(self):
        assert _extract_numeric_value({"value": 3.14}) == 3.14

    def test_none_on_empty(self):
        assert _extract_numeric_value({}) is None
        assert _extract_numeric_value(None) is None  # type: ignore[arg-type]


class TestExtractFredValues:
    def test_list_of_numbers(self):
        assert _extract_fred_values({"data": [1.0, 2.0, 3.0]}) == [1.0, 2.0, 3.0]

    def test_list_of_dicts(self):
        result = {"data": [{"value": "1.5"}, {"value": "2.5"}]}
        assert _extract_fred_values(result) == [1.5, 2.5]

    def test_empty(self):
        assert _extract_fred_values({}) == []
        assert _extract_fred_values(None) == []  # type: ignore[arg-type]


# ── MacroIndicator 테스트 ────────────────────────────────

class TestMacroIndicator:
    def test_change(self):
        ind = MacroIndicator(name="기준금리", value=2.75, prev_value=3.0)
        assert ind.change == pytest.approx(-0.25)

    def test_change_pct(self):
        ind = MacroIndicator(name="GDP", value=2.5, prev_value=2.0)
        assert ind.change_pct == pytest.approx(25.0)

    def test_change_none_no_prev(self):
        ind = MacroIndicator(name="기준금리", value=2.75)
        assert ind.change is None
        assert ind.change_pct is None


# ── RegimeResult 테스트 ──────────────────────────────────

class TestRegimeResult:
    def test_summary_contains_regime(self):
        result = RegimeResult(
            regime=Regime.EXPANSION,
            confidence=0.8,
            score=5.0,
            positive_signals=7,
            negative_signals=1,
            neutral_signals=2,
            allocation={"equity": 0.7, "crypto": 0.2, "cash": 0.1},
        )
        s = result.summary()
        assert "EXPANSION" in s
        assert "80%" in s

    def test_crisis_summary(self):
        result = RegimeResult(
            regime=Regime.CRISIS,
            confidence=0.9,
            score=-8.0,
            positive_signals=0,
            negative_signals=9,
            neutral_signals=1,
            allocation={"cash": 0.7, "gold": 0.2, "inverse": 0.1},
        )
        assert "CRISIS" in result.summary()


# ── Dashboard 기본 기능 테스트 ───────────────────────────

class TestDashboardBasic:
    def test_initial_state(self):
        dashboard = MacroRegimeDashboard()
        assert len(dashboard.indicators) == 10
        assert all(ind.value is None for ind in dashboard.indicators.values())

    def test_update_indicator(self):
        dashboard = MacroRegimeDashboard()
        ind = dashboard.update_indicator("기준금리", 2.75)
        assert ind.value == 2.75
        assert ind.name == "기준금리"

    def test_update_with_prev(self):
        dashboard = MacroRegimeDashboard()
        dashboard.update_indicator("기준금리", 3.0)
        ind = dashboard.update_indicator("기준금리", 2.75)
        assert ind.value == 2.75
        assert ind.prev_value == 3.0

    def test_update_custom_indicator(self):
        dashboard = MacroRegimeDashboard()
        ind = dashboard.update_indicator("VIX", 25.0)
        assert ind.value == 25.0
        assert "VIX" in dashboard.indicators


# ── 시그널 계산 테스트 ───────────────────────────────────

class TestSignalComputation:
    def test_gdp_up_positive(self):
        sig = MacroRegimeDashboard._compute_signal("GDP 성장률", 3.0, 2.5)
        assert sig == 1

    def test_gdp_down_negative(self):
        sig = MacroRegimeDashboard._compute_signal("GDP 성장률", 2.0, 2.5)
        assert sig == -1

    def test_rate_up_negative(self):
        sig = MacroRegimeDashboard._compute_signal("기준금리", 3.5, 3.0)
        assert sig == -1

    def test_rate_down_positive(self):
        sig = MacroRegimeDashboard._compute_signal("기준금리", 2.5, 3.0)
        assert sig == 1

    def test_cpi_up_negative(self):
        sig = MacroRegimeDashboard._compute_signal("CPI (소비자물가)", 3.5, 3.0)
        assert sig == -1

    def test_m2_up_positive(self):
        sig = MacroRegimeDashboard._compute_signal("M2 통화량", 3500, 3400)
        assert sig == 1

    def test_spread_up_negative(self):
        sig = MacroRegimeDashboard._compute_signal("신용스프레드", 400, 300)
        assert sig == -1

    def test_no_change_neutral(self):
        sig = MacroRegimeDashboard._compute_signal("기준금리", 3.0, 3.0)
        assert sig == 0

    def test_none_values_neutral(self):
        sig = MacroRegimeDashboard._compute_signal("기준금리", None, 3.0)
        assert sig == 0


# ── 레짐 판별 테스트 ─────────────────────────────────────

class TestRegimeClassification:
    def _setup_expansion(self, dashboard: MacroRegimeDashboard) -> None:
        """확장기 시뮬레이션: GDP↑, M2↑, 금리↓, CPI↓"""
        dashboard.update_indicator("GDP 성장률", 3.5, 2.5)
        dashboard.update_indicator("M2 통화량", 3600, 3400)
        dashboard.update_indicator("기준금리", 2.5, 3.0)
        dashboard.update_indicator("CPI (소비자물가)", 2.0, 2.5)
        dashboard.update_indicator("미국 기준금리", 4.0, 4.5)
        dashboard.update_indicator("실업률", 3.0, 3.5)

    def _setup_contraction(self, dashboard: MacroRegimeDashboard) -> None:
        """수축기 시뮬레이션: GDP↓, 금리↑, CPI↑"""
        dashboard.update_indicator("GDP 성장률", 1.0, 2.5)
        dashboard.update_indicator("기준금리", 4.0, 3.0)
        dashboard.update_indicator("CPI (소비자물가)", 4.5, 3.0)
        dashboard.update_indicator("미국 기준금리", 5.5, 5.0)
        dashboard.update_indicator("실업률", 5.0, 4.0)
        dashboard.update_indicator("원/달러 환율", 1400, 1300)

    def _setup_crisis(self, dashboard: MacroRegimeDashboard) -> None:
        """위기 시뮬레이션: 신용스프레드 급등 + 전면 부정"""
        dashboard.update_indicator("신용스프레드", 600, 300)
        dashboard.update_indicator("GDP 성장률", -1.0, 2.0)
        dashboard.update_indicator("기준금리", 5.0, 3.0)
        dashboard.update_indicator("CPI (소비자물가)", 5.0, 3.0)
        dashboard.update_indicator("미국 기준금리", 6.0, 5.0)
        dashboard.update_indicator("실업률", 7.0, 4.0)
        dashboard.update_indicator("원/달러 환율", 1500, 1300)
        dashboard.update_indicator("유가 (WTI)", 120, 80)

    def test_expansion_regime(self):
        dashboard = MacroRegimeDashboard()
        self._setup_expansion(dashboard)
        result = dashboard.classify_regime()
        assert result.regime == Regime.EXPANSION
        assert result.positive_signals > result.negative_signals

    def test_contraction_regime(self):
        dashboard = MacroRegimeDashboard()
        self._setup_contraction(dashboard)
        result = dashboard.classify_regime()
        assert result.regime == Regime.CONTRACTION
        assert result.negative_signals > result.positive_signals

    def test_crisis_regime_spread_trigger(self):
        dashboard = MacroRegimeDashboard()
        self._setup_crisis(dashboard)
        result = dashboard.classify_regime()
        assert result.regime == Regime.CRISIS

    def test_crisis_allocation_cash_heavy(self):
        dashboard = MacroRegimeDashboard()
        self._setup_crisis(dashboard)
        result = dashboard.classify_regime()
        assert result.allocation["cash"] >= 0.7

    def test_expansion_allocation_equity_heavy(self):
        dashboard = MacroRegimeDashboard()
        self._setup_expansion(dashboard)
        result = dashboard.classify_regime()
        assert result.allocation["equity"] >= 0.7

    def test_no_data_recovery_default(self):
        dashboard = MacroRegimeDashboard()
        result = dashboard.classify_regime()
        # 데이터 없으면 중립 → RECOVERY 기본값
        assert result.regime == Regime.RECOVERY
        assert result.confidence == 0.0

    def test_confidence_scales_with_data(self):
        dashboard = MacroRegimeDashboard()
        # 5개만 업데이트 → 50% 신뢰도
        dashboard.update_indicator("GDP 성장률", 3.0, 2.5)
        dashboard.update_indicator("기준금리", 2.5, 3.0)
        dashboard.update_indicator("CPI (소비자물가)", 2.0, 2.5)
        dashboard.update_indicator("M2 통화량", 3500, 3400)
        dashboard.update_indicator("실업률", 3.0, 3.5)
        result = dashboard.classify_regime()
        assert result.confidence == pytest.approx(0.5, abs=0.01)


# ── 자산배분 테스트 ──────────────────────────────────────

class TestRecommendedAllocation:
    def test_auto_classify_on_first_call(self):
        dashboard = MacroRegimeDashboard()
        alloc = dashboard.recommended_allocation()
        assert isinstance(alloc, dict)
        assert sum(alloc.values()) == pytest.approx(1.0, abs=0.01)

    def test_all_regimes_sum_to_one(self):
        for regime, alloc in REGIME_ALLOCATION.items():
            total = sum(alloc.values())
            assert total == pytest.approx(1.0, abs=0.01), f"{regime}: {total}"


# ── 대시보드 출력 테스트 ─────────────────────────────────

class TestDashboardOutput:
    def test_summary_contains_header(self):
        dashboard = MacroRegimeDashboard()
        s = dashboard.summary()
        assert "매크로 레짐 대시보드" in s

    def test_summary_with_data(self):
        dashboard = MacroRegimeDashboard()
        dashboard.update_indicator("기준금리", 2.75)
        dashboard.classify_regime()
        s = dashboard.summary()
        assert "2.75" in s

    def test_indicator_table(self):
        dashboard = MacroRegimeDashboard()
        dashboard.update_indicator("기준금리", 2.75)
        table = dashboard.indicator_table()
        assert len(table) == 10
        rate_row = next(r for r in table if r["name"] == "기준금리")
        assert rate_row["value"] == 2.75


# ── 영속성 테스트 ────────────────────────────────────────

class TestMacroPersistence:
    def test_save_and_load(self, tmp_path: Path):
        state_file = str(tmp_path / "macro.json")

        # 저장
        d1 = MacroRegimeDashboard(state_file=state_file)
        d1.update_indicator("기준금리", 2.75, 3.0)
        d1.update_indicator("GDP 성장률", 2.5, 2.0)
        d1.classify_regime()

        # 로드
        d2 = MacroRegimeDashboard(state_file=state_file)
        assert d2.indicators["기준금리"].value == 2.75
        assert d2.indicators["GDP 성장률"].value == 2.5

    def test_json_structure(self, tmp_path: Path):
        state_file = str(tmp_path / "macro.json")
        dashboard = MacroRegimeDashboard(state_file=state_file)
        dashboard.update_indicator("기준금리", 2.75)
        dashboard.classify_regime()

        data = json.loads(Path(state_file).read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert "indicators" in data
        assert "last_regime" in data
        assert "기준금리" in data["indicators"]

    def test_load_nonexistent(self):
        dashboard = MacroRegimeDashboard(state_file="/nonexistent/macro.json")
        assert len(dashboard.indicators) == 10  # 기본값으로 초기화


# ── MCP 연동 테스트 (Mock) ───────────────────────────────

class TestMCPFetch:
    @pytest.mark.asyncio
    async def test_fetch_base_rate(self):
        dashboard = MacroRegimeDashboard()
        mock_mcp = MagicMock()
        mock_mcp.get_risk_free_rate = AsyncMock(return_value=0.0275)
        await dashboard._fetch_base_rate(mock_mcp)
        assert dashboard.indicators["기준금리"].value == pytest.approx(2.75)

    @pytest.mark.asyncio
    async def test_fetch_fred_series(self):
        dashboard = MacroRegimeDashboard()
        mock_mcp = MagicMock()
        mock_mcp._call_vps_tool = AsyncMock(return_value={
            "data": [{"value": "4.25"}, {"value": "4.50"}]
        })
        await dashboard._fetch_fred_series(mock_mcp, "미국 기준금리", "FEDFUNDS")
        ind = dashboard.indicators["미국 기준금리"]
        assert ind.value == 4.50
        assert ind.prev_value == 4.25

    @pytest.mark.asyncio
    async def test_fetch_failure_graceful(self):
        dashboard = MacroRegimeDashboard()
        mock_mcp = MagicMock()
        mock_mcp.get_risk_free_rate = AsyncMock(side_effect=Exception("연결 실패"))
        await dashboard._fetch_base_rate(mock_mcp)
        # 실패해도 에러 안 남, 이전 값 유지
        assert dashboard.indicators["기준금리"].value is None

    @pytest.mark.asyncio
    async def test_fetch_indicators_calls_all(self):
        dashboard = MacroRegimeDashboard()
        mock_mcp = MagicMock()
        mock_mcp.get_risk_free_rate = AsyncMock(return_value=0.0275)
        mock_mcp._call_vps_tool = AsyncMock(return_value={"data": [{"value": "3.0"}]})

        result = await dashboard.fetch_indicators(mock_mcp)
        assert isinstance(result, dict)
        # base_rate + 4 ECOS + 4 FRED = 9 calls
        total_calls = mock_mcp.get_risk_free_rate.call_count + mock_mcp._call_vps_tool.call_count
        assert total_calls >= 9


# ── 오프라인 캐시 폴백 테스트 ──────────────────────────────

class TestOfflineCacheFallback:
    """MCP 완전 오프라인 시 캐시된 레짐 반환 검증."""

    def test_cached_regime_returned_when_no_live_data(self, tmp_path: Path):
        """state_file에 저장된 regime이 있으면, 새 지표 데이터 없어도 캐시 반환."""
        state_file = str(tmp_path / "macro.json")

        # 1) 먼저 데이터 채워서 저장
        d1 = MacroRegimeDashboard(state_file=state_file)
        d1.update_indicator("기준금리", 2.75, 3.0)
        d1.update_indicator("GDP 성장률", 2.5, 2.0)
        d1.update_indicator("실업률", 3.5, 4.0)
        d1.update_indicator("M2 통화량", 3500, 3400)
        d1.update_indicator("CPI (소비자물가)", 2.0, 2.5)
        result1 = d1.classify_regime()
        assert result1.confidence > 0.0, "저장 전 confidence > 0 이어야 함"

        # 2) 새 인스턴스 (state_file 로드) — MCP 데이터 추가 없이 classify
        d2 = MacroRegimeDashboard(state_file=state_file)
        result2 = d2.classify_regime()

        # 캐시된 지표값(5개)으로 classify → confidence > 0
        assert result2.confidence > 0.0, "오프라인 캐시에서 confidence > 0 이어야 함"

    def test_offline_fallback_to_last_regime(self, tmp_path: Path):
        """지표값 0개인 상황에서 last_regime 캐시가 있으면 폴백."""
        state_file = str(tmp_path / "macro.json")

        # 캐시에 last_regime 직접 기록
        state_data = {
            "version": 1,
            "updated_at": "2026-01-01T00:00:00",
            "indicators": {},
            "last_regime": {
                "regime": "expansion",
                "confidence": 0.7,
                "score": 5.0,
                "positive_signals": 7,
                "negative_signals": 1,
                "neutral_signals": 2,
            },
        }
        Path(state_file).write_text(
            __import__("json").dumps(state_data, ensure_ascii=False),
            encoding="utf-8",
        )

        d = MacroRegimeDashboard(state_file=state_file)
        result = d.classify_regime()

        # 지표 0개이지만 last_regime 캐시로 폴백
        assert result.regime.value == "expansion"
        assert result.confidence == pytest.approx(0.7)

    def test_no_state_file_still_returns_zero_confidence(self):
        """state_file 없으면 기존 동작(confidence=0) 유지."""
        d = MacroRegimeDashboard()  # state_file=None
        result = d.classify_regime()
        assert result.confidence == 0.0
        assert result.regime == Regime.RECOVERY

    def test_orchestrator_uses_default_state_file(self, tmp_path: Path, monkeypatch):
        """LuxonOrchestrator 기본 생성 시 state_file이 설정됨."""
        from kis_backtest.luxon import orchestrator as orch_mod
        # 임시 경로로 override
        monkeypatch.setattr(orch_mod, "_MACRO_STATE_FILE", str(tmp_path / "macro.json"))

        from kis_backtest.luxon.orchestrator import LuxonOrchestrator
        orc = LuxonOrchestrator()
        assert orc.dashboard._state_file == str(tmp_path / "macro.json")
