"""
Luxon Terminal — Stream 레이어 공유 스키마 (SSOT)

Sprint 1 FRED Quick Win의 모든 에이전트(A1-A6)가 읽기 전용으로 참조하는
공통 타입 정의. 6-에이전트 병렬 개발 프로토콜(플랜 섹션 13.4)의 핵심 가드.

수정 권한:
    - A5 (Schema Guard) 또는 Luxon 본인만
    - 다른 에이전트가 이 파일 수정 시 A7 감사에서 자동 reject

설계 원칙:
    - frozen dataclass로 불변성 보장 (CLAUDE.md 불변성 원칙)
    - Enum으로 매직 스트링 제거
    - pandas DataFrame은 FredSeries.data에 격리

참조:
    플랜: C:\\Users\\lch68\\.claude\\plans\\valiant-honking-simon.md 섹션 13.4
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path

import pandas as pd


class FredSeriesId(str, Enum):
    """Sprint 1에서 추적하는 10개 FRED 거시 지표.

    Druckenmiller 4-state 레짐 판별 + 한국 노출도(원/달러, 원유) 반영.
    series_registry.yaml과 1:1 대응.
    """

    DGS10 = "DGS10"            # 미 10년물 국채금리
    DGS2 = "DGS2"              # 미 2년물 국채금리
    T10Y2Y = "T10Y2Y"          # 10-2 스프레드 (경기침체 신호)
    DFF = "DFF"                # 연방기금 실효 금리
    CPIAUCSL = "CPIAUCSL"      # 소비자물가지수 (YoY 변환)
    UNRATE = "UNRATE"          # 실업률
    M2SL = "M2SL"              # M2 통화량 (YoY 변환)
    VIXCLS = "VIXCLS"          # VIX 변동성 지수
    DCOILWTICO = "DCOILWTICO"  # WTI 원유
    DEXKOUS = "DEXKOUS"        # 원/달러 환율


class TransformType(str, Enum):
    """시리즈 변환 방식."""

    NONE = "none"
    PCT_CHANGE_YOY = "pct_change_yoy"
    DIFF = "diff"


class FredSource(str, Enum):
    """FRED 시리즈 데이터 출처 추적 (데이터 품질 감사용).

    MCP 우선 원칙(플랜 섹션 13.66.9):
        1. MCP_NEXUS가 기본 주 경로 (Nexus MCP fred_get_series 도구)
        2. CACHE는 TTL 내 재요청 시 Parquet에서 로드
        3. FRED_DIRECT는 옵션 (선택적 오프라인 폴백)
    """

    MCP_NEXUS = "mcp_nexus"      # Nexus MCP 398도구 경유 (주 경로)
    CACHE = "cache"              # 로컬 Parquet 캐시
    FRED_DIRECT = "fred_direct"  # 직접 fredapi (선택)


class SeriesCategory(str, Enum):
    """시리즈 카테고리 (대시보드 그룹핑용)."""

    RATES = "rates"
    INFLATION = "inflation"
    LABOR = "labor"
    LIQUIDITY = "liquidity"
    RISK = "risk"
    COMMODITY = "commodity"
    FX = "fx"


@dataclass(frozen=True)
class FredSeriesMeta:
    """series_registry.yaml의 개별 시리즈 메타데이터.

    A5(Schema Guard)가 YAML 로드 시 이 타입으로 변환.
    """

    id: FredSeriesId
    fred_code: str
    label_ko: str
    unit: str
    freq: str  # "daily" | "monthly" | "quarterly"
    transform: TransformType
    category: SeriesCategory


@dataclass(frozen=True)
class FredPoint:
    """단일 FRED 관측치."""

    observation_date: date
    value: float
    series_id: FredSeriesId


@dataclass
class FredSeries:
    """완전한 FRED 시리즈 데이터.

    FREDHub.load_series()의 반환 타입. MacroDashboard가 소비.
    """

    meta: FredSeriesMeta
    data: pd.DataFrame  # index=DatetimeIndex, column="value"
    fetched_at: datetime
    source: FredSource
    last_observation: date = field(init=False)

    def __post_init__(self) -> None:
        if self.data.empty:
            raise ValueError(
                f"FredSeries {self.meta.id.value} has empty data — "
                "가짜/목업 데이터 생성 금지 (실데이터 절대 원칙)"
            )
        if "value" not in self.data.columns:
            raise ValueError(
                f"FredSeries {self.meta.id.value}: data DataFrame must have "
                "'value' column"
            )
        if not isinstance(self.data.index, pd.DatetimeIndex):
            raise TypeError(
                f"FredSeries {self.meta.id.value}: data.index must be "
                "DatetimeIndex"
            )
        # last_observation은 init=False이므로 object.__setattr__ 불필요 (frozen=False)
        self.last_observation = self.data.index.max().date()


@dataclass(frozen=True)
class CacheEntry:
    """캐시 엔트리 메타.

    기본 경로: ~/.luxon/cache/fred/{series_id}.pkl
    기본 TTL: 6시간
    포맷: pickle (의존성 0, Python 표준 라이브러리)
    """

    series_id: FredSeriesId
    cache_path: Path
    cached_at: datetime
    ttl_hours: int = 6

    def is_expired(self, now: datetime | None = None) -> bool:
        """TTL 경과 여부."""
        current = now or datetime.now()
        elapsed_seconds = (current - self.cached_at).total_seconds()
        return elapsed_seconds > self.ttl_hours * 3600


@dataclass(frozen=True)
class StalenessReport:
    """시리즈 staleness 점검 결과.

    거시 데이터는 발표 주기가 다양하므로 (daily/monthly),
    각 시리즈별 임계치를 다르게 적용해야 함.
    기본 임계치: 영업일 기준 5일.
    """

    series_id: FredSeriesId
    last_observation: date
    business_days_stale: int
    is_stale: bool
    threshold_business_days: int = 5
    checked_at: datetime = field(default_factory=datetime.now)


# =====================================================================
# Sprint 3 — TickVault (실시간 틱 저장소) SSOT 확장
# =====================================================================
#
# 원칙 (Sprint 3 착수 가이드 DoD):
#   1. 재사용 의무 — 기존 FRED 타입 패턴 그대로 복제 (frozen dataclass)
#   2. 빈 데이터 거부 — 목업/가짜 데이터 생성 금지 (실데이터 절대 원칙)
#   3. 교체 가능성 — Phase 4 ClickHouse 마이그레이션 시 TickPoint는 불변
#   4. 경로 규약 — ~/.luxon/data/ticks/{exchange}/{symbol}/{YYYY-MM-DD}.pkl


class Exchange(str, Enum):
    """TickVault가 수집하는 거래소 식별자.

    경로 규약에 사용되므로 값은 lowercase 영문으로 고정.
    Phase 4에서 CCXT 통합 시 값을 추가할 예정.
    """

    KIS = "kis"        # 한국투자증권 (KRX KOSPI/KOSDAQ)
    UPBIT = "upbit"    # 업비트 (KRW 마켓 중심 암호화폐)


@dataclass(frozen=True)
class TickPoint:
    """단일 거래소 틱 (실시간 체결/호가 1건).

    Phase 1 ~ Phase 6 어디서나 통용되는 불변 원본 레코드.
    FRED 패턴과 일관되게 frozen dataclass로 equality/해싱 안전.

    필수 필드:
        timestamp    — 거래소 타임스탬프 (epoch ms 변환 후 aware datetime 권장)
        symbol       — "005930" (KIS) / "KRW-BTC" (Upbit) 원본 그대로
        exchange     — Exchange enum
        last         — 마지막 체결가 (>0)

    선택 필드:
        bid/ask      — 호가 스냅샷 (한쪽만 받는 스트림도 있음)
        volume       — 해당 틱의 체결 거래량 (누적이 아닌 증분)
        extra        — 거래소별 보조 컬럼을 frozenset-style tuple로 보존

    설계 주의:
        - frozen=True는 dict 필드를 여전히 허용하나 equality 충돌을 막기 위해
          extra는 tuple[tuple[str, Any], ...]로 저장. dict 원하면 dict(extra).
        - __post_init__에서 값 검증만. 변환은 tap 계층에서.
    """

    timestamp: datetime
    symbol: str
    exchange: Exchange
    last: float
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    extra: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime):
            raise TypeError(
                f"TickPoint.timestamp must be datetime, got {type(self.timestamp).__name__}"
            )
        if not self.symbol:
            raise ValueError(
                "TickPoint.symbol is empty — 실데이터 절대 원칙 위반 (목업/빈 데이터 금지)"
            )
        # [SECURITY] A6 보안 감사 HIGH-2: symbol path injection 차단.
        # TickVault가 symbol을 파일 경로 세그먼트로 직접 사용하므로, 악성 값이
        # 들어오면 루트 밖에 파일을 쓸 수 있다. KIS("005930")/Upbit("KRW-BTC")는
        # 이 문자들을 쓰지 않으므로 거부해도 정상 흐름에 영향 없음.
        forbidden_in_symbol = ("/", "\\", "..", "\x00")
        for bad in forbidden_in_symbol:
            if bad in self.symbol:
                raise ValueError(
                    f"TickPoint.symbol contains forbidden path character "
                    f"{bad!r} (got {self.symbol!r}) — path traversal 방지"
                )
        if self.last <= 0:
            raise ValueError(
                f"TickPoint.last must be > 0, got {self.last} "
                f"(symbol={self.symbol}, exchange={self.exchange.value})"
            )
        if self.volume is not None and self.volume < 0:
            raise ValueError(
                f"TickPoint.volume must be >= 0, got {self.volume}"
            )


@dataclass(frozen=True)
class TickMeta:
    """일별 TickVault 파일의 메타데이터 요약.

    TickVault.describe(exchange, symbol, day)가 반환하는 스냅샷.
    운영 대시보드/감사 로그에서 누적 틱 수와 경로만 빠르게 확인할 때 사용.
    """

    exchange: Exchange
    symbol: str
    day: date
    path: Path
    tick_count: int
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    bytes_on_disk: int = 0

    def is_empty(self) -> bool:
        """틱이 0개면 True (TickVault.put은 0을 허용하지 않지만 조회는 가능)."""
        return self.tick_count == 0


@dataclass(frozen=True)
class ReplaySpec:
    """TickReplayer의 재생 설정.

    Attributes:
        speed: 1.0=실제 속도, >1 가속, <1 감속, -1 또는 0이면 무한속도(지연 0).
        start_offset: 파일 첫 틱에서 건너뛸 개수 (테스트/부분 재생용).
        limit: 재생할 최대 틱 수 (None=전부).
    """

    speed: float = 1.0
    start_offset: int = 0
    limit: int | None = None

    def __post_init__(self) -> None:
        if self.start_offset < 0:
            raise ValueError("ReplaySpec.start_offset must be >= 0")
        if self.limit is not None and self.limit < 0:
            raise ValueError("ReplaySpec.limit must be None or >= 0")

    @property
    def is_unlimited_speed(self) -> bool:
        """speed<=0이면 delay=0으로 전송 (백테스트 전속 재생)."""
        return self.speed <= 0


# 공개 API (A5 외에는 수정 금지)
__all__ = [
    # FRED (Sprint 1)
    "FredSeriesId",
    "TransformType",
    "FredSource",
    "SeriesCategory",
    "FredSeriesMeta",
    "FredPoint",
    "FredSeries",
    "CacheEntry",
    "StalenessReport",
    # TickVault (Sprint 3)
    "Exchange",
    "TickPoint",
    "TickMeta",
    "ReplaySpec",
]
