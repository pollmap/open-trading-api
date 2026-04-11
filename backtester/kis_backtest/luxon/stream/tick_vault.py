"""
Luxon Terminal — TickVault (Sprint 3)

Parquet-이름 그러나 **pickle** 기반 일별 틱 저장소.
Sprint 1.5의 fred_cache.py 패턴을 그대로 복제하여 pyarrow 의존성 0.

설계 원칙:
    1. 불변성 — TickPoint는 frozen dataclass (schema.py)
    2. 명시적 실패 — 깨진 pickle은 load_day에서 None 반환 + warning
    3. 실데이터 보존 — 버퍼에 저장 전 TickPoint.__post_init__ 검증 통과
    4. 교체 가능 — Phase 4 ClickHouse 마이그레이션 시 인터페이스 유지

경로 규약 (naming_registry_sprint3.md):
    ~/.luxon/data/ticks/{exchange}/{symbol}/{YYYY-MM-DD}.pkl

환경 변수:
    LUXON_TICK_DATA_DIR       — 저장 루트 (기본 ~/.luxon/data/ticks)
    LUXON_TICK_RETENTION_DAYS — prune 기본 보관일 (기본 90)
    LUXON_TICK_FLUSH_INTERVAL — 자동 flush 임계 틱 수 (기본 50)

금지:
    - ❌ providers/kis, providers/upbit 수정
    - ❌ execution/* 수정
    - ❌ pyarrow 의존
"""
from __future__ import annotations

import logging
import os
import pickle
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from kis_backtest.luxon.stream.schema import Exchange, TickMeta, TickPoint

logger = logging.getLogger(__name__)

# 번들 포맷 버전 (향후 마이그레이션용)
_TICK_BUNDLE_VERSION = 1

_DEFAULT_RETENTION_DAYS = 90
_DEFAULT_FLUSH_INTERVAL = 50


def _default_tick_dir() -> Path:
    """틱 저장 루트 디렉토리 (env override 지원)."""
    env_dir = os.environ.get("LUXON_TICK_DATA_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return Path.home() / ".luxon" / "data" / "ticks"


def _default_retention_days() -> int:
    env = os.environ.get("LUXON_TICK_RETENTION_DAYS")
    if env:
        try:
            return int(env)
        except ValueError:
            logger.warning(
                "LUXON_TICK_RETENTION_DAYS=%s 파싱 실패, 기본값 %d 사용",
                env,
                _DEFAULT_RETENTION_DAYS,
            )
    return _DEFAULT_RETENTION_DAYS


def _default_flush_interval() -> int:
    env = os.environ.get("LUXON_TICK_FLUSH_INTERVAL")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            logger.warning(
                "LUXON_TICK_FLUSH_INTERVAL=%s 파싱 실패, 기본값 %d 사용",
                env,
                _DEFAULT_FLUSH_INTERVAL,
            )
    return _DEFAULT_FLUSH_INTERVAL


def _tick_local_day(tick: TickPoint) -> date:
    """틱의 local date (KST 영업일 정렬용).

    timestamp가 tz-aware면 해당 tz의 date, naive면 그대로 date() 사용.
    Sprint 3은 한국 시장 중심이므로 timestamp는 이미 KST로 들어온다고 가정.
    """
    return tick.timestamp.date()


class TickVault:
    """일별 pickle 기반 틱 저장소.

    내부 버퍼:
        _buffers[(exchange, symbol, day)] -> list[TickPoint]
        flush_interval마다 자동 flush. 수동 flush/flush_all도 가능.

    Usage:
        vault = TickVault()
        vault.append(tick)  # 자동 flush
        ...
        vault.flush_all()   # 세션 종료 시 강제

        ticks = vault.load_day(Exchange.KIS, "005930", date(2026, 4, 11))
        vault.prune(older_than_days=30)
    """

    def __init__(
        self,
        root_dir: Path | None = None,
        retention_days: int | None = None,
        flush_interval: int | None = None,
    ) -> None:
        self._root: Path = (root_dir or _default_tick_dir()).expanduser()
        self._retention_days: int = (
            retention_days if retention_days is not None else _default_retention_days()
        )
        self._flush_interval: int = (
            flush_interval
            if flush_interval is not None
            else _default_flush_interval()
        )
        self._root.mkdir(parents=True, exist_ok=True)
        self._buffers: dict[tuple[Exchange, str, date], list[TickPoint]] = defaultdict(
            list
        )
        logger.debug(
            "TickVault 초기화: root=%s retention=%dd flush_every=%d",
            self._root,
            self._retention_days,
            self._flush_interval,
        )

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def root_dir(self) -> Path:
        return self._root

    @property
    def retention_days(self) -> int:
        return self._retention_days

    @property
    def flush_interval(self) -> int:
        return self._flush_interval

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _tick_path(self, exchange: Exchange, symbol: str, day: date) -> Path:
        """경로 규약: root/{exchange}/{symbol}/{YYYY-MM-DD}.pkl"""
        return (
            self._root
            / exchange.value
            / symbol
            / f"{day.isoformat()}.pkl"
        )

    def _symbol_dir(self, exchange: Exchange, symbol: str) -> Path:
        return self._root / exchange.value / symbol

    # ------------------------------------------------------------------
    # Append / flush
    # ------------------------------------------------------------------

    def append(self, tick: TickPoint) -> None:
        """단일 틱을 버퍼에 추가. flush_interval 도달 시 자동 flush.

        TickPoint의 __post_init__가 이미 검증을 수행하므로 여기서는 추가
        검증 불필요. 실데이터 절대 원칙은 frozen dataclass 계층에서 보장.
        """
        key = (tick.exchange, tick.symbol, _tick_local_day(tick))
        self._buffers[key].append(tick)

        if len(self._buffers[key]) >= self._flush_interval:
            self._flush_key(key)

    def extend(self, ticks: list[TickPoint]) -> None:
        """복수 틱 한 번에 추가 (테스트/배치 주입용)."""
        for t in ticks:
            self.append(t)

    def _flush_key(
        self, key: tuple[Exchange, str, date]
    ) -> TickMeta | None:
        """내부 헬퍼: 특정 버퍼 키를 디스크에 기록."""
        ticks = self._buffers.get(key)
        if not ticks:
            return None
        exchange, symbol, day = key
        path = self._tick_path(exchange, symbol, day)
        path.parent.mkdir(parents=True, exist_ok=True)

        # 기존 파일과 병합 (append 시맨틱)
        existing: list[TickPoint] = []
        if path.exists():
            loaded = self._load_bundle(path)
            if loaded is not None:
                existing = loaded

        merged = existing + ticks
        bundle: dict[str, Any] = {
            "version": _TICK_BUNDLE_VERSION,
            "exchange": exchange.value,
            "symbol": symbol,
            "day": day.isoformat(),
            "ticks": merged,
            "first_timestamp": merged[0].timestamp.isoformat() if merged else None,
            "last_timestamp": merged[-1].timestamp.isoformat() if merged else None,
        }
        with path.open("wb") as f:
            pickle.dump(bundle, f, protocol=pickle.HIGHEST_PROTOCOL)

        logger.debug(
            "TickVault flush: %s/%s/%s → %d ticks (+%d)",
            exchange.value,
            symbol,
            day.isoformat(),
            len(merged),
            len(ticks),
        )

        # 버퍼 비움
        self._buffers[key].clear()

        return TickMeta(
            exchange=exchange,
            symbol=symbol,
            day=day,
            path=path,
            tick_count=len(merged),
            first_timestamp=merged[0].timestamp if merged else None,
            last_timestamp=merged[-1].timestamp if merged else None,
            bytes_on_disk=path.stat().st_size,
        )

    def flush(
        self,
        exchange: Exchange,
        symbol: str,
        day: date | None = None,
    ) -> TickMeta | None:
        """특정 (exchange, symbol [, day]) 버퍼 flush.

        day=None이면 해당 symbol의 모든 버퍼를 flush하고 마지막 TickMeta 반환.
        """
        if day is not None:
            return self._flush_key((exchange, symbol, day))

        last_meta: TickMeta | None = None
        keys_to_flush = [
            k for k in list(self._buffers.keys()) if k[0] == exchange and k[1] == symbol
        ]
        for k in keys_to_flush:
            meta = self._flush_key(k)
            if meta is not None:
                last_meta = meta
        return last_meta

    def flush_all(self) -> list[TickMeta]:
        """전체 버퍼 flush. 세션 종료 시 호출 필수."""
        metas: list[TickMeta] = []
        for k in list(self._buffers.keys()):
            meta = self._flush_key(k)
            if meta is not None:
                metas.append(meta)
        return metas

    # ------------------------------------------------------------------
    # Load / describe
    # ------------------------------------------------------------------

    def _load_bundle(self, path: Path) -> list[TickPoint] | None:
        """pickle 번들에서 틱 리스트 추출. 손상 시 None.

        [SECURITY — A6 감사 HIGH-1, pickle 신뢰 경계]
            pickle.load()는 역직렬화 중 임의 코드를 실행할 수 있다. 이 메서드는
            **동일 프로세스의 _flush_key()가 쓴 파일만 읽는다고 가정**한다.
            다음은 계약 위반이며 보안 사고의 원인이 된다:
              - 외부 프로세스/사용자가 제공한 경로 직접 전달
              - 네트워크 마운트(NFS, SMB), 공유 디렉토리, symlink 체인
              - 신뢰되지 않은 소스에서 다운받은 .pkl 파일 복사
            Luxon Terminal은 Phase 4(Sprint 11~13)에서 ClickHouse/Parquet로
            마이그레이션하며 이 메서드 전체를 제거한다. 그 전까지는 로컬
            사용자 홈 디렉토리(`~/.luxon/data/ticks`) 외 경로 사용 금지.
            관련 ADR: `luxon/naming_registry_sprint3.md` Section 6.
        """
        # 경로 신뢰 가드: path가 self._root 하위여야만 로드
        try:
            resolved = path.resolve()
            root_resolved = self._root.resolve()
            resolved.relative_to(root_resolved)
        except ValueError:
            logger.warning(
                "TickVault: 루트 밖 경로 로드 거부 path=%s root=%s",
                path,
                self._root,
            )
            return None

        try:
            with path.open("rb") as f:
                bundle: dict[str, Any] = pickle.load(f)
        except Exception as e:
            logger.warning("TickVault load 실패 (path=%s): %s", path, e)
            return None

        if not isinstance(bundle, dict):
            logger.warning("TickVault: 잘못된 번들 포맷 (path=%s)", path)
            return None
        if bundle.get("version") != _TICK_BUNDLE_VERSION:
            logger.warning(
                "TickVault: 번들 버전 불일치 path=%s version=%s 기대=%d",
                path,
                bundle.get("version"),
                _TICK_BUNDLE_VERSION,
            )
            return None

        ticks = bundle.get("ticks") or []
        if not isinstance(ticks, list):
            logger.warning("TickVault: ticks가 list가 아님 path=%s", path)
            return None

        # 역직렬화된 TickPoint 검증: 깨진 요소 drop
        valid: list[TickPoint] = []
        for t in ticks:
            if isinstance(t, TickPoint):
                valid.append(t)
        if len(valid) != len(ticks):
            logger.warning(
                "TickVault: 손상 틱 %d개 drop (path=%s)",
                len(ticks) - len(valid),
                path,
            )
        return valid

    def load_day(
        self,
        exchange: Exchange,
        symbol: str,
        day: date,
    ) -> list[TickPoint]:
        """특정 일자의 틱 전체 로드. 파일 없거나 손상 시 빈 리스트."""
        path = self._tick_path(exchange, symbol, day)
        if not path.exists():
            return []
        loaded = self._load_bundle(path)
        return loaded if loaded is not None else []

    def describe(
        self,
        exchange: Exchange,
        symbol: str,
        day: date,
    ) -> TickMeta | None:
        """파일 메타 조회. 없으면 None."""
        path = self._tick_path(exchange, symbol, day)
        if not path.exists():
            return None
        ticks = self.load_day(exchange, symbol, day)
        if not ticks:
            return TickMeta(
                exchange=exchange,
                symbol=symbol,
                day=day,
                path=path,
                tick_count=0,
                bytes_on_disk=path.stat().st_size,
            )
        return TickMeta(
            exchange=exchange,
            symbol=symbol,
            day=day,
            path=path,
            tick_count=len(ticks),
            first_timestamp=ticks[0].timestamp,
            last_timestamp=ticks[-1].timestamp,
            bytes_on_disk=path.stat().st_size,
        )

    def list_days(
        self,
        exchange: Exchange,
        symbol: str,
    ) -> list[date]:
        """저장된 모든 일자를 오름차순 반환."""
        d = self._symbol_dir(exchange, symbol)
        if not d.exists():
            return []
        days: list[date] = []
        for f in d.glob("*.pkl"):
            try:
                days.append(date.fromisoformat(f.stem))
            except ValueError:
                continue
        days.sort()
        return days

    # ------------------------------------------------------------------
    # Retention / stats
    # ------------------------------------------------------------------

    def prune(self, older_than_days: int | None = None) -> int:
        """retention_days 이상 오래된 파일 삭제.

        Args:
            older_than_days: 명시하지 않으면 self.retention_days.

        Returns:
            삭제된 파일 수.
        """
        cutoff_days = (
            older_than_days if older_than_days is not None else self._retention_days
        )
        if cutoff_days < 0:
            raise ValueError("older_than_days must be >= 0")

        today = date.today()
        deleted = 0
        for exchange_dir in self._root.iterdir():
            if not exchange_dir.is_dir():
                continue
            for symbol_dir in exchange_dir.iterdir():
                if not symbol_dir.is_dir():
                    continue
                for pkl in symbol_dir.glob("*.pkl"):
                    try:
                        d = date.fromisoformat(pkl.stem)
                    except ValueError:
                        continue
                    if (today - d).days > cutoff_days:
                        pkl.unlink()
                        deleted += 1
        if deleted:
            logger.info("TickVault prune: %d files deleted", deleted)
        return deleted

    def stats(self) -> dict[str, object]:
        """저장소 현황 요약."""
        total_files = 0
        total_bytes = 0
        symbols: set[tuple[str, str]] = set()
        for exchange_dir in self._root.iterdir():
            if not exchange_dir.is_dir():
                continue
            for symbol_dir in exchange_dir.iterdir():
                if not symbol_dir.is_dir():
                    continue
                symbols.add((exchange_dir.name, symbol_dir.name))
                for pkl in symbol_dir.glob("*.pkl"):
                    total_files += 1
                    total_bytes += pkl.stat().st_size
        return {
            "root_dir": str(self._root),
            "retention_days": self._retention_days,
            "flush_interval": self._flush_interval,
            "total_files": total_files,
            "total_bytes": total_bytes,
            "symbol_count": len(symbols),
            "buffered_keys": len(self._buffers),
            "buffered_ticks": sum(len(v) for v in self._buffers.values()),
        }

    # ------------------------------------------------------------------
    # Context manager support (세션 종료 시 자동 flush)
    # ------------------------------------------------------------------

    def __enter__(self) -> "TickVault":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.flush_all()


__all__ = ["TickVault"]
