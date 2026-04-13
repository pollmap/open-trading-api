"""TA 신호 적중률 추적기 — TASignalIngestor의 probability 점진적 갱신.

Flow:
    TASignalIngestor.run()
      ↓
    SignalAccuracyTracker.record()   ← 신호 발생 시 즉시 기록
      ↓ (5일 경과 후)
    SignalAccuracyTracker.update_outcomes()  ← 실제 수익률로 hit 채우기
      ↓
    SignalAccuracyTracker.probability(source)
      → TASignalIngestor가 prob= 파라미터로 사용
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_LUXON_DIR = Path.home() / ".luxon"
_DEFAULT_DB = _LUXON_DIR / "signal_accuracy.json"

_OUTCOME_HORIZON_DAYS = 5
_LOOKBACK_RECORDS = 50
_WEIGHT_DEFAULT = 0.3
_WEIGHT_LEARNED = 0.7

_DEFAULT_PROB: dict[str, float] = {
    "RSI": 0.70,
    "MACD": 0.60,
    "Bollinger": 0.65,
}


@dataclass
class SignalAccuracy:
    """단일 TA 신호 및 결과 기록."""

    source: str                          # "RSI" | "MACD" | "Bollinger"
    direction: int                       # +1 (상승) or -1 (하락)
    signal_date: str                     # ISO date "YYYY-MM-DD"
    symbol: str
    predicted_return_5d: Optional[float] = None  # 5일 후 실제 수익률
    hit: Optional[bool] = None           # 적중 여부


class SignalAccuracyTracker:
    """TA 신호 적중률을 JSON DB로 추적하고 probability를 학습.

    Args:
        db_path: 저장 경로. None이면 ~/.luxon/signal_accuracy.json.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._records: list[SignalAccuracy] = []
        self.load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        symbol: str,
        source: str,
        direction: int,
        signal_date: str,
    ) -> None:
        """신규 신호를 미결 상태(hit=None)로 기록.

        Args:
            symbol: 종목 코드 (예: "005930").
            source: 신호 출처 ("RSI" | "MACD" | "Bollinger").
            direction: +1 상승 신호, -1 하락 신호.
            signal_date: 신호 발생일 ISO date (예: "2026-01-15").
        """
        rec = SignalAccuracy(
            source=source,
            direction=direction,
            signal_date=signal_date,
            symbol=symbol,
        )
        self._records.append(rec)
        logger.debug("신호 기록: %s %s %+d (%s)", symbol, source, direction, signal_date)
        self.save()

    def update_outcomes(
        self,
        symbol: str,
        returns_by_date: dict[str, float],
    ) -> None:
        """5일 이상 지난 미결 레코드에 실제 수익률과 hit 채우기.

        Args:
            symbol: 대상 종목 코드.
            returns_by_date: {ISO date → 해당일의 일간 수익률} 딕셔너리.
                             5일 누적 수익률 계산에 사용.
        """
        today = date.today()
        updated = False
        for rec in self._records:
            if rec.symbol != symbol or rec.hit is not None:
                continue
            if not self._is_outcome_ready(rec.signal_date, today):
                continue
            ret_5d = self._calc_5d_return(rec.signal_date, returns_by_date)
            if ret_5d is None:
                continue
            rec.predicted_return_5d = ret_5d
            rec.hit = self._evaluate_hit(rec.direction, ret_5d)
            logger.debug(
                "결과 업데이트: %s %s 5d=%.2f%% hit=%s",
                symbol,
                rec.source,
                ret_5d * 100,
                rec.hit,
            )
            updated = True
        if updated:
            self.save()

    def probability(self, source: str, min_samples: int = 5) -> float:
        """source 신호의 적중 확률 반환.

        Args:
            source: "RSI" | "MACD" | "Bollinger".
            min_samples: 최소 완료 샘플 수. 미달 시 기본값과 가중 평균.

        Returns:
            0.0~1.0 확률값.
        """
        default = _DEFAULT_PROB.get(source, 0.65)
        completed = [r for r in self._records if r.source == source and r.hit is not None]
        recent = completed[-_LOOKBACK_RECORDS:]

        if len(recent) < min_samples:
            return default

        hit_rate = sum(1 for r in recent if r.hit) / len(recent)
        blended = _WEIGHT_DEFAULT * default + _WEIGHT_LEARNED * hit_rate
        logger.debug(
            "probability(%s): n=%d hit=%.2f blended=%.2f",
            source,
            len(recent),
            hit_rate,
            blended,
        )
        return round(blended, 4)

    def save(self) -> None:
        """레코드를 JSON 파일에 직렬화."""
        try:
            _LUXON_DIR.mkdir(parents=True, exist_ok=True)
            data = [asdict(r) for r in self._records]
            self._db_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("SignalAccuracyTracker 저장 실패: %s", e)

    def load(self) -> None:
        """JSON 파일에서 레코드 역직렬화."""
        try:
            if not self._db_path.exists():
                return
            raw: list[dict] = json.loads(
                self._db_path.read_text(encoding="utf-8")
            )
            self._records = [SignalAccuracy(**item) for item in raw]
            logger.debug("SignalAccuracyTracker 로드: %d 레코드", len(self._records))
        except (json.JSONDecodeError, OSError, TypeError) as e:
            logger.warning("SignalAccuracyTracker 로드 실패: %s", e)
            self._records = []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_outcome_ready(signal_date: str, today: date) -> bool:
        """신호일로부터 OUTCOME_HORIZON_DAYS 이상 경과했는지 확인."""
        try:
            sig = date.fromisoformat(signal_date)
            return (today - sig).days >= _OUTCOME_HORIZON_DAYS
        except ValueError as e:
            logger.warning("날짜 파싱 실패 (%r): %s", signal_date, e)
            return False

    @staticmethod
    def _calc_5d_return(
        signal_date: str,
        returns_by_date: dict[str, float],
    ) -> Optional[float]:
        """신호일 다음날부터 5 거래일 수익률 누적 계산.

        Args:
            signal_date: 신호 발생일 ISO date.
            returns_by_date: {ISO date → 일간 수익률} (예: 0.01 = +1%).

        Returns:
            5일 누적 수익률. 데이터 부족 시 None.
        """
        try:
            start = date.fromisoformat(signal_date)
        except ValueError:
            return None

        collected: list[float] = []
        cursor = start + timedelta(days=1)
        checked = 0

        while len(collected) < _OUTCOME_HORIZON_DAYS and checked < 30:
            key = cursor.isoformat()
            if key in returns_by_date:
                collected.append(returns_by_date[key])
            cursor += timedelta(days=1)
            checked += 1

        if len(collected) < _OUTCOME_HORIZON_DAYS:
            return None

        cumulative = 1.0
        for r in collected:
            cumulative *= 1.0 + r
        return cumulative - 1.0

    @staticmethod
    def _evaluate_hit(direction: int, ret_5d: float) -> bool:
        """신호 방향과 실제 수익률로 적중 여부 판정.

        Args:
            direction: +1 상승 신호, -1 하락 신호.
            ret_5d: 5일 누적 수익률.

        Returns:
            적중 여부.
        """
        if direction == 1:
            return ret_5d > 0.0
        if direction == -1:
            return ret_5d < 0.0
        logger.warning("알 수 없는 direction: %d", direction)
        return False
