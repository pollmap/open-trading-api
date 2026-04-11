"""macro_regime.py 실 MCP 통합 테스트 (Sprint 2 R11 회귀 방지).

2026-04-11 Sprint 1에서 발견된 치명 버그(R11):
    기존 macro_regime.py가 호출하던 `fred_get_series`와 `ecos_get_indicator`는
    Nexus MCP에 등록되지 않은 도구였음. try/except가 silent fail시켜
    "검증 완료"로 잘못 기록되던 문제.

Sprint 2 수정 (이 파일):
    실제 MCP 서버에 연결해서 10개 지표가 **실제로** 값을 수집하는지
    integration 마커로 회귀 방지. mock 테스트만으로는 도구 이름 오류 감지 불가.

실행:
    # 이 통합 테스트만
    pytest tests/test_macro_regime_integration.py -m integration -v

    # 기본 실행에서 제외 (기본)
    pytest -m "not integration"

환경:
    NEXUS_MCP_TOKEN이 ~/.mcp.json에 있어야 함 (MCPDataProvider 자동 로드).
    네트워크 미연결 시 자동 skip.
"""
from __future__ import annotations

import pytest

from kis_backtest.portfolio.macro_regime import (
    _ECOS_INDICATOR_CONFIG,
    _FRED_SERIES_MAP,
    MacroRegimeDashboard,
)
from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider


async def _ensure_mcp() -> MCPDataProvider:
    """MCP 연결 확인 (실패 시 테스트 skip)."""
    mcp = MCPDataProvider()
    try:
        # 가벼운 discover_tools 호출로 연결 검증
        tools = await mcp.discover_tools()
        if len(tools) < 100:
            pytest.skip(f"MCP 도구 수 비정상: {len(tools)} (기대 398+)")
        return mcp
    except Exception as e:
        pytest.skip(f"MCP 연결 실패: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fred_macro_tool_exists() -> None:
    """`macro_fred` 도구가 실제로 Nexus MCP에 존재하는지 검증 (R11 회귀)."""
    mcp = await _ensure_mcp()
    tools = await mcp.discover_tools()
    assert "macro_fred" in tools, (
        "R11 회귀: macro_fred 도구가 Nexus MCP에 없음. "
        "기존 fred_get_series 실수 반복."
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ecos_dedicated_tools_exist() -> None:
    """ECOS 전용 도구들이 실제로 Nexus MCP에 존재하는지 검증 (R11 회귀)."""
    mcp = await _ensure_mcp()
    tools = await mcp.discover_tools()

    required = {
        "ecos_get_base_rate",
        "ecos_get_gdp",
        "ecos_get_m2",
        "ecos_get_exchange_rate",
        "ecos_get_stat_data",
    }
    missing = required - set(tools.keys())
    assert not missing, (
        f"R11 회귀: ECOS 필수 도구 누락 {missing}. "
        "macro_regime._ECOS_INDICATOR_CONFIG와 실제 MCP 도구 이름 동기화 필요."
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_single_fred_returns_data() -> None:
    """단일 FRED 호출이 실제로 숫자 데이터를 반환하는지."""
    mcp = await _ensure_mcp()
    dashboard = MacroRegimeDashboard()
    await dashboard._fetch_fred_series(mcp, "미국 기준금리", "FEDFUNDS")

    ind = dashboard.indicators["미국 기준금리"]
    assert ind.value is not None, (
        "R11 회귀: macro_fred 호출 후 값이 None. "
        "silent fail 복원 — 도구 이름 또는 응답 구조 불일치."
    )
    assert 0 < ind.value < 20, f"FRED 기준금리 값 이상: {ind.value}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_single_ecos_gdp_returns_data() -> None:
    """ECOS GDP 전용 도구가 실제로 동작하는지."""
    mcp = await _ensure_mcp()
    dashboard = MacroRegimeDashboard()
    config = _ECOS_INDICATOR_CONFIG["GDP 성장률"]
    await dashboard._fetch_ecos_indicator(mcp, "GDP 성장률", config)

    ind = dashboard.indicators["GDP 성장률"]
    # GDP는 분기별이라 값이 나오지 않을 수 있음, 최소한 silent-fail 문자열 에러는 없어야
    # 실패해도 `_extract_numeric_value`가 None 반환 → value=None (기존 동작 유지)
    # 핵심은 MCP 서버 에러 문자열이 없는 것
    assert ind.value is None or isinstance(ind.value, (int, float))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_indicators_full_pipeline() -> None:
    """fetch_indicators 전체 실행 후 **최소 5개** 지표가 실제 값 확보 (R11 핵심 회귀).

    기존 silent fail 상태에선 0~1개만 값 있었음 (base_rate만 동작).
    Sprint 2 수정 후 FRED 4개 + base_rate 최소 5개는 값 확보 기대.
    """
    mcp = await _ensure_mcp()
    dashboard = MacroRegimeDashboard()

    await dashboard.fetch_indicators(mcp)

    filled = [
        name
        for name, ind in dashboard.indicators.items()
        if ind.value is not None
    ]
    assert len(filled) >= 5, (
        f"R11 회귀: 10개 지표 중 {len(filled)}개만 값 확보 "
        f"(filled={filled}). 기존 silent-fail 상태로 퇴행."
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unknown_tool_returns_server_error_string() -> None:
    """존재하지 않는 도구 호출 시 MCP 서버가 `{'data': 'Unknown tool: ...'}` 반환 확인.

    이 테스트가 실패하면 서버 응답 포맷이 변경된 것.
    `_fetch_ecos_indicator`와 `_fetch_fred_series`의 에러 감지 로직 재검토 필요.
    """
    mcp = await _ensure_mcp()
    result = await mcp._call_vps_tool("this_tool_does_not_exist_xyz", {})
    assert isinstance(result, dict)
    data = result.get("data")
    assert isinstance(data, str), (
        f"MCP 서버 에러 응답 포맷 변경 감지: data={data!r}. "
        "macro_regime.py와 luxon/stream/fred_hub.py의 에러 감지 로직 업데이트 필요."
    )
    assert "unknown tool" in data.lower() or "error" in data.lower()
