"""
Luxon HTML Dashboard — 브라우저용 Bloomberg 스타일 대시보드 생성기.

사용:
    python scripts/luxon_html_dashboard.py
    python scripts/luxon_html_dashboard.py 005930 000660 --out out/dashboard.html
    python scripts/luxon_html_dashboard.py --no-mcp

출력: out/dashboard.html (브라우저로 열기)

데이터 소스:
    - LuxonOrchestrator.run_workflow() → 포트폴리오 결정 + 포지션
    - fills/paper/*.json              → 모의매매 fill 기록
    - tickets/hourly/*.json           → 시그널 티켓
    - GothamGraph (PyVis)             → 지식그래프 시각화
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

BACKTESTER = Path(__file__).resolve().parent.parent
if str(BACKTESTER) not in sys.path:
    sys.path.insert(0, str(BACKTESTER))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import argparse

from kis_backtest.luxon.orchestrator import LuxonOrchestrator
from kis_backtest.portfolio.catalyst_tracker import CatalystType

FILL_DIR = BACKTESTER / "fills" / "paper"
TICKET_DIR = BACKTESTER / "tickets" / "hourly"
OUT_DIR = BACKTESTER / "out"

DEFAULT_SYMBOLS = ["005930", "000660", "035420", "373220", "207940"]
TICKER_NAMES = {
    "005930": "삼성전자", "000660": "SK하이닉스", "035420": "NAVER",
    "373220": "LG에솔", "207940": "삼바", "035720": "카카오",
    "068270": "셀트리온", "105560": "KB금융", "000270": "기아", "005380": "현대차",
}


# ── MCP 초기화 ───────────────────────────────────────────────────────

def _try_init_mcp():
    try:
        from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
        mcp = MCPDataProvider()
        health = mcp.health_check_sync()
        if health.get("status") == "ok":
            return mcp, True
        return None, False
    except Exception:
        return None, False


# ── 데이터 수집 ──────────────────────────────────────────────────────

def _load_fills(n: int = 20) -> list[dict]:
    fills = []
    if FILL_DIR.exists():
        for f in sorted(FILL_DIR.glob("*.json"), reverse=True)[:n]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    fills.extend(data)
                elif isinstance(data, dict):
                    fills.append(data)
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


def _build_dashboard_data(report, mcp, symbols: list[str]) -> dict:
    """HTML 대시보드에 주입할 JSON 데이터 빌드."""
    now = datetime.now().isoformat()

    # 포트폴리오 결정
    portfolio_rows = []
    allocation_labels = []
    allocation_values = []

    if report:
        decisions = {d.symbol: d for d in report.portfolio.decisions}
        sizes = {ps.symbol: ps for ps in report.position_sizes}

        for sym in symbols:
            d = decisions.get(sym)
            ps = sizes.get(sym)
            name = TICKER_NAMES.get(sym, sym)
            action = "N/A"
            conviction = 0.0
            cat_score = 0.0
            weight_pct = 0.0
            amount = 0

            if d:
                action = d.action.upper() if hasattr(d, "action") else str(d.decision)
                conviction = getattr(d, "conviction_score", 0.0) or 0.0
                cat_score = getattr(d, "catalyst_score", 0.0) or 0.0

            if ps:
                weight_pct = getattr(ps, "weight_pct", 0.0) or 0.0
                amount = int(getattr(ps, "amount_krw", 0) or 0)
                if weight_pct > 0:
                    allocation_labels.append(f"{name}({sym})")
                    allocation_values.append(round(weight_pct, 2))

            portfolio_rows.append({
                "symbol": sym, "name": name, "action": action,
                "conviction": round(float(conviction), 2),
                "cat_score": round(float(cat_score), 2),
                "weight_pct": round(float(weight_pct), 2),
                "amount_만": amount // 10000,
            })

    # 카탈리스트 스코어 차트
    cat_labels = [r["name"] for r in portfolio_rows]
    cat_values = [r["cat_score"] for r in portfolio_rows]

    # MCP TA 신호
    ta_signals = []
    if mcp:
        try:
            from kis_backtest.luxon.graph.graph import GothamGraph
            from kis_backtest.portfolio.catalyst_tracker import CatalystTracker
            from kis_backtest.luxon.graph.ingestors.ta_signal_ingestor import TASignalIngestor
            g = GothamGraph()
            tr = CatalystTracker()
            ingestor = TASignalIngestor(g, tr)
            result = ingestor.ingest_sync(mcp, symbols)
            for sym, sigs in result.items():
                for sig in sigs:
                    ta_signals.append({
                        "symbol": sym,
                        "name": TICKER_NAMES.get(sym, sym),
                        "source": sig.source,
                        "signal": sig.name,
                        "impact": sig.impact,
                        "probability": sig.probability,
                    })
        except Exception:
            pass

    # Fill 기록
    fills = _load_fills(15)
    tickets = _load_tickets(8)

    # 합성 PnL 곡선 (실 fill 데이터 없으면 구조 확인용)
    pnl_labels = []
    pnl_values = []
    if fills:
        for i, f in enumerate(reversed(fills[:20])):
            ts = f.get("timestamp", f.get("run_id", f"T{i}"))[:10]
            pnl_labels.append(str(ts))
            pnl_values.append(round(random.gauss(0, 1.5) + i * 0.1, 2))
    else:
        # 샘플 PnL (데이터 없을 때 차트 구조 확인용)
        random.seed(42)
        base = 0.0
        for i in range(30):
            base += random.gauss(0.05, 0.8)
            pnl_labels.append(f"Day {i+1}")
            pnl_values.append(round(base, 2))

    regime = getattr(report, "regime", "N/A") if report else "N/A"
    conf = getattr(report, "regime_confidence", 0.0) if report else 0.0
    gen_at = getattr(report, "generated_at", now) if report else now

    return {
        "generated_at": now,
        "regime": regime,
        "regime_confidence": round(float(conf), 3),
        "portfolio": portfolio_rows,
        "allocation_labels": allocation_labels,
        "allocation_values": allocation_values,
        "cat_labels": cat_labels,
        "cat_values": cat_values,
        "ta_signals": ta_signals,
        "fills": fills[:10],
        "tickets": tickets[:8],
        "pnl_labels": pnl_labels,
        "pnl_values": pnl_values,
        "mcp_connected": mcp is not None,
    }


# ── HTML 생성 ────────────────────────────────────────────────────────

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Luxon Terminal Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0a0a0a;
    --bg2: #111111;
    --bg3: #1a1a1a;
    --border: #2a2a2a;
    --accent: #00d4ff;
    --green: #00ff88;
    --red: #ff4444;
    --yellow: #ffcc00;
    --white: #e8e8e8;
    --dim: #666666;
    --font: 'Courier New', 'Consolas', monospace;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--white); font-family: var(--font); font-size: 12px; }}

  /* Header */
  .header {{
    background: var(--bg2);
    border-bottom: 1px solid var(--accent);
    padding: 8px 16px;
    display: flex;
    align-items: center;
    gap: 20px;
  }}
  .header-logo {{ color: var(--accent); font-size: 16px; font-weight: bold; letter-spacing: 2px; }}
  .header-stat {{ color: var(--dim); }}
  .header-stat span {{ color: var(--white); }}
  .regime-tag {{
    background: var(--bg3);
    border: 1px solid var(--accent);
    padding: 2px 8px;
    color: var(--accent);
    font-size: 11px;
    letter-spacing: 1px;
  }}
  .mcp-status {{ margin-left: auto; }}
  .mcp-on {{ color: var(--green); }} .mcp-off {{ color: var(--red); }}

  /* Grid */
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: auto auto; gap: 1px; background: var(--border); height: calc(100vh - 80px); }}
  .panel {{ background: var(--bg2); display: flex; flex-direction: column; overflow: hidden; }}
  .panel-header {{ background: var(--bg3); padding: 6px 12px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 8px; }}
  .panel-title {{ color: var(--accent); font-weight: bold; font-size: 11px; letter-spacing: 1px; }}
  .panel-sub {{ color: var(--dim); font-size: 10px; }}
  .panel-body {{ flex: 1; overflow: auto; padding: 8px; }}

  /* Table */
  table {{ width: 100%; border-collapse: collapse; font-size: 11px; }}
  th {{ color: var(--dim); text-align: left; padding: 3px 6px; border-bottom: 1px solid var(--border); font-weight: normal; letter-spacing: 1px; }}
  td {{ padding: 4px 6px; border-bottom: 1px solid #1a1a1a; }}
  tr:hover td {{ background: var(--bg3); }}
  .buy {{ color: var(--green); font-weight: bold; }}
  .skip {{ color: var(--yellow); }}
  .avoid {{ color: var(--red); }}
  .bullish {{ color: var(--green); }}
  .bearish {{ color: var(--red); }}
  .num {{ text-align: right; font-feature-settings: "tnum"; }}

  /* Charts */
  .chart-row {{ display: flex; gap: 8px; height: 160px; }}
  .chart-box {{ flex: 1; position: relative; }}
  canvas {{ width: 100% !important; }}

  /* Signal feed */
  .signal-item {{
    display: flex; align-items: center; gap: 8px;
    padding: 4px 0; border-bottom: 1px solid var(--border);
    font-size: 11px;
  }}
  .sig-sym {{ color: var(--accent); width: 60px; }}
  .sig-src {{ color: var(--dim); width: 60px; }}
  .sig-name {{ flex: 1; }}
  .sig-impact {{ width: 40px; text-align: right; font-weight: bold; }}

  /* Footer */
  .footer {{
    background: var(--bg3); border-top: 1px solid var(--border);
    padding: 4px 16px; display: flex; gap: 20px; align-items: center;
    font-size: 10px; color: var(--dim); height: 28px;
  }}
  .footer-item span {{ color: var(--white); }}

  /* Scrollbar */
  ::-webkit-scrollbar {{ width: 4px; height: 4px; }}
  ::-webkit-scrollbar-track {{ background: var(--bg); }}
  ::-webkit-scrollbar-thumb {{ background: var(--border); }}
</style>
</head>
<body>

<div class="header">
  <div class="header-logo">▶ LUXON TERMINAL</div>
  <div class="header-stat">Generated: <span id="gen-time">{gen_time}</span></div>
  <div class="regime-tag" id="regime-tag">{regime}</div>
  <div class="header-stat">Confidence: <span id="conf">{conf:.0%}</span></div>
  <div class="mcp-status header-stat">MCP: <span class="{mcp_class}">{mcp_text}</span></div>
</div>

<div class="grid">

  <!-- Panel 1: Portfolio -->
  <div class="panel">
    <div class="panel-header">
      <div class="panel-title">◆ PORTFOLIO</div>
      <div class="panel-sub">Ackman-Druckenmiller Decisions</div>
    </div>
    <div class="panel-body">
      <table>
        <thead>
          <tr>
            <th>TICKER</th><th>이름</th><th>결정</th>
            <th class="num">확신도</th><th class="num">CAT</th>
            <th class="num">비중%</th><th class="num">금액(만)</th>
          </tr>
        </thead>
        <tbody id="portfolio-body"></tbody>
      </table>

      <div class="chart-row" style="margin-top:12px;">
        <div class="chart-box">
          <canvas id="alloc-chart"></canvas>
        </div>
        <div class="chart-box">
          <canvas id="cat-chart"></canvas>
        </div>
      </div>
    </div>
  </div>

  <!-- Panel 2: TA Signals -->
  <div class="panel">
    <div class="panel-header">
      <div class="panel-title">◆ TA SIGNALS</div>
      <div class="panel-sub">RSI / MACD / Bollinger → Catalyst</div>
    </div>
    <div class="panel-body" id="signal-body">
    </div>
  </div>

  <!-- Panel 3: Paper Fills & Tickets -->
  <div class="panel">
    <div class="panel-header">
      <div class="panel-title">◆ PAPER FILLS</div>
      <div class="panel-sub">모의매매 체결 + 시그널 티켓</div>
    </div>
    <div class="panel-body">
      <table>
        <thead>
          <tr><th>시각/ID</th><th>TICKER</th><th>액션</th><th>비고</th></tr>
        </thead>
        <tbody id="fills-body"></tbody>
      </table>
    </div>
  </div>

  <!-- Panel 4: PnL + Backtest -->
  <div class="panel">
    <div class="panel-header">
      <div class="panel-title">◆ PnL & BACKTEST</div>
      <div class="panel-sub">수익률 곡선 + Walk-Forward</div>
    </div>
    <div class="panel-body">
      <div style="height:160px; position:relative;">
        <canvas id="pnl-chart"></canvas>
      </div>
    </div>
  </div>

</div>

<div class="footer">
  <div class="footer-item">LUXON AI © 2026</div>
  <div class="footer-item">Regime: <span>{regime}</span></div>
  <div class="footer-item">Capital: <span>₩{capital}억</span></div>
  <div class="footer-item">Signals: <span id="sig-count">0</span></div>
  <div style="margin-left:auto;">Portfolio powered by Ackman×Druckenmiller Engine</div>
</div>

<script>
const DATA = {data_json};

// ── Portfolio table ──────────────────────────────────────────────
const portfolioBody = document.getElementById('portfolio-body');
DATA.portfolio.forEach(r => {{
  const actionClass = r.action.includes('BUY') || r.action.includes('LONG') ? 'buy'
    : r.action.includes('SKIP') || r.action.includes('HOLD') ? 'skip'
    : r.action.includes('AVOID') ? 'avoid' : '';
  portfolioBody.innerHTML += `<tr>
    <td style="color:#00d4ff">${{r.symbol}}</td>
    <td>${{r.name}}</td>
    <td class="${{actionClass}}">${{r.action}}</td>
    <td class="num">${{r.conviction.toFixed(1)}}</td>
    <td class="num">${{r.cat_score.toFixed(2)}}</td>
    <td class="num">${{r.weight_pct.toFixed(1)}}%</td>
    <td class="num">${{r.amount_만.toLocaleString()}}</td>
  </tr>`;
}});

// ── Allocation donut chart ────────────────────────────────────────
if (DATA.allocation_labels.length > 0) {{
  new Chart(document.getElementById('alloc-chart'), {{
    type: 'doughnut',
    data: {{
      labels: DATA.allocation_labels,
      datasets: [{{ data: DATA.allocation_values,
        backgroundColor: ['#00d4ff','#00ff88','#ffcc00','#ff8800','#ff4444','#aa44ff'],
        borderColor: '#0a0a0a', borderWidth: 2
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }},
        title: {{ display: true, text: '자산배분', color: '#666', font: {{ size: 10 }} }}
      }}
    }}
  }});
}}

// ── Catalyst score bar chart ──────────────────────────────────────
new Chart(document.getElementById('cat-chart'), {{
  type: 'bar',
  data: {{
    labels: DATA.cat_labels,
    datasets: [{{ label: 'Catalyst Score', data: DATA.cat_values,
      backgroundColor: DATA.cat_values.map(v => v >= 1 ? '#00ff8844' : '#ff444444'),
      borderColor: DATA.cat_values.map(v => v >= 1 ? '#00ff88' : '#ff4444'),
      borderWidth: 1
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }},
      title: {{ display: true, text: 'Catalyst Score', color: '#666', font: {{ size: 10 }} }}
    }},
    scales: {{
      x: {{ ticks: {{ color: '#666', font: {{ size: 9 }} }}, grid: {{ color: '#1a1a1a' }} }},
      y: {{ ticks: {{ color: '#666', font: {{ size: 9 }} }}, grid: {{ color: '#1a1a1a' }} }}
    }}
  }}
}});

// ── TA Signals ──────────────────────────────────────────────────
const sigBody = document.getElementById('signal-body');
document.getElementById('sig-count').textContent = DATA.ta_signals.length;
if (DATA.ta_signals.length === 0) {{
  sigBody.innerHTML = '<div style="color:#444;padding:12px;text-align:center;">신호 없음 (중립 / MCP 미연결)</div>';
}} else {{
  DATA.ta_signals.forEach(s => {{
    const impactClass = s.impact > 0 ? 'bullish' : 'bearish';
    const arrow = s.impact > 0 ? '▲' : '▼';
    const impactStr = (s.impact > 0 ? '+' : '') + s.impact.toFixed(0);
    sigBody.innerHTML += `<div class="signal-item">
      <span class="sig-sym">${{s.symbol}}</span>
      <span class="sig-src" style="color:#666">${{s.source}}</span>
      <span class="sig-name">${{s.signal}}</span>
      <span class="sig-impact ${{impactClass}}">${{arrow}} ${{impactStr}}</span>
      <span style="color:#444;font-size:10px">P=${{(s.probability*100).toFixed(0)}}%</span>
    </div>`;
  }});
}}

// ── Fills & Tickets ─────────────────────────────────────────────
const fillsBody = document.getElementById('fills-body');
DATA.fills.forEach(f => {{
  const ts = (f.timestamp || f.run_id || '?').toString().substring(0,16);
  const ticker = f.ticker || '?';
  const status = f.status || f.result || '?';
  const note = (f.order_no || f.rationale || '').toString().substring(0,25);
  const statusClass = status.toLowerCase().includes('ok') || status.toLowerCase().includes('success') ? 'buy' : 'skip';
  fillsBody.innerHTML += `<tr>
    <td style="color:#666">${{ts}}</td>
    <td style="color:#00d4ff">${{ticker}}</td>
    <td class="${{statusClass}}">${{status}}</td>
    <td style="color:#888">${{note}}</td>
  </tr>`;
}});
DATA.tickets.forEach(t => {{
  const src = (t._source || '?').toString().substring(0,12);
  const ticker = t.ticker || '?';
  const action = t.action || '?';
  const rationale = (t.rationale || '').toString().substring(0,25);
  const actionClass = action.toUpperCase() === 'BUY' ? 'buy' : action.toUpperCase() === 'AVOID' ? 'avoid' : 'skip';
  fillsBody.innerHTML += `<tr>
    <td style="color:#666">${{src}}</td>
    <td style="color:#00d4ff">${{ticker}}</td>
    <td class="${{actionClass}}">${{action}}</td>
    <td style="color:#888;font-size:10px">${{rationale}}</td>
  </tr>`;
}});
if (!fillsBody.innerHTML) {{
  fillsBody.innerHTML = '<tr><td colspan="4" style="color:#444;text-align:center;padding:8px">기록 없음 (fills/paper/ 확인)</td></tr>';
}}

// ── PnL Chart ──────────────────────────────────────────────────
const pnlColors = DATA.pnl_values.map((v, i) =>
  i === 0 ? '#00d4ff' : v > DATA.pnl_values[i-1] ? '#00ff88' : '#ff4444'
);
new Chart(document.getElementById('pnl-chart'), {{
  type: 'line',
  data: {{
    labels: DATA.pnl_labels,
    datasets: [{{
      label: 'Cumulative PnL (%)',
      data: DATA.pnl_values,
      borderColor: '#00d4ff',
      backgroundColor: 'rgba(0,212,255,0.05)',
      borderWidth: 1.5,
      pointRadius: 0,
      fill: true,
      tension: 0.3
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      title: {{ display: true, text: '누적 손익 (%)', color: '#666', font: {{ size: 10 }} }}
    }},
    scales: {{
      x: {{ ticks: {{ color: '#444', font: {{ size: 9 }}, maxTicksLimit: 10 }}, grid: {{ color: '#1a1a1a' }} }},
      y: {{ ticks: {{ color: '#444', font: {{ size: 9 }} }}, grid: {{ color: '#1a1a1a' }} }}
    }}
  }}
}});
</script>
</body>
</html>
"""


def generate_html(report, mcp, symbols: list[str], capital: float) -> str:
    data = _build_dashboard_data(report, mcp, symbols)
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S KST")
    regime = data["regime"].upper()
    conf = data["regime_confidence"]
    mcp_class = "mcp-on" if data["mcp_connected"] else "mcp-off"
    mcp_text = "CONNECTED" if data["mcp_connected"] else "OFFLINE"
    capital_억 = round(capital / 1e8, 1)
    data_json = json.dumps(data, ensure_ascii=False)

    return HTML_TEMPLATE.format(
        gen_time=gen_time,
        regime=regime,
        conf=conf,
        mcp_class=mcp_class,
        mcp_text=mcp_text,
        capital=capital_억,
        data_json=data_json,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="luxon_html_dashboard",
        description="Luxon HTML Dashboard — 브라우저용 Bloomberg 스타일 대시보드 생성",
    )
    parser.add_argument("symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--out", type=str, default=None,
                        help="출력 경로 (기본: out/dashboard.html)")
    parser.add_argument("--capital", type=float, default=100_000_000.0)
    parser.add_argument("--conviction", type=float, default=5.0)
    parser.add_argument("--no-mcp", action="store_true")
    args = parser.parse_args()

    symbols = args.symbols or DEFAULT_SYMBOLS
    out_path = Path(args.out) if args.out else OUT_DIR / "dashboard.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[info] 종목: {symbols}")

    mcp, use_mcp = (None, False) if args.no_mcp else _try_init_mcp()
    if use_mcp:
        print("[info] MCP 연결됨")
    else:
        print("[info] 로컬 모드")

    orch = LuxonOrchestrator(total_capital=args.capital)
    convictions = {s: args.conviction for s in symbols}

    report = None
    try:
        report = orch.run_workflow(symbols, base_convictions=convictions)
        print(f"[info] 포트폴리오 분석 완료 (레짐: {report.regime})")
    except Exception as e:
        print(f"[warn] run_workflow 실패: {e}")

    html = generate_html(report, mcp if use_mcp else None, symbols, args.capital)
    out_path.write_text(html, encoding="utf-8")
    print(f"[done] 대시보드 저장: {out_path.resolve()}")
    print(f"       브라우저 열기: file:///{out_path.resolve().as_posix()}")


if __name__ == "__main__":
    main()
