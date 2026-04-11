# Luxon Terminal — 1인 AI 헤지펀드 × AaaS 퀀트 OS

> 🎯 **"블룸버그 × 팔란티어 × 헤지펀드 Top-Tier 급 1인 운용 + SaaS 판매 듀얼 비즈니스"**

**코드네임:** Luxon Terminal v0.1
**기반:** `open-trading-api` v0.3α (730+ pytest, 15K+ LOC)
**플랜 파일:** `C:\Users\lch68\.claude\plans\valiant-honking-simon.md` (~2,500 라인)

---

## 📦 Sprint 1 — FRED Quick Win (완료)

거시 10지표를 Nexus MCP(`fred_get_series`)에서 실시간 수집하여 다크테마 대시보드로 표시.

### ✨ 특징

- **MCP 우선** — `MCPDataProvider._call_vps_tool("fred_get_series", ...)` 주 경로, **FRED API 키 불필요**
- **Parquet 캐시** — `~/.luxon/cache/fred/` 기본, 6시간 TTL
- **10 거시 지표** — DGS10/DGS2/T10Y2Y/DFF/CPIAUCSL/UNRATE/M2SL/VIXCLS/DCOILWTICO/DEXKOUS
- **다크 대시보드** — matplotlib 2×5 그리드, PNG + HTML 출력
- **Staleness 감지** — daily 5영업일 / monthly 45일 / quarterly 100일 임계치
- **실데이터 절대 원칙** — 목업 생성 금지, 빈 응답 시 예외

### 🚀 사용법

#### 빠른 실행 (스모크 스크립트)

```bash
cd C:\Users\lch68\Desktop\open-trading-api\backtester
.venv\Scripts\python.exe scripts/smoke_sprint1.py --out ./out/macro_20260411.png --html ./out/macro_20260411.html
```

결과:
- `./out/macro_20260411.png` — 10 지표 다크 PNG 대시보드
- `./out/macro_20260411.html` — 인터랙티브 HTML (PNG embed + 데이터 테이블)

#### Python API

```python
import asyncio
from pathlib import Path

from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
from kis_backtest.luxon.stream.fred_hub import FREDHub
from kis_backtest.luxon.ui.macro_dashboard import MacroDashboard

async def main():
    mcp = MCPDataProvider()  # ~/.mcp.json에서 토큰 자동 로드
    hub = FREDHub(mcp=mcp)

    # 10개 시리즈 전체 로드
    all_series = await hub.load_all()

    # 다크 대시보드 렌더
    dashboard = MacroDashboard()
    dashboard.render_png(all_series, Path("./out/macro.png"))
    dashboard.render_html(all_series, Path("./out/macro.html"))

    # Staleness 점검
    from kis_backtest.luxon.stream.schema import FredSeriesId
    dgs10 = all_series[FredSeriesId.DGS10]
    report = hub.detect_staleness(dgs10)
    if report.is_stale:
        print(f"⚠️ {dgs10.meta.label_ko} {report.business_days_stale}일 지연")

asyncio.run(main())
```

### 🧪 테스트

```bash
cd C:\Users\lch68\Desktop\open-trading-api\backtester
.venv\Scripts\python.exe -m pytest tests/luxon/ -v
```

- `test_fred_hub.py` — 8 테스트 (mock MCP)
- `test_fred_cache.py` — 4 테스트 (Parquet 라운드트립)
- `test_macro_dashboard.py` — 4 테스트 (렌더링)

**총 16 테스트 + 기존 730+ 회귀 0 유지**

### 📂 파일 구조

```
backtester/kis_backtest/luxon/
├── __init__.py                     # 패키지 진입점
├── README.md                       # ← 이 파일
├── naming_registry_sprint1.md      # Sprint 1 네이밍 레지스트리 (SSOT)
├── stream/                         # Maven 레이어 (실시간 데이터)
│   ├── __init__.py
│   ├── schema.py                   # 공유 타입 SSOT (8 타입)
│   ├── series_registry.yaml        # 10 FRED 시리즈 카탈로그
│   ├── fred_cache.py               # Parquet 캐시 (A3)
│   └── fred_hub.py                 # FREDHub 메인 (A1, MCP 래퍼)
├── ui/                             # UI 레이어
│   ├── __init__.py
│   └── macro_dashboard.py          # matplotlib 2×5 다크 대시보드 (A2)
├── ontology/                       # Gotham 레이어 (Phase 2 예정)
│   └── __init__.py
└── intelligence/                   # AIP 레이어 (Phase 2 예정)
    └── __init__.py
```

### 🏗️ 설계 원칙

1. **기존 자산 무수정** — `portfolio/`, `execution/`, `providers/`, `core/pipeline.py` 수정 금지
2. **MCP 우선** — fredapi 직접 통합 금지, Nexus MCP 398도구 주 경로
3. **실데이터 절대** — 목업/가짜/할루시네이션 금지 (FredSeries.__post_init__에서 강제)
4. **3중 리스크 게이트 우회 불가** — 주문 경로는 RiskGateway → KillSwitch → CapitalLadder (Sprint 1에선 주문 없음)
5. **SSOT 스키마** — `schema.py`와 `series_registry.yaml`이 공유 타입의 단일 진실원
6. **UX 원칙** — 블룸버그 밀도 × Apple 직관성 (플랜 섹션 6.6)

### 🔗 기존 자산 재사용

| Luxon 신규 | 기존 자산 | 재사용 방식 |
|---|---|---|
| `FREDHub` | `MCPDataProvider._call_vps_tool` | MCP 호출 패턴 100% |
| `FREDHub` | `macro_regime._FRED_SERIES_MAP` | 도구 이름 + 시리즈 ID 패턴 |
| `MacroDashboard` | (신규) | `report/themes/` 있으면 팔레트 재사용 예정 |

### 📋 다음 단계 (Sprint 2)

- ECOS Hub 추가 (한국 거시 지표)
- `MacroRegimeDashboard`와 통합 (source="fred" 스위치)
- CUFA 보고서 매크로 섹션에 FRED 데이터 주입

---

## 📦 Sprint 3 — TickVault (실시간 틱 저장소)

일별 pickle 파일 기반 경량 틱 저장소. 이름은 Parquet을 유지하되 pyarrow 의존 0, 순수 표준 라이브러리로 동작.

### ✨ 특징

- **일별 pickle 저장** — 심볼당 하루 1파일, 플러시 주기 조절 가능
- **Exchange 추상화** — KIS/Upbit 공통 `TickPoint` 스키마
- **Context manager** — `with TickVault() as v:` 자동 flush
- **Replay 지원** — 동기/비동기 재생, `speed`/`offset`/`limit` 옵션
- **Retention 정책** — `prune()` 호출로 오래된 파일 자동 삭제
- **Phase 4에서 ClickHouse로 교체 가능** — 인터페이스만 유지하면 백엔드 스왑 OK

### 🆕 새 모듈

| 파일 | 설명 |
|---|---|
| `stream/tick_vault.py` | `TickVault` 클래스 (append/flush/load_day/prune/context manager) |
| `stream/kis_tick_tap.py` | `KISTickTap` (KIS WebSocket → TickPoint 콜백 어댑터) |
| `stream/upbit_tick_tap.py` | `UpbitTickTap` (Upbit async generator → TickPoint) |
| `stream/replay.py` | `TickReplayer` (동기/비동기 재생, speed/offset/limit) |
| `stream/schema.py` 확장 | `TickPoint`, `TickMeta`, `Exchange`, `ReplaySpec` 타입 추가 |

### 📁 경로 규약

```
~/.luxon/data/ticks/{exchange}/{symbol}/{YYYY-MM-DD}.pkl
```

### 🔧 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LUXON_TICK_DATA_DIR` | `~/.luxon/data/ticks` | 틱 저장 루트 |
| `LUXON_TICK_RETENTION_DAYS` | `30` | 보존 기간 (일) |
| `LUXON_TICK_FLUSH_INTERVAL` | `5.0` | 자동 flush 주기 (초) |

### 🚀 사용 예시

```python
from kis_backtest.luxon.stream.tick_vault import TickVault
from kis_backtest.luxon.stream.upbit_tick_tap import UpbitTickTap
from kis_backtest.providers.upbit.websocket import UpbitWebSocket

vault = TickVault()
ws = UpbitWebSocket()
tap = UpbitTickTap(vault, ws)
await tap.run(codes=["KRW-BTC"], message_type="trade", duration_seconds=60)
vault.flush_all()
```

### ✅ Sprint 3 DoD 체크리스트

- [ ] `TickVault` 5 메서드 구현 + 컨텍스트 매니저 지원
- [ ] KIS/Upbit Tap 2종 각 async 런 검증
- [ ] `TickReplayer` 동기/비동기 양쪽 경로 단위 테스트
- [ ] 환경 변수 3종 override 테스트
- [ ] Retention `prune()` 일자 경계 테스트
- [ ] 기존 730+ 회귀 0 유지
- [ ] 네이밍 레지스트리: [`luxon/naming_registry_sprint3.md`](./naming_registry_sprint3.md)

### 🛡️ 수정 금지 영역

Sprint 3 작업 범위 밖 — **절대 손대지 말 것**:

- `providers/kis/` — KIS WebSocket/REST 원본
- `providers/upbit/` — Upbit WebSocket/REST 원본
- `execution/*` — 주문 실행 레이어 전체
- `core/pipeline.py` — QuantPipeline 코어

---

## 🗺️ 전체 로드맵 (Phase 1~7 / Sprint 1~30.5)

- **Phase 1 재료 준비** (Sprint 1-4) — Data Foundation ← **현재**
- **Phase 2 초벌 조리** (Sprint 5-7) — Ontology + Intelligence
- **Phase 3 플레이팅** (Sprint 8-10) — UI 킬러 데모
- **Phase 4 글로벌 소싱** (Sprint 11-13) — CCXT/Tavily/Factor Zoo
- **Phase 5 간 맞추기** (Sprint 14-17) — Risk/Quality/Attribution
- **Phase 6 그랜드 오픈** (Sprint 18-24) — Hedge Fund Inc
- **Phase 7 상용화** (Sprint 25-30) — AaaS 배포 + 판매

**완성본:** Luxon Terminal AaaS — 1인 AI 헤지펀드가 운용하며 동시에 전 세계에 판매하는 듀얼 비즈니스 퀀트 OS 플랫폼.

---

## 📝 라이선스 / 기여

- 현재 비공개 (Phase 7 Tier 0에서 오픈소스 공개 예정)
- 작성자: 이찬희 (Luxon AI 창업자, CUFA 회장)
- 참조: [플랜 마스터 파일](C:\Users\lch68\.claude\plans\valiant-honking-simon.md)
