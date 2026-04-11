# Sprint 1 — Luxon Terminal Naming Registry (SSOT)

**목적:** 6-에이전트 병렬 개발 시 식별자 충돌 방지 (플랜 섹션 13.4.2 규칙 2).
**규칙:** 이 파일에 없는 클래스/함수/파일명을 에이전트가 생성하면 A7 감사에서 자동 reject.
**변경 권한:** Luxon 본인 또는 A5(Schema Guard).
**참조:** `C:\Users\lch68\.claude\plans\valiant-honking-simon.md` 섹션 8, 13.4

---

## 1. 파일 경로 계약 (2026-04-11 수정 — MCP 우선 + 실제 tests 경로 반영)

### 1.1 주요 변경 (찬희 지시 반영)
- **FRED API 키 불필요** — MCP `fred_get_series` 도구 주 경로, fredapi 직접 삭제
- **tests 경로 정정** — `backtester/kis_backtest/tests/luxon/` ❌ → **`backtester/tests/luxon/`** ✅
- **A3 역할 재정의** — MCP Fallback → **FredCache (Parquet 전담)**
- **이번 세션은 Claude 직접 작성** — 6-에이전트 병렬 스폰은 Sprint 5 이후 적용

### 1.2 파일 경로

| Agent / 역할 | 수정 허용 경로 | 비고 |
|---|---|---|
| **T0 (Luxon)** | `luxon/__init__.py`, `luxon/stream/__init__.py`, `luxon/stream/schema.py`, `luxon/stream/series_registry.yaml`, `luxon/ui/__init__.py`, `luxon/naming_registry_sprint1.md` | 선행 작업, SSOT 확정 |
| **A1 (FREDHub Core)** | `luxon/stream/fred_hub.py` | MCP 래퍼 + staleness (캐시는 A3 분리) |
| **A2 (MacroDashboard)** | `luxon/ui/macro_dashboard.py` | matplotlib 2×5 + HTML |
| **A3 (FredCache)** | `luxon/stream/fred_cache.py` | Parquet 캐시 + TTL 관리 (A3 역할 재정의) |
| **A4 (Test Author)** | `backtester/tests/luxon/__init__.py`, `backtester/tests/luxon/conftest.py`, `backtester/tests/luxon/test_fred_hub.py`, `backtester/tests/luxon/test_macro_dashboard.py`, `backtester/tests/luxon/test_fred_cache.py` | 15+ 테스트 |
| **A5 (Schema Guard)** | `luxon/stream/schema.py`, `luxon/stream/series_registry.yaml`, `luxon/naming_registry_sprint1.md` | SSOT 거버넌스만 |
| **A6 (Docs + Smoke)** | `luxon/README.md`, `backtester/scripts/smoke_sprint1.py` | 사용법 + 수동 스모크 |
| **A7 (Auditor)** | 수정 없음, 읽기 전용 감사 | diff 리뷰 + naming 체크 |

**금지:** 자기 할당 경로 외 파일 생성/수정 → 감사 reject.

## 2. 클래스명 (허용 목록, 다른 이름 생성 금지)

### schema.py (T0 선행 작성 완료)
- `FredSeriesId` — Enum (10개 시리즈 ID)
- `TransformType` — Enum (none/pct_change_yoy/diff)
- `SeriesCategory` — Enum (rates/inflation/labor/liquidity/risk/commodity/fx)
- `FredSeriesMeta` — frozen dataclass (레지스트리 메타)
- `FredPoint` — frozen dataclass (단일 관측)
- `FredSeries` — dataclass (완전한 시리즈 데이터)
- `CacheEntry` — frozen dataclass (캐시 메타)
- `StalenessReport` — frozen dataclass (staleness 점검)

### fred_hub.py (A1)
- `FREDHub` — 메인 클래스
- `FREDCache` — Parquet 캐시 관리
- `FredSeriesRegistry` — YAML 로더 (레지스트리 파싱)

### fred_cache.py (A3 — 역할 재정의)
- `FREDCache` — Parquet 기반 로컬 캐시 (TTL 관리, staleness 감지)

### macro_dashboard.py (A2)
- `MacroDashboard` — 메인 렌더러

## 3. 주요 함수 시그니처 (허용)

### FREDHub (A1) — MCP 기반 (2026-04-11 수정)
- `async load_series(series_id: FredSeriesId, force_refresh: bool = False) -> FredSeries`
- `async load_many(series_ids: list[FredSeriesId] | None = None) -> dict[FredSeriesId, FredSeries]`
- `async load_all() -> dict[FredSeriesId, FredSeries]`  # 레지스트리 10개 전체
- `detect_staleness(series: FredSeries) -> StalenessReport`
- `close() -> None`  # MCP 세션 정리
- `__init__(self, mcp: MCPDataProvider, cache: FREDCache | None = None, registry: FredSeriesRegistry | None = None)`

### FredSeriesRegistry (A1)
- `load_registry(yaml_path: Path | None = None) -> FredSeriesRegistry`  # classmethod
- `get_meta(series_id: FredSeriesId) -> FredSeriesMeta`
- `all_series() -> list[FredSeriesMeta]`

### FREDCache (A3) — Parquet 전담
- `get(series_id: FredSeriesId) -> FredSeries | None`
- `put(series: FredSeries) -> CacheEntry`
- `clear(series_id: FredSeriesId | None = None) -> int`
- `is_expired(entry: CacheEntry) -> bool`
- `__init__(self, cache_dir: Path | None = None, ttl_hours: int = 6)`

### MacroDashboard (A2)
- `render_png(data: dict[FredSeriesId, FredSeries], out_path: Path) -> Path`
- `render_html(data: dict[FredSeriesId, FredSeries], out_path: Path) -> Path`
- `__init__(self, theme: str = "dark")`

## 4. 환경 변수 (2026-04-11 수정 — MCP 우선)

- **`FRED_API_KEY` 불필요** — MCP Nexus `fred_get_series` 도구가 주 경로 (찬희 지시)
- `NEXUS_MCP_TOKEN` — MCPDataProvider가 `~/.mcp.json`에서 자동 로드 (기존 로직)
- `LUXON_CACHE_DIR` — 선택 (기본: `~/.luxon/cache/fred/`)
- `LUXON_FRED_TTL_HOURS` — 선택 (기본: 6)

## 5. 금지 사항 (명시적)

- ❌ 동의어 클래스 생성: `LuxonFredHub`, `FredDataFetcher`, `FredClient`, `FredFetcher` 등 금지 → `FREDHub`만 사용
- ❌ `MacroChart`, `MacroViz`, `DashboardRenderer`, `FredDashboard` 금지 → `MacroDashboard`만
- ❌ schema.py 외에 새 dataclass 정의 금지 → 기존 타입 재사용
- ❌ 중복 클래스/함수 정의 (git grep 감사)
- ❌ 파일 크기 800줄 초과
- ❌ 목업/가짜 데이터 (실데이터 절대 원칙)
- ❌ `execution/`, `providers/`, `core/pipeline.py` 수정
- ❌ 기존 730+ 테스트 깨뜨리기

## 6. 필수 재사용 (중복 구현 금지)

### A1 FREDHub (최소 3개 재사용)
- `backtester.kis_backtest.portfolio.mcp_data_provider.MCPDataProvider` — 폴백 경유
- `backtester.kis_backtest.portfolio.macro_regime` — 시리즈 ID 참고
- `backtester.kis_backtest.report.themes` (있다면) — 다크 팔레트

### A2 MacroDashboard
- matplotlib만 (bokeh/plotly 추가 라이브러리 금지)
- `report/themes/` 다크 팔레트 재사용

### A3 FredMCPFallback
- `MCPDataProvider.get_risk_free_rate_sync()` 또는 동등 MCP 호출 100%

## 7. 테스트 계약 (A4 담당, 총 15+)

### test_fred_hub.py (8 테스트 — MCP 기반)
1. `test_load_series_calls_mcp_fred_get_series` (mock MCP)
2. `test_registry_resolution_dgs10_returns_correct_meta`
3. `test_cache_hit_skips_mcp_call` (2번째 호출 시 cache 확인)
4. `test_yoy_transform_applied_when_registry_says_so` (CPIAUCSL)
5. `test_mcp_error_raises_clear_runtime_error`
6. `test_load_many_returns_all_10_series` (mock)
7. `test_detect_staleness_flags_monthly_series_after_35_days`
8. `test_force_refresh_bypasses_cache`

### test_fred_cache.py (4 테스트 — A3 역할 재정의)
1. `test_put_and_get_roundtrip_parquet`
2. `test_cache_expiration_after_ttl`
3. `test_clear_removes_specific_series`
4. `test_empty_cache_returns_none`

### test_macro_dashboard.py (4 테스트)
1. `test_render_png_produces_valid_file`
2. `test_render_html_contains_10_subplot_ids`
3. `test_dark_theme_background_color`
4. `test_footer_shows_last_observation_date`

**DoD:** 16 테스트 그린 + 기존 회귀 0 (integration 마커 제외 기본 실행)

## 8. 데모 시나리오 (A6 smoke 스크립트)

```bash
export FRED_API_KEY=<찬희 키>
python scripts/smoke_sprint1.py --out ./out/macro_20260411.png --html ./out/macro_20260411.html
# → 10개 시리즈 로드 + 2×5 다크 대시보드 PNG + 인터랙티브 HTML 생성
# → 콘솔에 각 시리즈 마지막 관측일 출력
# → staleness 경고 (있으면) 출력
```

## 9. A7 감사 체크리스트

- [ ] naming_registry에 없는 식별자 존재 여부 (`git grep`)
- [ ] `class FREDHub`, `class MacroDashboard`, `class FredMCPFallback` 각 1회만
- [ ] `from backtester.kis_backtest.portfolio` import 존재 (재사용 의무)
- [ ] schema.py 수정자 A5만 (git log 확인)
- [ ] 파일 크기 800줄 이하
- [ ] pytest 전체 그린
- [ ] 실데이터 스모크 성공
- [ ] `execution/`, `providers/` 수정 0건
- [ ] 한국어 docstring, AAA 패턴 테스트

---

**작성:** 2026-04-11 (Sprint 1 T0 선행 작업)
**다음 단계:** T1 — 6-에이전트 병렬 스폰 (A1~A6)
