"""
Luxon Terminal — MacroDashboard (Sprint 1 A2)

matplotlib 기반 FRED 10지표 다크테마 대시보드.
2×5 그리드 + 카테고리별 색상 + 마지막 관측일 footer + HTML 래퍼.

설계 원칙:
    - matplotlib만 사용 (플랜 Sprint 1 제약)
    - 다크 테마 하드코딩 (report/themes/ 없으면 내장 팔레트)
    - 금리 시리즈는 zero line
    - PNG + HTML 모두 출력

사용 예:
    from kis_backtest.luxon.ui.macro_dashboard import MacroDashboard
    dashboard = MacroDashboard()
    dashboard.render_png(data, Path("./out/macro.png"))
    dashboard.render_html(data, Path("./out/macro.html"))
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime
from io import BytesIO
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt

from kis_backtest.luxon.stream.schema import (
    FredSeries,
    FredSeriesId,
    SeriesCategory,
)

matplotlib.use("Agg")  # 서버/백그라운드 렌더링 (GUI 불필요)

# 한글 폰트 설정 (Windows Malgun Gothic 우선, macOS/Linux fallback)
# matplotlib은 리스트 순서대로 fallback 시도
matplotlib.rcParams["font.family"] = [
    "Malgun Gothic",      # Windows (C:\Windows\Fonts\malgun.ttf)
    "AppleGothic",        # macOS
    "Noto Sans CJK KR",   # Linux
    "NanumGothic",        # Linux alt
    "DejaVu Sans",        # ASCII fallback
]
matplotlib.rcParams["axes.unicode_minus"] = False  # 마이너스 기호 깨짐 방지

logger = logging.getLogger(__name__)


# 다크 테마 팔레트 (Bloomberg-inspired)
_DARK_BG = "#0a0e14"
_DARK_PANEL = "#12171f"
_DARK_GRID = "#1e2530"
_DARK_TEXT = "#d0d6e0"
_DARK_MUTED = "#6b7280"

# 카테고리별 색상
_CATEGORY_COLORS: dict[SeriesCategory, str] = {
    SeriesCategory.RATES: "#4ade80",       # green
    SeriesCategory.INFLATION: "#f87171",   # red
    SeriesCategory.LABOR: "#60a5fa",       # blue
    SeriesCategory.LIQUIDITY: "#a78bfa",   # purple
    SeriesCategory.RISK: "#fbbf24",        # amber
    SeriesCategory.COMMODITY: "#f97316",   # orange
    SeriesCategory.FX: "#22d3ee",          # cyan
}


class MacroDashboard:
    """FRED 10지표 다크 대시보드 렌더러.

    레이아웃: 2행 × 5열 subplot
    """

    def __init__(
        self,
        theme: str = "dark",
        figsize: tuple[float, float] = (18.0, 9.0),
    ) -> None:
        self._theme = theme
        self._figsize = figsize
        if theme != "dark":
            logger.warning(
                "MacroDashboard: %s 테마 미지원, dark로 fallback", theme
            )

    def _build_figure(
        self, data: dict[FredSeriesId, FredSeries]
    ) -> plt.Figure:
        """매트플롯립 figure 생성 (PNG/HTML 공통)."""
        fig, axes = plt.subplots(
            2, 5, figsize=self._figsize, facecolor=_DARK_BG
        )
        axes_flat = axes.flatten()

        # FredSeriesId enum 순서대로 subplot 배치 (레지스트리 순서와 일치)
        ordered_ids = [sid for sid in FredSeriesId if sid in data]
        for idx, sid in enumerate(ordered_ids):
            if idx >= 10:
                break
            ax = axes_flat[idx]
            self._plot_single_series(ax, data[sid])

        # 빈 subplot 숨기기
        for idx in range(len(ordered_ids), 10):
            axes_flat[idx].axis("off")

        # 전체 제목
        last_obs_dates = [
            s.last_observation for s in data.values()
        ]
        latest_str = (
            max(last_obs_dates).isoformat() if last_obs_dates else "N/A"
        )
        fig.suptitle(
            f"Luxon Terminal — FRED Macro Dashboard  ·  최신 관측일 {latest_str}",
            color=_DARK_TEXT,
            fontsize=14,
            y=0.98,
        )

        # Footer (생성 시각 + 출처)
        fig.text(
            0.5,
            0.01,
            f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}  ·  "
            f"Source: Nexus MCP fred_get_series  ·  Luxon Terminal v0.1",
            ha="center",
            color=_DARK_MUTED,
            fontsize=8,
        )

        fig.tight_layout(rect=(0.0, 0.03, 1.0, 0.95))
        return fig

    def _plot_single_series(
        self, ax: plt.Axes, series: FredSeries
    ) -> None:
        """단일 subplot 렌더링."""
        ax.set_facecolor(_DARK_PANEL)

        color = _CATEGORY_COLORS.get(series.meta.category, _DARK_TEXT)
        df = series.data

        # 최근 10년만 표시 (가독성)
        if len(df) > 0:
            cutoff = df.index.max() - pd.Timedelta(days=365 * 10)
            df = df[df.index >= cutoff]

        ax.plot(df.index, df["value"], color=color, linewidth=1.5)
        ax.fill_between(
            df.index, df["value"], alpha=0.15, color=color
        )

        # 금리 시리즈에 zero line
        if series.meta.category == SeriesCategory.RATES and series.meta.id.value.startswith("T10"):
            ax.axhline(y=0, color=_DARK_MUTED, linestyle="--", linewidth=0.8)

        # 제목 + 최신 값
        latest_value = (
            float(df["value"].iloc[-1]) if len(df) > 0 else float("nan")
        )
        title = (
            f"{series.meta.label_ko}\n"
            f"{latest_value:.2f} {series.meta.unit}"
        )
        ax.set_title(title, color=_DARK_TEXT, fontsize=9, loc="left")

        # 스타일
        ax.tick_params(colors=_DARK_MUTED, labelsize=7)
        for spine in ax.spines.values():
            spine.set_color(_DARK_GRID)
        ax.grid(True, color=_DARK_GRID, linewidth=0.5, alpha=0.5)

        # 마지막 관측일 작게
        ax.text(
            0.98,
            0.02,
            f"{series.last_observation.isoformat()}",
            transform=ax.transAxes,
            color=_DARK_MUTED,
            fontsize=6,
            ha="right",
            va="bottom",
        )

    def render_png(
        self,
        data: dict[FredSeriesId, FredSeries],
        out_path: Path,
    ) -> Path:
        """PNG 파일 생성."""
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig = self._build_figure(data)
        fig.savefig(
            out_path,
            facecolor=_DARK_BG,
            dpi=120,
            bbox_inches="tight",
        )
        plt.close(fig)
        logger.info("MacroDashboard PNG 저장: %s", out_path)
        return out_path

    def render_html(
        self,
        data: dict[FredSeriesId, FredSeries],
        out_path: Path,
    ) -> Path:
        """HTML 래퍼 (PNG를 base64로 embed + subplot 메타 정보)."""
        out_path.parent.mkdir(parents=True, exist_ok=True)

        fig = self._build_figure(data)
        buf = BytesIO()
        fig.savefig(
            buf, format="png", facecolor=_DARK_BG, dpi=120, bbox_inches="tight"
        )
        plt.close(fig)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode("ascii")

        # subplot 정보 테이블 (HTML 파싱 테스트용 data 속성)
        rows: list[str] = []
        ordered_ids = [sid for sid in FredSeriesId if sid in data]
        for sid in ordered_ids:
            s = data[sid]
            latest = (
                float(s.data["value"].iloc[-1]) if len(s.data) > 0 else float("nan")
            )
            rows.append(
                f'<tr data-subplot="{sid.value}">'
                f'<td>{s.meta.label_ko}</td>'
                f'<td>{latest:.2f} {s.meta.unit}</td>'
                f'<td>{s.last_observation.isoformat()}</td>'
                f'<td>{s.source.value}</td>'
                f'</tr>'
            )

        latest_str = (
            max(s.last_observation for s in data.values()).isoformat()
            if data
            else "N/A"
        )

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>Luxon Terminal — FRED Macro Dashboard</title>
<style>
  body {{
    background: {_DARK_BG};
    color: {_DARK_TEXT};
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', monospace;
    margin: 0;
    padding: 20px;
  }}
  h1 {{ font-size: 18px; color: {_DARK_TEXT}; margin-bottom: 5px; }}
  .subtitle {{ color: {_DARK_MUTED}; font-size: 12px; margin-bottom: 20px; }}
  img.dashboard {{
    width: 100%;
    max-width: 1800px;
    border: 1px solid {_DARK_GRID};
    border-radius: 6px;
  }}
  table {{
    width: 100%;
    max-width: 1800px;
    margin-top: 20px;
    border-collapse: collapse;
    font-size: 12px;
  }}
  th, td {{
    padding: 8px 12px;
    border-bottom: 1px solid {_DARK_GRID};
    text-align: left;
  }}
  th {{ color: {_DARK_MUTED}; font-weight: normal; text-transform: uppercase; font-size: 10px; }}
  footer {{ color: {_DARK_MUTED}; font-size: 10px; margin-top: 30px; text-align: center; }}
</style>
</head>
<body>
  <h1>Luxon Terminal — FRED Macro Dashboard</h1>
  <div class="subtitle">최신 관측일: {latest_str} · Source: Nexus MCP fred_get_series</div>
  <img class="dashboard" src="data:image/png;base64,{b64}" alt="FRED Macro Dashboard">
  <table>
    <thead>
      <tr><th>지표</th><th>최신값</th><th>관측일</th><th>출처</th></tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
  <footer>Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · Luxon Terminal v0.1</footer>
</body>
</html>
"""
        out_path.write_text(html, encoding="utf-8")
        logger.info("MacroDashboard HTML 저장: %s", out_path)
        return out_path


# pandas는 _plot_single_series의 Timedelta에만 필요 — top-level import
import pandas as pd  # noqa: E402


__all__ = ["MacroDashboard"]
