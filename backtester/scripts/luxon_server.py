"""
Luxon Live Dashboard Server — 실시간 대시보드 (포트 7777).

로컬 nexus-finance-mcp (127.0.0.1:8100) 연결 → 실데이터 자동 갱신.

사용:
    python scripts/luxon_server.py
    python scripts/luxon_server.py 005930 000660 035420
    python scripts/luxon_server.py --port 7777 --refresh 30

접속: http://localhost:7777

데이터 흐름:
    MCPDataProvider(127.0.0.1:8100)
        ├─ health_check         → MCP 상태
        ├─ ta_rsi/macd/bb       → TA 신호 (TASignalIngestor)
        ├─ get_stock_returns     → 실 일간수익률 → PnL 곡선
        └─ refresh_macro        → 매크로 레짐 (신뢰도 갱신)
    LuxonOrchestrator.run_workflow() → 포트폴리오 결정
    fills/paper/*.json           → 모의매매 기록
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

# ── 경로 주입 ─────────────────────────────────────────────────────────
BACKTESTER = Path(__file__).resolve().parent.parent
if str(BACKTESTER) not in sys.path:
    sys.path.insert(0, str(BACKTESTER))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("kis_backtest.portfolio.mcp_data_provider").setLevel(logging.ERROR)
log = logging.getLogger("luxon.server")

# LuxonTerminal._paper_record() 이 저장하는 경로와 동기화
_LUXON_HOME = Path.home() / ".luxon"
FILL_DIR = _LUXON_HOME / "fills" / "paper"
TICKET_DIR = BACKTESTER / "tickets" / "hourly"  # 티켓은 프로젝트 내

DEFAULT_SYMBOLS = ["005930", "000660", "035420", "373220", "207940"]
TICKER_NAMES = {
    "005930": "삼성전자", "000660": "SK하이닉스", "035420": "NAVER",
    "373220": "LG에솔", "207940": "삼바", "035720": "카카오",
    "068270": "셀트리온", "105560": "KB금융", "000270": "기아", "005380": "현대차",
}

LOCAL_MCP_HOST = os.environ.get("MCP_LOCAL_HOST", "127.0.0.1:8100")


# ── MCP 초기화 (로컬 우선) ────────────────────────────────────────────

def _init_mcp():
    """127.0.0.1:8100 로컬 MCP 연결. 실패 시 VPS fallback."""
    from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider

    # 1) 로컬 MCP 시도
    try:
        mcp = MCPDataProvider(vps_host=LOCAL_MCP_HOST, vps_token="")
        health = mcp.health_check_sync()
        if health.get("status") == "ok":
            log.info("MCP 연결: 로컬 %s (tool_count=%s)", LOCAL_MCP_HOST, health.get("tool_count"))
            return mcp, "local"
    except Exception as e:
        log.warning("로컬 MCP 실패: %s", e)

    # 2) VPS fallback
    try:
        mcp = MCPDataProvider()
        health = mcp.health_check_sync()
        if health.get("status") == "ok":
            log.info("MCP 연결: VPS fallback")
            return mcp, "vps"
    except Exception as e:
        log.warning("VPS MCP 실패: %s", e)

    log.warning("MCP 전체 실패 — 로컬 모드")
    return None, "offline"


# ── 데이터 수집 ──────────────────────────────────────────────────────

def _load_fills(n: int = 20) -> list[dict]:
    fills = []
    if FILL_DIR.exists():
        for f in sorted(FILL_DIR.glob("*.json"), reverse=True)[:n]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                fills.extend(data if isinstance(data, list) else [data])
            except Exception:
                pass
    return fills[:n]


def _load_tickets(n: int = 10) -> list[dict]:
    tickets = []
    if TICKET_DIR.exists():
        for f in sorted(TICKET_DIR.glob("*.json"), reverse=True)[:n]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                items = data if isinstance(data, list) else [data]
                for it in items:
                    if isinstance(it, dict):
                        it["_source"] = f.stem
                        tickets.append(it)
            except Exception:
                pass
    return tickets[:n]


def _fetch_pnl(mcp, symbols: list[str]) -> tuple[list[str], list[float]]:
    """실 주가 데이터로 포트폴리오 PnL 곡선 계산.

    - 각 종목 일간수익률 평균 → 누적 복리 계산
    - 실패 시 합성 데이터 fallback
    """
    import random
    if mcp is None:
        random.seed(42)
        base = 0.0
        labels, values = [], []
        for i in range(60):
            base += random.gauss(0.05, 0.9)
            labels.append(f"D{i+1:02d}")
            values.append(round(base, 2))
        return labels, values

    # 실 수익률 데이터
    all_returns: dict[str, list[float]] = {}
    for sym in symbols:
        try:
            rets = mcp.get_stock_returns_sync(sym)
            if rets and len(rets) >= 20:
                all_returns[sym] = list(rets[-90:])  # 최근 90일
                log.info("PnL 실데이터: %s %d일", sym, len(all_returns[sym]))
        except Exception as e:
            log.debug("PnL fetch 실패 %s: %s", sym, e)

    if not all_returns:
        # fallback
        import random
        random.seed(99)
        base = 0.0
        labels, values = [], []
        for i in range(60):
            base += random.gauss(0.04, 0.8)
            labels.append(f"D{i+1:02d}")
            values.append(round(base, 2))
        return labels, values

    # 가장 짧은 시리즈에 맞춰 정렬
    min_len = min(len(v) for v in all_returns.values())
    # 동일가중 평균 수익률
    avg_returns = [
        sum(all_returns[sym][i] for sym in all_returns) / len(all_returns)
        for i in range(min_len)
    ]
    # 누적 PnL (%)
    cum = 0.0
    values = []
    for r in avg_returns:
        cum += r * 100
        values.append(round(cum, 2))

    n = len(values)
    today = datetime.now().toordinal()
    labels = [
        datetime.fromordinal(today - n + i + 1).strftime("%m/%d")
        for i in range(n)
    ]
    return labels, values


def _fetch_ta_signals(mcp, symbols: list[str]) -> list[dict]:
    if mcp is None:
        return []
    try:
        from kis_backtest.luxon.graph.graph import GothamGraph
        from kis_backtest.portfolio.catalyst_tracker import CatalystTracker
        from kis_backtest.luxon.graph.ingestors.ta_signal_ingestor import TASignalIngestor
        result = TASignalIngestor(GothamGraph(), CatalystTracker()).ingest_sync(mcp, symbols)
        signals = []
        for sym, sigs in result.items():
            for sig in sigs:
                signals.append({
                    "symbol": sym, "name": TICKER_NAMES.get(sym, sym),
                    "source": sig.source, "signal": sig.name,
                    "impact": sig.impact, "probability": sig.probability,
                })
        return signals
    except Exception as e:
        log.warning("TA 신호 fetch 실패: %s", e)
        return []


# ── DataService (백그라운드 갱신) ────────────────────────────────────

class DataService:
    """30초마다 백그라운드에서 데이터 갱신. 스레드 안전 캐시."""

    def __init__(self, symbols: list[str], capital: float, refresh_secs: int):
        self.symbols = symbols
        self.capital = capital
        self.refresh_secs = refresh_secs
        self._lock = threading.RLock()
        self._data: dict = {"loading": True}
        self._mcp = None
        self._mcp_mode = "offline"
        self._orch = None
        self._stop = threading.Event()

    def start(self) -> None:
        """초기 데이터 로드 후 백그라운드 스레드 시작."""
        self._init()
        self._refresh()
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()
        log.info("DataService 시작 (갱신주기: %ds)", self.refresh_secs)

    def stop(self) -> None:
        self._stop.set()

    def get(self) -> dict:
        with self._lock:
            return dict(self._data)

    def _init(self) -> None:
        self._mcp, self._mcp_mode = _init_mcp()
        from kis_backtest.luxon.orchestrator import LuxonOrchestrator
        self._orch = LuxonOrchestrator(
            mcp=self._mcp,
            total_capital=self.capital,
        )

    def _refresh(self) -> None:
        log.info("데이터 갱신 중...")
        try:
            data = self._build()
            with self._lock:
                self._data = data
            log.info("갱신 완료 (regime=%s, signals=%d)",
                     data.get("regime", "?"), len(data.get("ta_signals", [])))
        except Exception as e:
            log.error("갱신 실패: %s", e)

    def _loop(self) -> None:
        while not self._stop.wait(self.refresh_secs):
            self._refresh()

    def _build(self) -> dict:
        convictions = {s: 5.0 for s in self.symbols}

        # 매크로 레짐 갱신 (MCP 있을 때)
        if self._mcp:
            try:
                import asyncio
                asyncio.run(self._orch.refresh_macro())
            except Exception:
                pass

        # 포트폴리오 분석
        report = None
        try:
            report = self._orch.run_workflow(self.symbols, base_convictions=convictions)
        except Exception as e:
            log.warning("run_workflow 실패: %s", e)

        # 포트폴리오 rows
        portfolio_rows = []
        allocation_labels, allocation_values = [], []
        cat_labels, cat_values = [], []

        if report:
            decisions = {d.symbol: d for d in report.portfolio.decisions}
            sizes = {ps.symbol: ps for ps in report.position_sizes}
            for sym in self.symbols:
                d = decisions.get(sym)
                ps = sizes.get(sym)
                name = TICKER_NAMES.get(sym, sym)
                action = "N/A"
                conviction = cat_score = weight_pct = 0.0
                amount = 0
                if d:
                    action = d.action.upper() if hasattr(d, "action") else str(d.decision)
                    conviction = float(getattr(d, "conviction_score", 0) or 0)
                    cat_score = float(getattr(d, "catalyst_score", 0) or 0)
                if ps:
                    weight_pct = float(getattr(ps, "weight_pct", 0) or 0)
                    amount = int(getattr(ps, "amount_krw", 0) or 0)
                    if weight_pct > 0:
                        allocation_labels.append(name)
                        allocation_values.append(round(weight_pct, 2))
                cat_labels.append(name)
                cat_values.append(round(cat_score, 2))
                portfolio_rows.append({
                    "symbol": sym, "name": name, "action": action,
                    "conviction": round(conviction, 2),
                    "cat_score": round(cat_score, 2),
                    "weight_pct": round(weight_pct, 2),
                    "amount_만": amount // 10000,
                })

        # TA 신호 (실 MCP)
        ta_signals = _fetch_ta_signals(self._mcp, self.symbols)

        # PnL (실 주가 or 합성)
        pnl_labels, pnl_values = _fetch_pnl(self._mcp, self.symbols)

        # 페이퍼 Fill + 티켓
        fills = _load_fills(12)
        tickets = _load_tickets(8)

        # 레짐: OrchestrationReport > dashboard.classify_regime() 폴백 (캐시 포함)
        regime_label = "N/A"
        regime_conf = 0.0
        if report:
            regime_label = getattr(report, "regime", "N/A")
            regime_conf = round(float(getattr(report, "regime_confidence", 0) or 0), 3)
        else:
            try:
                cached = self._orch.dashboard.classify_regime()
                regime_label = cached.regime.value
                regime_conf = round(cached.confidence, 3)
            except Exception:
                pass

        return {
            "generated_at": datetime.now().isoformat(),
            "regime": regime_label,
            "regime_confidence": regime_conf,
            "mcp_mode": self._mcp_mode,
            "mcp_connected": self._mcp is not None,
            "portfolio": portfolio_rows,
            "allocation_labels": allocation_labels,
            "allocation_values": allocation_values,
            "cat_labels": cat_labels,
            "cat_values": cat_values,
            "ta_signals": ta_signals,
            "pnl_labels": pnl_labels,
            "pnl_values": pnl_values,
            "fills": [{k: str(v)[:40] for k, v in f.items()} for f in fills],
            "tickets": [{k: str(v)[:40] for k, v in t.items()} for t in tickets],
        }


# ── HTML Template ─────────────────────────────────────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LUXON TERMINAL v4.1</title>
<link href="https://fonts.googleapis.com/css2?family=VT323&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg0:#050402;--bg1:#0a0806;--bg2:#0f0c08;--bg3:#151008;
  --amber:#d4a017;--amber-lo:#7a5c0a;--amber-hi:#ffd060;
  --green:#39ff14;--green-lo:#1a7a06;
  --red:#ff2200;--red-lo:#7a1000;
  --dim:#4a3a18;--border:#2a1f08;
  --glow:0 0 8px rgba(212,160,23,0.6),0 0 20px rgba(212,160,23,0.2);
  --glow-g:0 0 8px rgba(57,255,20,0.5),0 0 20px rgba(57,255,20,0.15);
  --glow-r:0 0 8px rgba(255,34,0,0.5);
  --mono:'Share Tech Mono',monospace;
  --display:'VT323',monospace;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html,body{height:100%;background:var(--bg0);color:var(--amber);font-family:var(--mono);font-size:11px;overflow:hidden;cursor:crosshair;}
body::before{content:'';position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,0.15) 2px,rgba(0,0,0,0.15) 4px);pointer-events:none;z-index:1000;}
body::after{content:'';position:fixed;inset:0;background:radial-gradient(ellipse at center,transparent 60%,rgba(0,0,0,0.7) 100%);pointer-events:none;z-index:999;}

/* Header */
.hdr{display:grid;grid-template-columns:auto 1fr auto;align-items:center;height:44px;padding:0 16px;background:var(--bg1);border-bottom:1px solid var(--amber-lo);position:relative;z-index:10;}
.hdr-logo{font-family:var(--display);font-size:28px;color:var(--amber-hi);letter-spacing:3px;text-shadow:var(--glow);animation:flicker 8s infinite;}
.hdr-center{display:flex;align-items:center;justify-content:center;gap:24px;}
.hdr-stat{display:flex;flex-direction:column;align-items:center;}
.hsl{font-size:8px;color:var(--dim);letter-spacing:2px;text-transform:uppercase;}
.hsv{font-family:var(--display);font-size:18px;line-height:1;letter-spacing:1px;}
.hsv.a{color:var(--amber-hi);text-shadow:var(--glow);}
.hsv.g{color:var(--green);text-shadow:var(--glow-g);}
.hsv.r{color:var(--red);text-shadow:var(--glow-r);}
.hdr-right{display:flex;flex-direction:column;align-items:flex-end;gap:2px;}
.hdr-time{font-family:var(--display);font-size:20px;color:var(--amber-hi);letter-spacing:2px;text-shadow:var(--glow);}
.hdr-date{font-size:9px;color:var(--dim);letter-spacing:1px;}

/* Refresh indicator */
.refresh-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--dim);margin-left:6px;vertical-align:middle;}
.refresh-dot.pulse{background:var(--green);box-shadow:var(--glow-g);animation:pulse 0.5s ease-out;}
@keyframes pulse{0%{transform:scale(1.8);}100%{transform:scale(1);}}

/* Grid */
.main{display:grid;grid-template-columns:1fr 320px;grid-template-rows:1fr 1fr;gap:1px;background:var(--amber-lo);height:calc(100vh - 44px - 26px);}
.panel{background:var(--bg1);display:flex;flex-direction:column;overflow:hidden;}
.ph{background:var(--bg2);border-bottom:1px solid var(--amber-lo);padding:5px 10px 4px;display:flex;align-items:baseline;gap:10px;flex-shrink:0;}
.ph-title{font-family:var(--display);font-size:16px;color:var(--amber-hi);letter-spacing:2px;text-shadow:var(--glow);}
.ph-sub{font-size:9px;color:var(--dim);letter-spacing:1px;}
.ph-badge{margin-left:auto;font-size:9px;border:1px solid var(--amber-lo);padding:1px 5px;color:var(--dim);letter-spacing:1px;}
.ph-badge.live{border-color:var(--green-lo);color:var(--green);animation:blink 2s step-end infinite;}
@keyframes blink{50%{opacity:0;}}
.pb{flex:1;overflow-y:auto;overflow-x:hidden;padding:6px 10px;}
.pb::-webkit-scrollbar{width:3px;}
.pb::-webkit-scrollbar-thumb{background:var(--amber-lo);}

/* Table */
.tbl{width:100%;border-collapse:collapse;font-size:10.5px;}
.tbl th{text-align:left;padding:2px 6px;color:var(--dim);font-size:8px;letter-spacing:2px;text-transform:uppercase;border-bottom:1px solid var(--bg3);font-weight:normal;}
.tbl td{padding:4px 6px;border-bottom:1px solid var(--bg2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:120px;}
.tbl tr:hover td{background:var(--bg3);}
td.sym{color:var(--amber-hi);font-weight:bold;}
td.num{text-align:right;font-feature-settings:"tnum";}
td.buy{color:var(--green);text-shadow:var(--glow-g);}
td.skip{color:var(--amber);}
td.avoid{color:var(--red);}
td.bull{color:var(--green);}
td.bear{color:var(--red);}
td.dimv{color:var(--dim);}

/* Catalyst bars */
.cat-row{display:flex;align-items:center;gap:8px;padding:3px 0;border-bottom:1px solid var(--bg2);font-size:10px;}
.cat-name{width:60px;color:var(--amber);overflow:hidden;text-overflow:ellipsis;}
.cat-sym{width:52px;color:var(--dim);font-size:9px;}
.cat-bar-wrap{flex:1;height:5px;background:var(--bg3);}
.cat-bar{height:100%;transition:width 1s ease;}
.cat-bar.pos{background:var(--green);box-shadow:var(--glow-g);}
.cat-bar.neg{background:var(--amber-lo);}
.cat-bar.zero{background:var(--red-lo);}
.cat-val{width:34px;text-align:right;font-size:10px;}
.cat-val.bull{color:var(--green);}
.cat-val.bear{color:var(--red);}

/* Signal feed */
.sig-item{display:grid;grid-template-columns:52px 44px 1fr 36px 52px;gap:6px;align-items:center;padding:4px 0;border-bottom:1px solid var(--bg2);font-size:10px;}
.sig-sym{color:var(--amber-hi);}
.sig-src{color:var(--dim);font-size:9px;}
.sig-impact{text-align:right;font-weight:bold;}

/* Fill feed */
.fill-item{display:grid;grid-template-columns:86px 52px 52px 1fr;gap:6px;align-items:center;padding:3px 0;border-bottom:1px solid var(--bg2);font-size:10px;}
.fill-ts{color:var(--dim);font-size:9px;}
.fill-sym{color:var(--amber-hi);}
.fill-ok{color:var(--green);}
.fill-warn{color:var(--amber);}
.fill-fail{color:var(--red);}
.fill-note{color:var(--dim);font-size:9px;overflow:hidden;text-overflow:ellipsis;}

/* Chart */
.chart-pair{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px;height:90px;}
.chart-box{position:relative;}
.chart-full{position:relative;height:calc(100% - 8px);}

/* Section */
.sl{font-size:8px;color:var(--dim);letter-spacing:2px;text-transform:uppercase;margin:6px 0 3px;display:flex;align-items:center;gap:6px;}
.sl::after{content:'';flex:1;height:1px;background:var(--bg3);}
.empty{color:var(--dim);text-align:center;padding:16px;font-size:10px;letter-spacing:1px;}

/* Ticker */
.ticker-wrap{height:26px;background:var(--bg2);border-top:1px solid var(--amber-lo);overflow:hidden;position:relative;z-index:10;}
.ticker-inner{display:flex;align-items:center;height:100%;white-space:nowrap;animation:scroll 50s linear infinite;gap:0;}
@keyframes scroll{from{transform:translateX(100vw);}to{transform:translateX(-100%);}}
.ticker-item{display:inline-flex;align-items:center;gap:8px;padding:0 20px;font-size:10px;color:var(--amber);border-right:1px solid var(--amber-lo);}
.ticker-item .ts{color:var(--amber-hi);font-weight:bold;}
.tval.up{color:var(--green);}
.tval.dn{color:var(--red);}
.ticker-label{position:absolute;left:0;top:0;bottom:0;background:var(--bg2);border-right:1px solid var(--amber-lo);padding:0 10px;display:flex;align-items:center;font-size:8px;color:var(--dim);letter-spacing:2px;z-index:1;}

/* Boot */
@keyframes boot{0%{opacity:0;transform:scaleY(0.02);filter:brightness(4);}10%{opacity:1;transform:scaleY(1);filter:brightness(2);}100%{filter:brightness(1);}}
.hdr,.main,.ticker-wrap{animation:boot 0.7s ease-out both;}
.main{animation-delay:0.05s;}
.ticker-wrap{animation-delay:0.1s;}

@keyframes flicker{0%,96%,98%,100%{opacity:1;}97%{opacity:0.8;}99%{opacity:0.9;}}
::-webkit-scrollbar{width:3px;}
::-webkit-scrollbar-thumb{background:var(--amber-lo);}
</style>
</head>
<body>

<header class="hdr">
  <div class="hdr-logo">▶ LUXON TERMINAL</div>
  <div class="hdr-center">
    <div class="hdr-stat"><span class="hsl">REGIME</span><span class="hsv a" id="h-regime">LOADING</span></div>
    <div class="hdr-stat"><span class="hsl">CONFIDENCE</span><span class="hsv a" id="h-conf">—</span></div>
    <div class="hdr-stat"><span class="hsl">TA SIGNALS</span><span class="hsv g" id="h-sigs">0</span></div>
    <div class="hdr-stat"><span class="hsl">DEPLOYED</span><span class="hsv a" id="h-dep">—</span></div>
    <div class="hdr-stat"><span class="hsl">MCP</span><span class="hsv" id="h-mcp">—</span></div>
    <div class="hdr-stat"><span class="hsl">UPDATED</span><span class="hsv a" id="h-upd" style="font-size:13px">—</span></div>
  </div>
  <div class="hdr-right">
    <div class="hdr-time" id="clock">—</div>
    <div class="hdr-date" id="dateline">—<span class="refresh-dot" id="rdot"></span></div>
  </div>
</header>

<div class="main">
  <!-- ① Portfolio -->
  <div class="panel">
    <div class="ph">
      <span class="ph-title">PORTFOLIO</span>
      <span class="ph-sub">ACKMAN × DRUCKENMILLER ENGINE</span>
      <span class="ph-badge live">● LIVE</span>
    </div>
    <div class="pb">
      <table class="tbl">
        <thead><tr>
          <th style="width:58px">TICKER</th><th style="width:60px">이름</th>
          <th style="width:56px">결정</th><th style="width:36px" class="num">확신</th>
          <th style="width:36px" class="num">CAT</th><th style="width:40px" class="num">비중%</th>
          <th class="num">금액(만)</th>
        </tr></thead>
        <tbody id="port-body"></tbody>
      </table>
      <div class="sl" style="margin-top:8px">CATALYST SCORE</div>
      <div id="cat-bars"></div>
      <div class="chart-pair">
        <div class="chart-box"><canvas id="alloc-chart"></canvas></div>
        <div class="chart-box"><canvas id="score-chart"></canvas></div>
      </div>
    </div>
  </div>

  <!-- ② TA Signals -->
  <div class="panel">
    <div class="ph">
      <span class="ph-title">TA SIGNALS</span>
      <span class="ph-sub">RSI · MACD · BOLLINGER</span>
      <span class="ph-badge" id="sig-badge">0 SIGNALS</span>
    </div>
    <div class="pb" id="sig-body"><div class="empty">[ LOADING... ]</div></div>
  </div>

  <!-- ③ PnL -->
  <div class="panel">
    <div class="ph">
      <span class="ph-title">PNL CURVE</span>
      <span class="ph-sub" id="pnl-sub">실 주가 기반 누적 수익률</span>
    </div>
    <div class="pb">
      <div class="chart-full"><canvas id="pnl-chart"></canvas></div>
    </div>
  </div>

  <!-- ④ Paper Fills -->
  <div class="panel">
    <div class="ph">
      <span class="ph-title">PAPER FILLS</span>
      <span class="ph-sub">모의매매 + 시그널 티켓</span>
      <span class="ph-badge">PAPER MODE</span>
    </div>
    <div class="pb">
      <div class="sl">RECENT FILLS</div>
      <div id="fills-body"></div>
      <div class="sl">SIGNAL TICKETS</div>
      <div id="tickets-body"></div>
    </div>
  </div>
</div>

<div class="ticker-wrap">
  <div class="ticker-label">WATCHLIST</div>
  <div class="ticker-inner" id="ticker"></div>
</div>

<script>
'use strict';

// ── Chart instances (갱신 시 재사용) ────────────────────────────────
let allocChart = null, scoreChart = null, pnlChart = null;

// ── Clock ──────────────────────────────────────────────────────────
(function clock(){
  const pad=n=>String(n).padStart(2,'0');
  setInterval(()=>{
    const n=new Date();
    document.getElementById('clock').textContent=`${pad(n.getHours())}:${pad(n.getMinutes())}:${pad(n.getSeconds())}`;
    document.getElementById('dateline').textContent=`${n.getFullYear()}-${pad(n.getMonth()+1)}-${pad(n.getDate())} KST`;
  },1000);
})();

// ── Fetch & update ─────────────────────────────────────────────────
async function fetchData(){
  try {
    const r = await fetch('/api/data');
    if(!r.ok) throw new Error(r.status);
    const d = await r.json();
    updateDashboard(d);
    // pulse dot
    const dot = document.getElementById('rdot');
    dot.classList.add('pulse');
    setTimeout(()=>dot.classList.remove('pulse'), 600);
  } catch(e){
    console.warn('fetch 실패:', e);
  }
}

function updateDashboard(D){
  // Header
  document.getElementById('h-regime').textContent = D.regime.toUpperCase();
  document.getElementById('h-conf').textContent   = Math.round(D.regime_confidence*100)+'%';
  document.getElementById('h-sigs').textContent   = D.ta_signals.length;
  const dep = D.portfolio.reduce((s,r)=>s+r.amount_만*10000,0);
  document.getElementById('h-dep').textContent = (dep/1e8).toFixed(2)+'억';
  const mEl = document.getElementById('h-mcp');
  if(D.mcp_connected){
    mEl.textContent='ON('+D.mcp_mode+')'; mEl.style.color='var(--green)'; mEl.style.textShadow='var(--glow-g)';
  } else {
    mEl.textContent='OFFLINE'; mEl.style.color='var(--dim)'; mEl.style.textShadow='none';
  }
  document.getElementById('h-upd').textContent = D.generated_at.slice(11,19);

  renderPortfolio(D);
  renderCatBars(D);
  renderCharts(D);
  renderSignals(D);
  renderPnL(D);
  renderFills(D);
  renderTicker(D);
}

// ── Portfolio Table ─────────────────────────────────────────────────
function renderPortfolio(D){
  const b = document.getElementById('port-body');
  b.innerHTML='';
  D.portfolio.forEach(r=>{
    const ac = r.action.includes('BUY')||r.action.includes('LONG')?'buy':r.action.includes('AVOID')?'avoid':'skip';
    b.insertAdjacentHTML('beforeend',`<tr>
      <td class="sym">${r.symbol}</td><td>${r.name}</td>
      <td class="${ac}">${r.action}</td>
      <td class="num">${r.conviction.toFixed(1)}</td>
      <td class="num ${r.cat_score>=1?'bull':r.cat_score>0?'':'dimv'}">${r.cat_score.toFixed(2)}</td>
      <td class="num">${r.weight_pct>0?r.weight_pct.toFixed(1)+'%':'—'}</td>
      <td class="num">${r.amount_만>0?r.amount_만.toLocaleString():'—'}</td>
    </tr>`);
  });
}

// ── Catalyst Bars ──────────────────────────────────────────────────
function renderCatBars(D){
  const w = document.getElementById('cat-bars');
  w.innerHTML='';
  const mx = Math.max(...D.cat_values, 1);
  D.cat_labels.forEach((name,i)=>{
    const v=D.cat_values[i], sym=D.portfolio[i]?.symbol||'';
    const pct=Math.min(100,(v/mx)*100);
    const cls=v>=1?'pos':v>0?'neg':'zero';
    const vc=v>=1?'bull':v>0?'':'bear';
    w.insertAdjacentHTML('beforeend',`<div class="cat-row">
      <span class="cat-name">${name}</span>
      <span class="cat-sym">${sym}</span>
      <div class="cat-bar-wrap"><div class="cat-bar ${cls}" style="width:${pct}%"></div></div>
      <span class="cat-val ${vc}">${v.toFixed(2)}</span>
    </div>`);
  });
}

// ── Charts ─────────────────────────────────────────────────────────
const PAL=['#d4a017','#39ff14','#00e5ff','#ff8800','#aa44ff','#ff2200'];
const COPTS={backgroundColor:'#0a0806',borderColor:'#d4a017',borderWidth:1,
  titleColor:'#d4a017',bodyColor:'#7a5c0a',
  titleFont:{family:'Share Tech Mono',size:10},bodyFont:{family:'Share Tech Mono',size:10}};
const XSCALE={ticks:{color:'#4a3a18',font:{size:8}},grid:{color:'#0f0c08'},border:{color:'#0f0c08'}};
const YSCALE={ticks:{color:'#4a3a18',font:{size:9}},grid:{color:'#0f0c08'},border:{color:'#0f0c08'}};

function renderCharts(D){
  // Allocation donut
  if(allocChart){allocChart.destroy();allocChart=null;}
  const actx=document.getElementById('alloc-chart');
  if(D.allocation_labels.length>0){
    allocChart=new Chart(actx,{type:'doughnut',data:{
      labels:D.allocation_labels,
      datasets:[{data:D.allocation_values,
        backgroundColor:PAL.map(c=>c+'33'),borderColor:PAL,borderWidth:1.5}]
    },options:{responsive:true,maintainAspectRatio:false,cutout:'65%',
      plugins:{legend:{display:false},
        title:{display:true,text:'ALLOCATION',color:'#4a3a18',font:{family:'Share Tech Mono',size:9},padding:{bottom:2}}}}});
  }

  // Score bar
  if(scoreChart){scoreChart.destroy();scoreChart=null;}
  scoreChart=new Chart(document.getElementById('score-chart'),{type:'bar',data:{
    labels:D.cat_labels,
    datasets:[{data:D.cat_values,
      backgroundColor:D.cat_values.map(v=>v>=1?'#39ff1422':'#7a5c0a22'),
      borderColor:D.cat_values.map(v=>v>=1?'#39ff14':'#7a5c0a'),borderWidth:1}]
  },options:{responsive:true,maintainAspectRatio:false,
    plugins:{legend:{display:false},tooltip:COPTS,
      title:{display:true,text:'CAT SCORE',color:'#4a3a18',font:{family:'Share Tech Mono',size:9},padding:{bottom:2}}},
    scales:{x:XSCALE,y:YSCALE}}});
}

// ── PnL ──────────────────────────────────────────────────────────
function renderPnL(D){
  if(pnlChart){pnlChart.destroy();pnlChart=null;}
  const vals=D.pnl_values;
  const finalPos=vals[vals.length-1]>=0;
  const borderCol=finalPos?'#39ff14':'#ff2200';
  document.getElementById('pnl-sub').textContent =
    D.mcp_connected ? '실 주가 기반 누적 수익률' : '합성 데이터 (MCP 연결 시 실데이터)';

  pnlChart=new Chart(document.getElementById('pnl-chart'),{type:'line',data:{
    labels:D.pnl_labels,
    datasets:[
      {label:'PnL%',data:vals,
        borderColor:borderCol,
        backgroundColor:ctx=>{
          const g=ctx.chart.ctx.createLinearGradient(0,0,0,ctx.chart.height);
          g.addColorStop(0,borderCol+'30');g.addColorStop(1,borderCol+'00');return g;},
        borderWidth:1.5,pointRadius:0,fill:true,tension:0.4},
      {data:new Array(vals.length).fill(0),
        borderColor:'#4a3a18',borderWidth:1,borderDash:[4,4],pointRadius:0,fill:false}
    ]
  },options:{responsive:true,maintainAspectRatio:false,
    interaction:{intersect:false,mode:'index'},
    plugins:{legend:{display:false},tooltip:{...COPTS,
      callbacks:{label:c=>`${c.parsed.y>=0?'+':''}${c.parsed.y.toFixed(2)}%`}}},
    scales:{
      x:{...XSCALE,ticks:{...XSCALE.ticks,maxTicksLimit:12}},
      y:{...YSCALE,ticks:{...YSCALE.ticks,callback:v=>(v>=0?'+':'')+v.toFixed(1)+'%'}}}}});
}

// ── TA Signals ─────────────────────────────────────────────────────
function renderSignals(D){
  const b=document.getElementById('sig-body');
  document.getElementById('sig-badge').textContent=D.ta_signals.length+' SIGNALS';
  if(D.ta_signals.length===0){
    b.innerHTML=`<div class="empty">${D.mcp_connected?'[ MARKET NEUTRAL — NO EXTREME SIGNALS ]':'[ MCP OFFLINE — TA 신호 없음 ]'}</div>`;
    return;
  }
  b.innerHTML='';
  D.ta_signals.forEach(s=>{
    const pos=s.impact>0;
    b.insertAdjacentHTML('beforeend',`<div class="sig-item">
      <span class="sig-sym">${s.symbol}</span>
      <span class="sig-src">${s.source}</span>
      <span class="${pos?'bull':'bear'}">${s.signal}</span>
      <span class="${pos?'bull':'bear'}">${pos?'▲':'▼'}</span>
      <span class="sig-impact ${pos?'bull':'bear'}">${pos?'+':''}${s.impact.toFixed(0)} [${Math.round(s.probability*100)}%]</span>
    </div>`);
  });
  // Source summary
  const src={};
  D.ta_signals.forEach(s=>src[s.source]=(src[s.source]||0)+1);
  const sum=Object.entries(src).map(([k,v])=>`<span style="color:var(--dim);margin-right:10px">${k}:<span style="color:var(--amber)">${v}</span></span>`).join('');
  b.insertAdjacentHTML('beforeend',`<div style="margin-top:10px;padding-top:6px;border-top:1px solid var(--bg3);font-size:9px">${sum}</div>`);
}

// ── Fills ──────────────────────────────────────────────────────────
function renderFills(D){
  const fb=document.getElementById('fills-body');
  const tb=document.getElementById('tickets-body');
  fb.innerHTML='';tb.innerHTML='';

  if(D.fills.length===0){
    fb.innerHTML='<div class="empty">[ NO FILLS — fills/paper/ ]</div>';
  } else {
    D.fills.forEach(f=>{
      const ts=(f.timestamp||f.run_id||'?').substring(0,16);
      const s=f.status||f.result||'?';
      const sc=s.toLowerCase().includes('ok')||s.toLowerCase().includes('success')?'fill-ok':s.toLowerCase().includes('fail')?'fill-fail':'fill-warn';
      fb.insertAdjacentHTML('beforeend',`<div class="fill-item">
        <span class="fill-ts">${ts}</span>
        <span class="fill-sym">${f.ticker||'?'}</span>
        <span class="${sc}">${s}</span>
        <span class="fill-note">${(f.order_no||f.rationale||'').substring(0,30)}</span>
      </div>`);
    });
  }

  if(D.tickets.length===0){
    tb.innerHTML='<div class="empty">[ NO TICKETS — tickets/hourly/ ]</div>';
  } else {
    D.tickets.forEach(t=>{
      const a=(t.action||'?').toUpperCase();
      const ac=a==='BUY'?'fill-ok':a==='AVOID'?'fill-fail':'fill-warn';
      tb.insertAdjacentHTML('beforeend',`<div class="fill-item">
        <span class="fill-ts">${(t._source||'?').substring(0,14)}</span>
        <span class="fill-sym">${t.ticker||'?'}</span>
        <span class="${ac}">${a}</span>
        <span class="fill-note">${(t.rationale||'').substring(0,30)}</span>
      </div>`);
    });
  }
}

// ── Ticker tape ────────────────────────────────────────────────────
let tickerRendered=false;
function renderTicker(D){
  if(tickerRendered) return; // 한 번만 렌더링
  const wrap=document.getElementById('ticker');
  const items=[
    ...D.portfolio.map(r=>({sym:r.symbol,name:r.name,val:r.cat_score.toFixed(2),cls:r.cat_score>=1?'up':'dn',pre:r.cat_score>=1?'▲':'▼'})),
    {sym:'REGIME',name:D.regime.toUpperCase(),val:Math.round(D.regime_confidence*100)+'%',cls:'',pre:''},
    {sym:'SIGNALS',name:'TA',val:D.ta_signals.length,cls:D.ta_signals.length>0?'up':'',pre:''},
  ];
  [...items,...items,...items].forEach(it=>{
    wrap.insertAdjacentHTML('beforeend',`<div class="ticker-item">
      <span class="ts">${it.sym}</span><span>${it.name}</span>
      <span class="tval ${it.cls}">${it.pre}${it.val}</span>
    </div>`);
  });
  tickerRendered=true;
}

// ── Auto-refresh ───────────────────────────────────────────────────
const REFRESH_MS = __REFRESH_MS__;
fetchData(); // 최초 로드
setInterval(fetchData, REFRESH_MS);
</script>
</body>
</html>"""


# ── HTTP Server ───────────────────────────────────────────────────────

class LuxonHandler(BaseHTTPRequestHandler):
    """요청 핸들러. DataService 참조는 클래스 변수로 주입."""
    data_service: DataService = None
    html_cache: str = ""
    log_message = lambda *a: None  # 접속 로그 억제

    def do_GET(self) -> None:
        path = self.path.split("?")[0]

        if path == "/api/data":
            self._serve_json(self.data_service.get())
        elif path in ("/", "/index.html"):
            self._serve_text(self.html_cache, "text/html; charset=utf-8")
        elif path == "/api/health":
            self._serve_json({"status": "ok", "ts": datetime.now().isoformat()})
        else:
            self.send_error(404)

    def _serve_json(self, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_text(self, text: str, ctype: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


class ThreadedHTTPServer(HTTPServer):
    """멀티스레드 HTTP 서버 (동시 요청 처리)."""
    def process_request(self, request, client_address):
        t = threading.Thread(target=self._process, args=(request, client_address))
        t.daemon = True
        t.start()

    def _process(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            pass
        finally:
            self.shutdown_request(request)


# ── Entry point ───────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(prog="luxon_server",
        description="Luxon Live Dashboard Server — 실시간 대시보드")
    parser.add_argument("symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--port", type=int, default=7777)
    parser.add_argument("--capital", type=float, default=100_000_000.0)
    parser.add_argument("--refresh", type=int, default=30,
                        help="데이터 갱신 주기 (초, 기본 30)")
    args = parser.parse_args()

    symbols = args.symbols or DEFAULT_SYMBOLS
    log.info("═" * 55)
    log.info("LUXON LIVE DASHBOARD SERVER")
    log.info("종목: %s", symbols)
    log.info("포트: %d  |  갱신주기: %ds", args.port, args.refresh)
    log.info("═" * 55)

    # DataService 시작
    svc = DataService(symbols, args.capital, args.refresh)
    svc.start()

    # HTML 생성 (refresh 주기를 JS에 주입)
    html = DASHBOARD_HTML.replace("__REFRESH_MS__", str(args.refresh * 1000))

    # 핸들러에 의존성 주입
    LuxonHandler.data_service = svc
    LuxonHandler.html_cache = html

    # 서버 시작
    server = ThreadedHTTPServer(("0.0.0.0", args.port), LuxonHandler)
    log.info("접속 URL: http://localhost:%d", args.port)
    log.info("종료: Ctrl+C")
    log.info("─" * 55)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("서버 종료")
        svc.stop()
        server.server_close()


if __name__ == "__main__":
    main()
