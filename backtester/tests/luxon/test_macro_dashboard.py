"""Sprint 1 — MacroDashboard (A2) 단위 테스트.

matplotlib 2×5 다크 테마 렌더링 + HTML 래퍼 검증.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kis_backtest.luxon.stream.schema import FredSeries, FredSeriesId
from kis_backtest.luxon.ui.macro_dashboard import MacroDashboard, _DARK_BG


def test_render_png_produces_valid_file(
    tmp_path: Path, sample_series_dgs10: FredSeries
) -> None:
    """render_png가 유효한 PNG 파일을 생성."""
    dashboard = MacroDashboard()
    out_path = tmp_path / "macro.png"

    data = {FredSeriesId.DGS10: sample_series_dgs10}
    result = dashboard.render_png(data, out_path)

    assert result == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 1000  # 최소 크기 (빈 파일 아님)

    # PNG 시그니처 확인 (0x89 0x50 0x4E 0x47)
    with out_path.open("rb") as f:
        sig = f.read(4)
    assert sig == b"\x89PNG"


def test_render_html_contains_subplot_data_attrs(
    tmp_path: Path, sample_series_dgs10: FredSeries
) -> None:
    """render_html에 subplot 데이터 테이블 포함."""
    dashboard = MacroDashboard()
    out_path = tmp_path / "macro.html"

    data = {FredSeriesId.DGS10: sample_series_dgs10}
    dashboard.render_html(data, out_path)

    assert out_path.exists()
    html = out_path.read_text(encoding="utf-8")

    # HTML 구조 검증
    assert "<!DOCTYPE html>" in html
    assert "Luxon Terminal" in html
    assert "FRED Macro Dashboard" in html
    assert "미 10년물 국채금리" in html
    # base64 embed 이미지
    assert 'src="data:image/png;base64,' in html
    # data 속성 (subplot 메타)
    assert 'data-subplot="DGS10"' in html


def test_dark_theme_background_color(
    tmp_path: Path, sample_series_dgs10: FredSeries
) -> None:
    """다크 테마 배경색 HTML에 반영."""
    dashboard = MacroDashboard(theme="dark")
    out_path = tmp_path / "macro.html"

    data = {FredSeriesId.DGS10: sample_series_dgs10}
    dashboard.render_html(data, out_path)

    html = out_path.read_text(encoding="utf-8")
    assert _DARK_BG in html  # 예: #0a0e14
    assert "background:" in html or "background-color:" in html


def test_footer_shows_last_observation_date(
    tmp_path: Path, sample_series_dgs10: FredSeries
) -> None:
    """HTML footer에 마지막 관측일 포함."""
    dashboard = MacroDashboard()
    out_path = tmp_path / "macro.html"

    data = {FredSeriesId.DGS10: sample_series_dgs10}
    dashboard.render_html(data, out_path)

    html = out_path.read_text(encoding="utf-8")
    last_obs_iso = sample_series_dgs10.last_observation.isoformat()
    assert last_obs_iso in html
    assert "Source: Nexus MCP" in html
