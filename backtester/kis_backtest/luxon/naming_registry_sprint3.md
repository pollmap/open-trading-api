# Luxon Sprint 3 — Naming Registry (TickVault)

**Status:** Authoritative. 이 문서와 충돌하는 코드는 A5(Schema Guard)가 reject.
**Scope:** Sprint 3 범위 내 신규 파일. 기존 `providers/kis`, `providers/upbit`,
`execution/*`, `core/pipeline.py`는 **절대 수정 금지**.

---

## 1. 모듈 경로 계약

| 역할 | 파일 경로 | 클래스/함수 | 비고 |
|------|----------|------------|------|
| SSOT 타입 | `kis_backtest/luxon/stream/schema.py` | `Exchange`, `TickPoint`, `TickMeta`, `ReplaySpec` | Sprint 3에서 **확장만**, 기존 FRED 타입 변경 금지 |
| 저장소 | `kis_backtest/luxon/stream/tick_vault.py` | `TickVault` | pickle 기반, fred_cache.py 패턴 재사용 |
| KIS Tap | `kis_backtest/luxon/stream/kis_tick_tap.py` | `KISTickTap`, `kis_realtime_price_to_tick()` | `providers.kis.websocket.RealtimePrice` → `TickPoint` 변환 후 Vault 저장 |
| Upbit Tap | `kis_backtest/luxon/stream/upbit_tick_tap.py` | `UpbitTickTap`, `upbit_trade_msg_to_tick()` | `UpbitWebSocket.subscribe("trade", ...)` 메시지 → `TickPoint` |
| Replay | `kis_backtest/luxon/stream/replay.py` | `TickReplayer` | 동기 iterator + async iterator 2중 인터페이스 |
| Unit 테스트 | `backtester/tests/luxon/test_tick_vault.py` | `test_append_*`, `test_load_day_*`, `test_retention_*` | pytest unit |
| Unit 테스트 | `backtester/tests/luxon/test_replay.py` | `test_replay_*` | pytest unit |
| 스모크 | `backtester/scripts/smoke_sprint3.py` | `__main__` 실행형 | 5분 실수집 |

## 2. 경로/파일명 규약 (디스크 레이아웃)

```
~/.luxon/data/ticks/
  kis/
    005930/
      2026-04-11.pkl          ← 당일 수집된 틱 (append-only)
      2026-04-10.pkl
    000660/
      2026-04-11.pkl
  upbit/
    KRW-BTC/
      2026-04-11.pkl
```

- **일별 파일**: UTC 자정 기준이 아니라 **KST(Asia/Seoul) local date** — 한국 증시 스케줄과 일치
- **디렉토리 자동 생성**: `TickVault.__init__`에서 `mkdir(parents=True, exist_ok=True)`
- **파일 포맷**: pickle (의존성 0, pyarrow 없음)
- **bundle 구조** (`_TICK_BUNDLE_VERSION = 1`):
  ```python
  {
      "version": 1,
      "exchange": "kis",
      "symbol": "005930",
      "day": "2026-04-11",
      "ticks": list[TickPoint],       # frozen dataclass 그대로 직렬화
      "first_timestamp": isoformat str,
      "last_timestamp": isoformat str,
  }
  ```

## 3. 환경 변수 (env override)

| 변수 | 기본값 | 용도 |
|------|--------|------|
| `LUXON_TICK_DATA_DIR` | `~/.luxon/data/ticks` | 저장 루트 디렉토리 override |
| `LUXON_TICK_RETENTION_DAYS` | `90` | `TickVault.prune()` 기본 보관 일수 |
| `LUXON_TICK_FLUSH_INTERVAL` | `50` | 내부 버퍼가 몇 틱마다 디스크에 flush 되는지 |

## 4. 재사용 의무 (중복 구현 금지)

| 참조 대상 | 재사용 방식 |
|----------|------------|
| `fred_cache._default_cache_dir()` 패턴 | `_default_tick_dir()`로 복제 (독립 env 변수) |
| `fred_cache.FREDCache._cache_path()` | `TickVault._tick_path(exchange, symbol, day)` |
| `fred_cache.FREDCache.get()` | `TickVault.load_day(exchange, symbol, day)` — pickle 로드 + 버전 검증 |
| `execution.fill_tracker.FillTracker.register → on_fill → reconcile` | `KISTickTap.start → on_price → flush`와 동일 tap 패턴 |
| `providers.kis.websocket.KISWebSocket.subscribe_price` | **호출만 함. 내부 수정 절대 금지.** 콜백으로 TickVault에 저장 |
| `providers.upbit.websocket.UpbitWebSocket.subscribe` | async generator로 소비. 내부 수정 금지 |

## 5. API 시그니처 (구현 계약)

```python
class TickVault:
    def __init__(
        self,
        root_dir: Path | None = None,
        retention_days: int | None = None,
        flush_interval: int | None = None,
    ) -> None: ...

    def append(self, tick: TickPoint) -> None:
        """단일 틱 버퍼 추가 + 주기적 flush."""

    def flush(self, exchange: Exchange, symbol: str) -> TickMeta | None:
        """특정 (exchange, symbol) 버퍼를 당일 파일에 즉시 flush."""

    def flush_all(self) -> list[TickMeta]:
        """전체 버퍼 flush."""

    def load_day(self, exchange: Exchange, symbol: str, day: date) -> list[TickPoint]: ...

    def describe(self, exchange: Exchange, symbol: str, day: date) -> TickMeta | None: ...

    def list_days(self, exchange: Exchange, symbol: str) -> list[date]: ...

    def prune(self, older_than_days: int | None = None) -> int:
        """retention_days보다 오래된 파일 삭제. 반환: 삭제 개수."""

    def stats(self) -> dict[str, object]: ...


class TickReplayer:
    def __init__(self, vault: TickVault) -> None: ...

    def replay(
        self,
        exchange: Exchange,
        symbol: str,
        day: date,
        spec: ReplaySpec | None = None,
    ) -> Iterator[TickPoint]:
        """동기 재생 iterator. spec.speed에 따라 time.sleep."""

    async def replay_async(
        self,
        exchange: Exchange,
        symbol: str,
        day: date,
        spec: ReplaySpec | None = None,
    ) -> AsyncIterator[TickPoint]:
        """async 재생. asyncio.sleep."""
```

## 6. 금지 사항 (Sprint 3)

- ❌ ClickHouse / Kafka / Redis 의존성 추가
- ❌ `pyarrow`, `fastparquet` 추가 — Sprint 1.5에서 제거한 이유 존중
- ❌ `providers/kis`, `providers/upbit` 파일 **한 글자도 수정 금지**
- ❌ `execution/*` 수정 금지 (fill_tracker는 read-only reference)
- ❌ `core/pipeline.py` 수정 금지
- ❌ 6-에이전트 병렬 스폰 (Claude 메인이 직접 작성, 컨텍스트 절약)
- ❌ 실 주문 경로 우회 — Sprint 3은 **데이터 수집/재생만**

## 7. DoD (Done 기준)

- [x] schema.py 확장 (T0-1)
- [x] naming_registry_sprint3.md (T0-2)
- [ ] `tick_vault.py` 구현 + pytest 5+
- [ ] `kis_tick_tap.py` 구현 + 단위 테스트
- [ ] `upbit_tick_tap.py` 구현 + 단위 테스트
- [ ] `replay.py` 구현 + pytest 3+
- [ ] 기존 742 회귀 0 실패
- [ ] 신규 pytest 8+ green
- [ ] smoke_sprint3.py 실행 시 최소 1개 `.pkl` 생성 확인 (네트워크 가용 시)
- [ ] git commit "feat(sprint3): TickVault Parquet-style tick store"
- [ ] 핸드오프 메모리 파일 갱신

---

**Why:** Sprint 3은 Phase 1의 마지막 재료 준비 단계. 여기서 네이밍·경로가 틀리면
Phase 4 ClickHouse 마이그레이션 시 전체 replay 인프라를 재작성해야 함.
계약을 먼저 박고 구현을 맞추는 순서를 엄수한다.

**How to apply:** 새 파일 생성 전 이 문서의 표를 먼저 확인하고, 충돌 시 구현이 아닌
이 문서를 수정 → Schema Guard가 diff 승인 → 그 다음 코드.
