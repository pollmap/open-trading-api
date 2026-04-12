# KIS Backtest -- Project Anatomy (v0.4a, 2026-04-12)

> 65,010 LOC Python | 880 tests | 142 files | 12 subpackages
> Generated: 2026-04-12 | Last commit: 6b1970f

---

## 0. 30-Second Summary

**What**: KIS Open API based **1-person AI quant operating system**. Define strategies in Python, verify with QuantConnect Lean (Docker), execute on KIS mock/live accounts. Luxon Terminal orchestrates macro regime analysis -> stock evaluation -> position sizing -> risk management -> order execution in one call.

**Core problem solved**: Individual quant investors cannot perform strategy design -> backtest -> risk verification -> live trading within a single system.

---

## 1. Identity & Value

| Item | Value |
|------|-------|
| Name | `kis-backtest` (PyPI), aka "Backtester" / "Luxon Terminal" |
| Version | `1.0.0` (pyproject.toml), Luxon `0.1.0-sprint1-t0` |
| License | Private (personal use only, commercialization deferred) |
| GitHub | `pollmap/open-trading-api` |
| Python | 3.11+ (actual: 3.13.12) |
| Maturity | **Alpha (v0.4a)** -- 880 tests green, core pipeline complete |

### Why it exists

Solves fragmentation in Korean stock market quant infrastructure:
- KIS API: auth/orders/quotes only, no backtesting or risk management
- QuantConnect Lean: global engine but no direct KRX data support
- MCP agent ecosystem: AI tools don't reach investment execution

### What makes it unique

1. **SSoT Architecture**: All strategy inputs converge to `StrategySchema` (Pydantic)
2. **Ackman + Druckenmiller philosophy codified**: Catalyst-required buy + macro regime weighting
3. **GothamGraph**: Pure stdlib property graph (6 nodes / 5 edges, no networkx)
4. **Triple safety**: `dry_run=True` -> `mode="paper"` -> `mode="prod"` + KillSwitch + RiskGateway 7-check + CapitalLadder 5-stage
5. **Built-in MCP server**: AI agents can call backtests directly (port 3846)

---

## 2. Architecture Overview

```
[User]
|
+-- Web browser (:3001)
+-- CLI (python -m kis_backtest.luxon)
+-- Python scripts (scripts/luxon_run.py)
+-- AI agents (MCP :3846)
     |
     v
+--------------------------------------------------------------------+
|                    Entry Layer (4 entry points)                     |
+----------------+----------------+-----------------+----------------+
|  Next.js       |  FastAPI       |  MCP Server     |  CLI / Scripts |
|  Frontend      |  Backend       |  (FastMCP)      |  __main__.py   |
|  :3001         |  :8002         |  :3846          |  luxon_run.py  |
+--------+-------+--------+------+---------+-------+--------+-------+
         |                |                |                |
         v                v                v                v
+--------------------------------------------------------------------+
|                   Core Business Logic Layer                         |
|                                                                    |
|  LuxonOrchestrator    QuantPipeline       StrategySchema (SSoT)    |
|  - run_workflow()     - run()             - from_preset()          |
|  - backtest()         - validate_oos()    - from_yaml_file()       |
|  - validate()         - review()          - from_dict()            |
|  - execute_decisions()- run_with_bt_fb                             |
|  - schedule_review()                                               |
|                                                                    |
|  Portfolio (17 modules)    Execution (10 modules)                  |
|  - Ackman+Druckenmiller    - LiveOrderExecutor                     |
|  - CatalystTracker         - RiskGateway (7 checks)                |
|  - ConvictionSizer         - KillSwitch                            |
|  - MacroRegimeDashboard    - CapitalLadder (5 stages)              |
|  - ReviewEngine            - FillTracker                           |
|  - MCPDataProvider         - ReviewScheduler                       |
|                                                                    |
|  GothamGraph (luxon/graph/)     Risk Modules (5)                   |
|  - 6 NodeKinds, 5 EdgeKinds    - CostModel (KRX fees)             |
|  - 4 Ingestors + HTML viz      - VolTarget (EWMA)                 |
|  - stdlib only, pickle serial   - DrawdownGuard (3-tier)           |
|                                 - CorrelationMonitor               |
|  Stream Layer (luxon/stream/)   - PositionSizer (Kelly)            |
|  - FredHub + FredCache                                             |
|  - TickVault + KIS/Upbit taps                                      |
+--------------------------------------------------------------------+
         |               |                |
         v               v                v
+----------------+ +----------------+ +----------------------+
| KIS Open API   | | Lean Engine    | | Nexus MCP 398 tools  |
| (quotes/orders)| | (Docker)       | | (VPS, macro/factor)  |
+----------------+ +----------------+ +----------------------+
         |                                |
         v                                v
+----------------+                +----------------+
| Upbit API      |                | FRED API       |
| (crypto)       |                | (US macro)     |
+----------------+                +----------------+
```

---

## 3. Directory Structure

```
backtester/                              # Project root (65,010 LOC Python)
|
+-- pyproject.toml                       # Project meta, deps, pytest config
+-- README.md                            # User guide (v0.4a, 650+ lines)
+-- ARCHITECTURE.md                      # Architecture doc
+-- conftest.py                          # pytest root -- sys.path setup
+-- start.sh                             # One-click server start (FE+BE)
+-- kis_auth.py                          # KIS API auth module
|
+-- kis_backtest/                        # Core Python library (39,173 LOC)
|   +-- __init__.py                      # Public API exports
|   +-- client.py                        # LeanClient -- Docker backtest orchestrator
|   +-- exceptions.py                    # Custom exceptions
|   |
|   +-- core/                            # Domain models (SSoT) -- 12 files
|   |   +-- schema.py                    # StrategySchema (Pydantic)
|   |   +-- pipeline.py                  # QuantPipeline -- E2E quant pipeline
|   |   +-- walk_forward.py              # WalkForwardValidator -- N-fold IS/OOS
|   |   +-- strategy.py / condition.py / indicator.py / candlestick.py
|   |   +-- converters.py / param_resolver.py / risk.py / strategy_comparison.py
|   |
|   +-- portfolio/                       # Portfolio management -- 18 files
|   |   +-- ackman_druckenmiller.py      # A+D investment engine
|   |   +-- catalyst_tracker.py          # Catalyst registration/scoring
|   |   +-- conviction_sizer.py          # Half-Kelly position sizing
|   |   +-- macro_regime.py              # Macro regime dashboard
|   |   +-- mcp_data_provider.py         # Nexus MCP 398-tool client
|   |   +-- review_engine.py             # Weekly review engine
|   |   +-- ... (+11 more modules)
|   |
|   +-- execution/                       # Order execution -- 11 files
|   |   +-- order_executor.py            # LiveOrderExecutor (KIS API)
|   |   +-- risk_gateway.py              # RiskGateway (7 checks)
|   |   +-- kill_switch.py               # Emergency stop
|   |   +-- capital_ladder.py            # 5-stage capital deployment
|   |   +-- ... (+6 more modules)
|   |
|   +-- luxon/                           # Luxon Terminal -- 33 files
|   |   +-- orchestrator.py              # Main orchestrator (7 methods)
|   |   +-- backtest_bridge.py           # Pipeline/WF verification adapter
|   |   +-- executor_bridge.py           # KIS trading adapter
|   |   +-- __main__.py                  # CLI entry point
|   |   +-- graph/                       # GothamGraph knowledge graph
|   |   |   +-- graph.py                 # Core graph (stdlib, 288 LOC)
|   |   |   +-- nodes.py / edges.py      # 6 NodeKinds / 5 EdgeKinds
|   |   |   +-- ingestors/              # 4 ingestors
|   |   |   +-- parsers/                # CUFA HTML parser
|   |   |   +-- viz/                    # PyVis HTML renderer
|   |   +-- stream/                      # Real-time data streams
|   |   +-- integration/ / intelligence/ / ontology/ / ui/
|   |
|   +-- strategies/                      # Strategy definitions -- 21 files
|   |   +-- registry.py / base.py / preset/ (10 strategies) / risk/ (5 modules)
|   |
|   +-- lean/                            # Lean integration -- 6 files
|   +-- codegen/                         # Lean code generation -- 3 files
|   +-- dsl/                             # RuleBuilder DSL -- 3 files
|   +-- file/                            # .kis.yaml I/O -- 6 files
|   +-- providers/                       # Exchange providers -- 12 files
|   +-- models/                          # Common data models -- 5 files
|   +-- report/                          # HTML reports -- 10 files
|   +-- utils/                           # Utilities -- 2 files
|
+-- backend/                             # FastAPI REST API (2,520 LOC)
|   +-- main.py                          # App factory (CORS, routers)
|   +-- routes/                          # 6 routers (backtest, luxon, auth, ...)
|   +-- schemas/                         # Pydantic schemas
|
+-- frontend/                            # Next.js 16 + React 19 (4,335 LOC)
|   +-- src/app/                         # Pages (backtest, luxon)
|   +-- src/components/                  # UI components
|   +-- src/lib/api/                     # Backend API client
|
+-- kis_mcp/                             # MCP server (1,717 LOC)
|   +-- server.py                        # FastMCP (12 tools)
|   +-- tools/                           # backtest, strategy, report
|
+-- tests/                               # Test suite (13,858 LOC, 883 cases)
|   +-- luxon/ (18 files)               # Luxon-specific tests
|   +-- test_*.py (27 files)            # Core/portfolio/execution tests
|
+-- scripts/                             # Operational scripts (2,678 LOC)
+-- examples/                            # 8 usage examples
+-- docs/                                # Documentation
```

---

## 4. Tech Stack

| Layer | Tech | Version | Why |
|-------|------|---------|-----|
| Language | Python | 3.13.12 | Quant ecosystem (pandas/numpy/scipy) |
| Framework | FastAPI | >=0.104 | Async REST + auto OpenAPI docs |
| Frontend | Next.js + React | 16.1.6 / 19.2.3 | SSR + App Router + Recharts |
| CSS | Tailwind CSS | v4 | Utility-based responsive |
| Backtest Engine | QuantConnect Lean | Docker latest | Industry-standard open-source |
| MCP | FastMCP | >=1.9.4 | AI agent protocol (streamable-http) |
| Data Analysis | pandas + numpy + scipy | >=2.2 / >=1.26 / >=1.12 | Time series, statistics, optimization |
| Visualization | matplotlib + plotly + Recharts | >=3.8 / >=5.18 / >=3.7 | Server-side + client interactive |
| Graph | PyVis | >=0.3.2 | HTML network visualization |
| HTML Parsing | BeautifulSoup4 | >=4.14.3 | CUFA report parsing |
| Crypto | PyCryptodome | >=3.20 | KIS API encryption |
| Testing | pytest + pytest-asyncio | >=9.0.2 | 883 tests, async support |
| Package Mgmt | uv + hatchling | latest | Fast dependency resolution |

---

## 5. Key Data Models

### OrchestrationReport (luxon/orchestrator.py)
```
regime: str                    # "expansion" | "recovery" | ...
regime_confidence: float       # 0.0~1.0
portfolio: PortfolioDecision
  decisions: list[InvestmentDecision]
    symbol, action ("buy"|"skip"|"hold"|"sell"), conviction, catalyst_score, final_weight
  total_equity_weight, cash_weight
position_sizes: list[PositionSize]
  symbol, weight (Half-Kelly), amount (KRW)
cross_references: dict[str, list[str]]
```

### PipelineResult (core/pipeline.py)
```
order: PortfolioOrder | None
risk_passed: bool
risk_details: list[str]
vol_adjustments, turb_index, dd_state
kelly_allocation: float        # 0.0~1.0
```

### WFResult (core/walk_forward.py)
```
folds: list[FoldResult]
  is_sharpe, oos_sharpe, oos_return, oos_max_dd, degradation
passed: bool
verdict: str                   # "PASS" | "FAIL: ..."
```

### GothamGraph (luxon/graph/graph.py)
```
6 NodeKinds: SYMBOL, SECTOR, EVENT, THEME, MACRO_REGIME, PERSON
5 EdgeKinds: BELONGS_TO, CATALYST_FOR, HOLDS, CORRELATED, TRIGGERED_BY
Operations: add_node/edge, neighbors, three_hop BFS, pickle save/load
```

---

## 6. Main Workflow (Luxon Analysis -> Verify -> Execute)

```
User          Orchestrator      A+D Engine      GothamGraph     Pipeline      KIS API
|                |                |                |              |             |
|--run_workflow-->|                |                |              |             |
|                |--evaluate_port->|               |              |             |
|                |<-PortfolioDecision               |              |             |
|                |--size_position->ConvictionSizer  |              |             |
|                |--ingest_all------------------->|              |             |
|<--Report-------|                                  |              |             |
|                |                                  |              |             |
|--backtest()--->|--run_risk_pipeline---------------------------->|             |
|<--risk_passed--|                                  |              |             |
|                |                                  |              |             |
|--validate()--->|--validate_oos (5-fold WF)------------------------->|        |
|<--WFResult-----|                                  |              |             |
|                |                                  |              |             |
|--execute_dec-->|--build_order-->ExecutorBridge     |              |             |
|                |--check-->KillSwitch              |              |             |
|                |--execute--------------------------------------------------->|
|<--ExecReport---|                                  |              |             |
```

---

## 7. LuxonOrchestrator API (7 methods)

```python
class LuxonOrchestrator:
    run_workflow(symbols, base_convictions)           -> OrchestrationReport
    generate_weekly_letter(symbols, output_path)      -> Path
    backtest(report, returns_dict)                    -> PipelineResult
    validate(report, returns_dict, n_folds=5)         -> WFResult
    schedule_review(brokerage, vault_path)             -> {daily, weekly}
    execute_decisions(report, brokerage, mode, dry_run) -> ExecutionReport
    run_and_execute(symbols, convictions, ...)         -> (Report, ExecReport)
```

---

## 8. API Endpoints

### FastAPI REST (port 8002)
| Method | Path | Description |
|--------|------|-------------|
| GET | /api/health | Health check |
| GET | /api/strategies | Strategy list |
| POST | /api/backtest/run | Preset backtest |
| POST | /api/backtest/run-custom | YAML backtest |
| POST | /api/files/validate | YAML validation |
| GET | /api/symbols/search | Stock search |
| POST | /api/luxon/analyze | Luxon analysis |
| GET | /api/luxon/graph | Graph HTML path |

### MCP Server (port 3846) -- 12 tools
list_presets, get_preset_yaml, validate_yaml, list_indicators,
run_backtest, run_preset_backtest, get_backtest_result, retry_backtest,
get_report, run_batch_backtest, optimize_strategy, health_check

### CLI
```bash
python -m kis_backtest.luxon 005930 000660 --conviction 8
python -m kis_backtest.luxon 005930 --backtest --validate
python -m kis_backtest.luxon 005930 --paper --dry-run
```

---

## 9. Environment Variables

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| ~/KIS/config/kis_devlp.yaml | -- | For trading | KIS API keys |
| MCP_HOST | 127.0.0.1 | N | MCP server bind |
| MCP_PORT | 3846 | N | MCP server port |
| LUXON_MCP_URL | -- | N | Nexus MCP URL |
| NEXT_PUBLIC_API_URL | http://localhost:8002 | N | Frontend->Backend |

---

## 10. Status & Completeness

### Implemented
- 10 preset strategies + .kis.yaml SSoT
- Lean Docker backtest (KRX + US)
- Grid/Random parameter optimization
- QuantPipeline E2E (6 risk checks + Kelly)
- Walk-Forward N-fold IS/OOS validation
- Ackman+Druckenmiller investment engine
- GothamGraph knowledge graph (6 nodes / 5 edges + 4 ingestors)
- LuxonOrchestrator (analysis -> verify -> execute one-liner)
- ExecutorBridge + BacktestBridge (adapters)
- MCP server 12 tools
- FastAPI REST API 6 routers
- Next.js frontend (backtest + Luxon pages)
- KIS + Upbit dual exchange providers
- 880 tests (880 passed, 3 skipped)

### Partial
- intelligence/ (LLM agent layer, empty placeholder)
- Real trading path (mode="prod", code exists but never executed)
- ReviewScheduler (code complete, no real fill data yet)

### Code Quality
- Architecture consistency: HIGH (SSoT + Adapter + Facade patterns)
- Test coverage: 880 tests / 65K LOC
- Module separation: GOOD (each file 200-400 LOC)
- Documentation: README 650+ lines, docstrings throughout
- Security: API keys in env/YAML, dry_run=True default, triple safety

---

## 11. Quick Start

```bash
git clone https://github.com/pollmap/open-trading-api.git
cd open-trading-api/backtester
uv sync
bash scripts/setup_lean_data.sh
./start.sh
# -> http://localhost:3001
```

---

## 12. Roadmap

### Short-term (1-2 weeks)
- Paper trading first run (mode="paper" with KIS mock account)
- Real data returns_dict from MCP for backtest/validate
- CI pipeline (GitHub Actions: pytest + lint)

### Mid-term (1-3 months)
- Real-time dashboard (LiveMonitor + WebSocket -> frontend)
- CapitalLadder auto-promotion (Sharpe/DD criteria)
- Upbit backtest support

### Long-term (6+ months)
- LLM Intelligence layer (news/disclosure auto-analysis)
- SaaS conversion (multi-user auth + PostgreSQL)
- Global expansion (US market via Alpaca API)
