"""Kronos/Chronos foundation model 어댑터.

전략: AAAI 2026 Kronos 원본이 공개되면 교체. 현재는 Amazon Chronos-T5-small (Apache 2.0) 사용.
의존성: transformers, torch (CPU). 최초 호출 시 HuggingFace pull (~250MB).

Usage:
    from kis_backtest.forecasting import predict
    result = predict(close_series, horizon_days=21)
    print(result.median[-1], result.p10[-1], result.p90[-1])
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_MODEL_CACHE: dict[str, object] = {}


@dataclass(frozen=True)
class ForecastResult:
    """분위수 기반 예측 결과."""

    median: np.ndarray
    p10: np.ndarray
    p90: np.ndarray
    horizon: int
    model_name: str

    def summary(self) -> str:
        last_med = float(self.median[-1])
        last_p10 = float(self.p10[-1])
        last_p90 = float(self.p90[-1])
        return (
            f"[{self.model_name}] +{self.horizon}일 예측: "
            f"median={last_med:.2f}, 80% CI=[{last_p10:.2f}, {last_p90:.2f}]"
        )


class KronosAdapter:
    """Chronos-small 백엔드 (Kronos 원본 공개 전 임시)."""

    DEFAULT_MODEL = "amazon/chronos-t5-small"

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or self.DEFAULT_MODEL

    def _load(self):
        if self.model_name in _MODEL_CACHE:
            return _MODEL_CACHE[self.model_name]

        try:
            from chronos import ChronosPipeline  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "chronos-forecasting 미설치. pip install chronos-forecasting"
            ) from exc

        import torch  # type: ignore

        logger.info(f"Chronos 모델 로드: {self.model_name}")
        pipeline = ChronosPipeline.from_pretrained(
            self.model_name,
            device_map="cpu",
            torch_dtype=torch.float32,
        )
        _MODEL_CACHE[self.model_name] = pipeline
        return pipeline

    def predict(
        self,
        series: pd.Series | np.ndarray,
        horizon_days: int = 21,
        num_samples: int = 20,
    ) -> ForecastResult:
        import torch  # type: ignore

        pipeline = self._load()
        values = np.asarray(series, dtype=np.float32)
        if len(values) < 30:
            raise ValueError(f"최소 30 포인트 필요, 받음: {len(values)}")

        context = torch.tensor(values)
        forecast = pipeline.predict(context, prediction_length=horizon_days, num_samples=num_samples)
        # shape: (1, num_samples, horizon)
        samples = forecast[0].numpy()

        return ForecastResult(
            median=np.quantile(samples, 0.5, axis=0),
            p10=np.quantile(samples, 0.1, axis=0),
            p90=np.quantile(samples, 0.9, axis=0),
            horizon=horizon_days,
            model_name=self.model_name,
        )


_DEFAULT = KronosAdapter()


def predict(
    series: pd.Series | np.ndarray,
    horizon_days: int = 21,
) -> ForecastResult:
    return _DEFAULT.predict(series, horizon_days=horizon_days)
