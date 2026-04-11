"""
Luxon Terminal — Phase 1 통합 파이프라인 테스트 (Sprint 4 STEP 2)

핸드오프 파일의 "STEP 2 착수 가이드" DoD를 정확히 따른다:
    1. 모든 성공 경로 (errors=[])
    2. FRED 실패 격리 (macro/vault는 정상)
    3. MacroRegime 실패 격리
    4. TickVault **실 인스턴스** + tmp_path (M3/M4/M5 가드 회귀 방지)
    5. close() flush_all 검증
    + 부가: RegimeResult None fallback, errors 누적, partial_success

실 MCP 호출 금지 — 이 테스트는 순수 단위 테스트. 실 MCP 스모크는 Sprint 4
STEP 3 (4D)에서 처음 등장.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kis_backtest.luxon.integration.phase1_pipeline import (
    Phase1CheckpointResult,
    Phase1Pipeline,
)
from kis_backtest.luxon.stream.schema import Exchange, FredSeriesId, TickPoint
from kis_backtest.luxon.stream.tick_vault import TickVault


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_mock_fred_hub(
    series_count: int = 3,
    stale_count: int = 1,
    raise_on_load: Exception | None = None,
) -> MagicMock:
    """FREDHub 모킹 헬퍼. load_all async + detect_staleness sync."""
    hub = MagicMock(name="FREDHub")

    if raise_on_load is not None:
        hub.load_all = AsyncMock(side_effect=raise_on_load)
    else:
        # load_all → dict[FredSeriesId, FredSeries] 시뮬레이션.
        # 내용은 detect_staleness에 전달만 되므로 sentinel로 충분.
        series_dict = {
            # FredSeriesId 값 몇 개를 실제로 꺼내서 키로 사용
            sid: MagicMock(name=f"FredSeries[{sid.value}]")
            for sid in list(FredSeriesId)[:series_count]
        }
        hub.load_all = AsyncMock(return_value=series_dict)

    def _staleness_side_effect(series: Any) -> MagicMock:
        # series.name → "FredSeries[...]" 기반으로 stale 여부 결정
        name = getattr(series, "name", "") or ""
        idx = _staleness_side_effect._call_count  # type: ignore[attr-defined]
        _staleness_side_effect._call_count += 1  # type: ignore[attr-defined]
        report = MagicMock(name=f"StalenessReport[{name}]")
        report.is_stale = idx < stale_count
        return report

    _staleness_side_effect._call_count = 0  # type: ignore[attr-defined]
    hub.detect_staleness = MagicMock(side_effect=_staleness_side_effect)
    return hub


def _build_mock_macro_dashboard(
    indicator_count: int = 9,
    regime_summary: str = "EXPANSION",
    raise_on_fetch: Exception | None = None,
    raise_on_classify: Exception | None = None,
) -> MagicMock:
    """MacroRegimeDashboard 모킹 헬퍼."""
    dash = MagicMock(name="MacroRegimeDashboard")

    if raise_on_fetch is not None:
        dash.fetch_indicators = AsyncMock(side_effect=raise_on_fetch)
    else:
        indicators = {
            f"indicator_{i}": MagicMock(name=f"MacroIndicator[{i}]")
            for i in range(indicator_count)
        }
        dash.fetch_indicators = AsyncMock(return_value=indicators)

    if raise_on_classify is not None:
        dash.classify_regime = MagicMock(side_effect=raise_on_classify)
    else:
        regime = MagicMock(name="RegimeResult")
        regime.summary = MagicMock(return_value=regime_summary)
        dash.classify_regime = MagicMock(return_value=regime)

    return dash


def _make_tick(symbol: str = "005930", last: float = 72000.0) -> TickPoint:
    """유효한 TickPoint 하나."""
    return TickPoint(
        timestamp=datetime(2026, 4, 11, 9, 15, 0),
        symbol=symbol,
        exchange=Exchange.KIS,
        last=last,
        bid=None,
        ask=None,
        volume=100.0,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_vault(tmp_path: Path) -> TickVault:
    """실제 TickVault 인스턴스 (tmp_path 루트, M3/M4/M5 가드 회귀 방지)."""
    root = tmp_path / "ticks"
    return TickVault(root_dir=root, retention_days=1, flush_interval=2)


@pytest.fixture
def mock_mcp() -> MagicMock:
    """MCPDataProvider 경량 mock (fetch_indicators가 인자로만 받음)."""
    return MagicMock(name="MCPDataProvider")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_all_success(
    tmp_vault: TickVault, mock_mcp: MagicMock
) -> None:
    """1. 모든 경로 성공 → errors=[] / success=True / 필드 채워짐."""
    fred_hub = _build_mock_fred_hub(series_count=3, stale_count=1)
    macro = _build_mock_macro_dashboard(indicator_count=9)
    pipeline = Phase1Pipeline(
        fred_hub=fred_hub,
        tick_vault=tmp_vault,
        macro_dashboard=macro,
        mcp=mock_mcp,
    )

    result = await pipeline.checkpoint()

    assert isinstance(result, Phase1CheckpointResult)
    assert result.success is True
    assert result.errors == []
    assert result.fred_series_loaded == 3
    assert result.fred_stale_count == 1
    assert result.macro_indicator_count == 9
    assert result.regime_result is not None
    assert result.regime_result.summary() == "EXPANSION"
    assert "buffered_keys" in result.tick_vault_stats
    fred_hub.load_all.assert_awaited_once()
    macro.fetch_indicators.assert_awaited_once_with(mock_mcp)
    macro.classify_regime.assert_called_once()


@pytest.mark.asyncio
async def test_checkpoint_fred_failure_isolation(
    tmp_vault: TickVault, mock_mcp: MagicMock
) -> None:
    """2. FRED raise → FRED 필드는 0, macro/vault는 정상. errors에 FRED만."""
    fred_hub = _build_mock_fred_hub(
        raise_on_load=RuntimeError("MCP macro_fred 빈 응답")
    )
    macro = _build_mock_macro_dashboard(indicator_count=10)
    pipeline = Phase1Pipeline(
        fred_hub=fred_hub,
        tick_vault=tmp_vault,
        macro_dashboard=macro,
        mcp=mock_mcp,
    )

    result = await pipeline.checkpoint()

    assert result.success is False
    assert result.partial_success is True
    assert result.fred_series_loaded == 0
    assert result.fred_stale_count == 0
    assert result.macro_indicator_count == 10
    assert result.regime_result is not None
    assert len(result.errors) == 1
    assert result.errors[0].startswith("FRED: RuntimeError")
    # TickVault stats는 성공
    assert "total_files" in result.tick_vault_stats


@pytest.mark.asyncio
async def test_checkpoint_macro_fetch_failure_isolation(
    tmp_vault: TickVault, mock_mcp: MagicMock
) -> None:
    """3. MacroRegime fetch raise → regime None, FRED/vault 정상."""
    fred_hub = _build_mock_fred_hub(series_count=2, stale_count=0)
    macro = _build_mock_macro_dashboard(
        raise_on_fetch=RuntimeError("MCP ecos 서버 에러")
    )
    pipeline = Phase1Pipeline(
        fred_hub=fred_hub,
        tick_vault=tmp_vault,
        macro_dashboard=macro,
        mcp=mock_mcp,
    )

    result = await pipeline.checkpoint()

    assert result.fred_series_loaded == 2
    assert result.regime_result is None
    assert result.macro_indicator_count == 0
    assert len(result.errors) == 1
    assert "MacroRegime.fetch" in result.errors[0]
    # classify_regime은 호출조차 되지 않아야 함 (fetch 실패 시 바로 다음 블록)
    macro.classify_regime.assert_not_called()


@pytest.mark.asyncio
async def test_checkpoint_macro_classify_failure_isolation(
    tmp_vault: TickVault, mock_mcp: MagicMock
) -> None:
    """3b. fetch는 성공했지만 classify_regime이 raise → indicator_count는 유지."""
    fred_hub = _build_mock_fred_hub(series_count=1, stale_count=0)
    macro = _build_mock_macro_dashboard(
        indicator_count=8,
        raise_on_classify=ValueError("지표 부족"),
    )
    pipeline = Phase1Pipeline(
        fred_hub=fred_hub,
        tick_vault=tmp_vault,
        macro_dashboard=macro,
        mcp=mock_mcp,
    )

    result = await pipeline.checkpoint()

    assert result.macro_indicator_count == 8
    assert result.regime_result is None
    assert len(result.errors) == 1
    assert "MacroRegime.classify" in result.errors[0]


@pytest.mark.asyncio
async def test_checkpoint_tick_vault_real_instance_with_buffered_ticks(
    tmp_path: Path, mock_mcp: MagicMock
) -> None:
    """4. TickVault **실제 인스턴스**에 틱 주입 → stats 반영 확인.

    M3 (pop), M5 (flush_all 격리), M6 (파일 생성 경로)를 함께 경유.
    """
    root = tmp_path / "ticks_real"
    # flush_interval=5로 설정해서 틱 3개는 버퍼에 남음
    vault = TickVault(root_dir=root, retention_days=1, flush_interval=5)
    vault.append(_make_tick("005930", 71000.0))
    vault.append(_make_tick("005930", 71100.0))
    vault.append(_make_tick("000660", 128000.0))

    fred_hub = _build_mock_fred_hub(series_count=1, stale_count=0)
    macro = _build_mock_macro_dashboard(indicator_count=5)
    pipeline = Phase1Pipeline(
        fred_hub=fred_hub,
        tick_vault=vault,
        macro_dashboard=macro,
        mcp=mock_mcp,
    )

    result = await pipeline.checkpoint()

    stats = result.tick_vault_stats
    assert stats["buffered_keys"] == 2  # 005930, 000660
    assert stats["buffered_ticks"] == 3
    # 아직 flush 전이므로 디스크 파일 0
    assert stats["total_files"] == 0


@pytest.mark.asyncio
async def test_close_flushes_tick_vault_to_disk(
    tmp_path: Path, mock_mcp: MagicMock
) -> None:
    """5. pipeline.close() → TickVault.flush_all() 호출 → 디스크 파일 생성."""
    root = tmp_path / "ticks_close"
    vault = TickVault(root_dir=root, retention_days=1, flush_interval=100)
    vault.append(_make_tick("005930", 72000.0))
    vault.append(_make_tick("005930", 72100.0))

    fred_hub = _build_mock_fred_hub(series_count=0)
    macro = _build_mock_macro_dashboard()
    pipeline = Phase1Pipeline(
        fred_hub=fred_hub,
        tick_vault=vault,
        macro_dashboard=macro,
        mcp=mock_mcp,
    )

    # close 전: 파일 없음
    assert vault.stats()["total_files"] == 0
    pipeline.close()
    # close 후: 1개 파일 생성 (005930의 단일 일자)
    assert vault.stats()["total_files"] == 1
    assert vault.stats()["buffered_keys"] == 0  # M3 pop 효과 검증


@pytest.mark.asyncio
async def test_checkpoint_result_partial_success_logic(
    tmp_vault: TickVault, mock_mcp: MagicMock
) -> None:
    """6. partial_success 프로퍼티 — 하나라도 성공하면 True."""
    fred_hub = _build_mock_fred_hub(
        raise_on_load=RuntimeError("FRED 전체 실패")
    )
    macro = _build_mock_macro_dashboard(
        raise_on_fetch=RuntimeError("macro 실패")
    )
    pipeline = Phase1Pipeline(
        fred_hub=fred_hub,
        tick_vault=tmp_vault,
        macro_dashboard=macro,
        mcp=mock_mcp,
    )

    result = await pipeline.checkpoint()

    # vault_stats는 여전히 성공 (빈 vault여도 dict 반환)
    assert result.success is False
    assert bool(result.tick_vault_stats) is True  # stats 사전은 비어있지 않음
    assert result.partial_success is True  # vault_stats만이라도 있음
    assert len(result.errors) == 2  # FRED + MacroRegime fetch


@pytest.mark.asyncio
async def test_pipeline_properties_expose_components(
    tmp_vault: TickVault, mock_mcp: MagicMock
) -> None:
    """7. fred_hub/tick_vault/macro_dashboard 프로퍼티가 주입된 인스턴스를 그대로 노출."""
    fred_hub = _build_mock_fred_hub()
    macro = _build_mock_macro_dashboard()
    pipeline = Phase1Pipeline(
        fred_hub=fred_hub,
        tick_vault=tmp_vault,
        macro_dashboard=macro,
        mcp=mock_mcp,
    )

    assert pipeline.fred_hub is fred_hub
    assert pipeline.tick_vault is tmp_vault
    assert pipeline.macro_dashboard is macro
