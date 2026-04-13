"""WeeklyReport → base_convictions 자동 조정 + KillSwitch/CapitalLadder 자동 트리거.

Flow:
    WeeklyReport
      ↓
    FeedbackAdapter.apply()
      ├─ kill_conditions 트리거 → KillSwitch.activate()
      ├─ MDD < -15% → CapitalLadder.demote()
      ├─ Sharpe > 1.5 && return > 0 → CapitalLadder.promote()
      └─ recommendations 파싱 → conviction 조정
      ↓
    조정된 convictions 반환 + 히스토리 저장
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from kis_backtest.portfolio.review_engine import WeeklyReport

if TYPE_CHECKING:
    from kis_backtest.execution.capital_ladder import CapitalLadder
    from kis_backtest.execution.kill_switch import KillSwitch

logger = logging.getLogger(__name__)

_LUXON_DIR = Path.home() / ".luxon"
_HISTORY_FILE = _LUXON_DIR / "feedback_history.json"
_CONVICTIONS_FILE = _LUXON_DIR / "convictions.json"

_CONVICTION_MIN = 1.0
_CONVICTION_MAX = 10.0
_CONVICTION_STEP = 1.0
_MDD_DEMOTE_THRESHOLD = -0.15
_SHARPE_PROMOTE_THRESHOLD = 1.5

_PREFIX_REDUCE = "비중 축소:"
_PREFIX_INCREASE = "비중 확대:"


def _ensure_luxon_dir() -> None:
    """~/.luxon 디렉토리 생성 (없으면)."""
    try:
        _LUXON_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("~/.luxon 디렉토리 생성 실패: %s", e)


def _load_json(path: Path) -> list | dict:
    """JSON 파일 로드. 실패 시 빈 컨테이너 반환."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("JSON 로드 실패 (%s): %s", path, e)
    return [] if path == _HISTORY_FILE else {}


def _save_json(path: Path, data: list | dict) -> None:
    """JSON 파일 저장. 실패 시 경고만."""
    try:
        _ensure_luxon_dir()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("JSON 저장 실패 (%s): %s", path, e)


def _parse_ticker(recommendation: str, prefix: str) -> str | None:
    """'비중 축소: 005930' → '005930'. 파싱 실패 시 None."""
    try:
        return recommendation.split(prefix, 1)[1].strip()
    except IndexError:
        logger.warning("추천 파싱 실패: %r", recommendation)
        return None


class FeedbackAdapter:
    """WeeklyReport를 convictions 조정 + 인프라 트리거로 변환.

    Args:
        kill_switch: KillSwitch 인스턴스. None이면 트리거 스킵.
        capital_ladder: CapitalLadder 인스턴스. None이면 트리거 스킵.
    """

    def __init__(
        self,
        kill_switch: KillSwitch | None = None,
        capital_ladder: CapitalLadder | None = None,
    ) -> None:
        self._kill_switch = kill_switch
        self._capital_ladder = capital_ladder
        self._history: list[dict] = _load_json(_HISTORY_FILE)  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(
        self,
        report: WeeklyReport,
        base_convictions: dict[str, float],
    ) -> dict[str, float]:
        """WeeklyReport 기반으로 convictions 조정 후 반환.

        1. Kill conditions 트리거 → KillSwitch 활성화
        2. MDD 기준 CapitalLadder 강등/승격
        3. recommendations 파싱 → conviction 조정
        4. 히스토리 저장

        Args:
            report: 주간 복기 리포트.
            base_convictions: 심볼 → conviction 점수 (1.0–10.0).

        Returns:
            조정된 convictions dict (새 객체, 원본 불변).
        """
        self._handle_kill_conditions(report)
        self._handle_capital_ladder(report)
        updated = self._adjust_convictions(report, base_convictions)
        self._append_history(report, base_convictions, updated)
        return updated

    def get_history(self, n: int = 10) -> list[dict]:
        """최근 n개 피드백 기록 반환."""
        return self._history[-n:]

    def load_persisted_convictions(
        self,
        symbols: list[str],
        default: float = 5.0,
    ) -> dict[str, float]:
        """~/.luxon/convictions.json에서 convictions 로드.

        저장된 값이 없는 심볼은 default로 초기화.

        Args:
            symbols: 유니버스 심볼 목록.
            default: 미등록 심볼의 기본 conviction.

        Returns:
            심볼 → conviction dict.
        """
        persisted: dict = _load_json(_CONVICTIONS_FILE)  # type: ignore[assignment]
        return {s: float(persisted.get(s, default)) for s in symbols}

    def save_convictions(self, convictions: dict[str, float]) -> None:
        """현재 convictions를 ~/.luxon/convictions.json에 저장."""
        _save_json(_CONVICTIONS_FILE, convictions)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _handle_kill_conditions(self, report: WeeklyReport) -> None:
        """트리거된 Kill Condition → KillSwitch.activate()."""
        if self._kill_switch is None:
            return
        for kc in report.kill_conditions:
            if kc.is_triggered:
                logger.warning("Kill condition 트리거: %s", kc.description)
                try:
                    self._kill_switch.activate(kc.description)
                except Exception as e:
                    logger.warning("KillSwitch 활성화 실패: %s", e)

    def _handle_capital_ladder(self, report: WeeklyReport) -> None:
        """MDD/Sharpe 기준으로 CapitalLadder 강등 또는 승격."""
        if self._capital_ladder is None:
            return
        if report.max_drawdown < _MDD_DEMOTE_THRESHOLD:
            logger.warning(
                "MDD %.1f%% → CapitalLadder 강등",
                report.max_drawdown * 100,
            )
            try:
                self._capital_ladder.demote("15% 초과 드로다운")
            except Exception as e:
                logger.warning("CapitalLadder.demote 실패: %s", e)
        elif report.sharpe > _SHARPE_PROMOTE_THRESHOLD and report.portfolio_return > 0:
            logger.info(
                "Sharpe %.2f, return %.2f%% → CapitalLadder 승격",
                report.sharpe,
                report.portfolio_return * 100,
            )
            try:
                self._capital_ladder.promote()
            except Exception as e:
                logger.warning("CapitalLadder.promote 실패: %s", e)

    def _adjust_convictions(
        self,
        report: WeeklyReport,
        base: dict[str, float],
    ) -> dict[str, float]:
        """recommendations를 파싱해 conviction 조정 후 새 dict 반환."""
        result = dict(base)
        for rec in report.recommendations:
            if rec.startswith(_PREFIX_REDUCE):
                ticker = _parse_ticker(rec, _PREFIX_REDUCE)
                if ticker is not None:
                    result[ticker] = max(
                        _CONVICTION_MIN,
                        result.get(ticker, 5.0) - _CONVICTION_STEP,
                    )
            elif rec.startswith(_PREFIX_INCREASE):
                ticker = _parse_ticker(rec, _PREFIX_INCREASE)
                if ticker is not None:
                    result[ticker] = min(
                        _CONVICTION_MAX,
                        result.get(ticker, 5.0) + _CONVICTION_STEP,
                    )
        return result

    def _append_history(
        self,
        report: WeeklyReport,
        before: dict[str, float],
        after: dict[str, float],
    ) -> None:
        """피드백 기록을 메모리 + 파일에 추가."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "period": f"{report.period_start}~{report.period_end}",
            "portfolio_return": report.portfolio_return,
            "benchmark_return": report.benchmark_return,
            "max_drawdown": report.max_drawdown,
            "sharpe": report.sharpe,
            "kill_triggered": any(kc.is_triggered for kc in report.kill_conditions),
            "conviction_delta": {
                k: after[k] - before.get(k, 5.0)
                for k in after
                if after[k] != before.get(k, 5.0)
            },
        }
        self._history.append(entry)
        _save_json(_HISTORY_FILE, self._history)
