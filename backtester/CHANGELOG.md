# Changelog

All notable changes to Luxon Terminal are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-04-13

### Added

- **Package distribution**: `pyproject.toml` with `luxon-terminal` name,
  entry points `luxon-run` / `luxon-wf`, extras `[exchange,viz,mcp,dev]`.
- **CI/CD**: GitHub Actions workflows for test, lint, security scan.
- **OSS governance**: `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`,
  issue/PR templates.
- **API exports**: `CufaConviction`, `compute_conviction_from_digest`,
  `load_cufa_digests_from_dir`, `build_convictions_from_digests` surfaced
  from `kis_backtest.luxon`.

### Changed

- `AGENTS.md`, `ARCHITECTURE.md` — removed personal/private infrastructure
  references, rewritten as public-facing docs.
- `RiskGateway` default VPS host changed from hardcoded IP to empty string
  (opt-in via `MCP_VPS_HOST` env var).
- README — restructured with public quickstart, removed internal branding.

### Fixed

- **C1** `LuxonTerminal.cycle()` now calls `CapitalLadder.update(equity)` —
  previously the ladder stayed frozen at PAPER forever.
- **C2** `LuxonTerminal` auto-creates stage-aware `RiskGateway` on boot and
  invokes `gateway.check()` in `_live_execute()` — Gate 8/9 (5% symbol,
  20% sector) now actually enforced.
- **C3** `load_cufa_digests_from_dir` accepts `*.html` as well as `*.json`,
  falls back to `CufaHtmlParser` for minimal digest extraction.
- **M1** `_orch_to_portfolio_order` infers `Market.KOSDAQ` from `sector_map`
  string/dict — tax calculation now correct for both exchanges.

---

## [0.8α] — Walk-Forward + CapitalLadder promotion

- `CapitalLadder.promote_if_wf_passed(wf_result)` — 4-gate OOS promotion
  (WF verdict + Sharpe + DD + days).
- `scripts/run_walk_forward.py` — equity JSON → WF validator → auto-promote.
- PAPER stage tightened: `min_sharpe=0.5`, `max_dd=-10%`.

## [0.7α] — CUFA → conviction bridge

- `kis_backtest.luxon.integration.cufa_conviction` — digest → conviction
  with formula `clamp(5 + min(IP,4) - triggered_kills*2, 1, 10)`.
- Terminal boot-time auto-injection via `cufa_digests_dir` config.

## [0.6α] — Live execution

- `LiveOrderExecutor` wired into `LuxonTerminal`.
- Paper/live branch: fills/paper vs fills/live.
- `_KISPriceAdapter` + `_orch_to_portfolio_order` adapters.

## [0.5α] — MacroRegime fix + dashboard

- Fixed `MacroRegime.confidence = 0%` caching bug (offline fallback).
- Phosphor dashboard (port 7777) integrated with paper fills.

## [0.4α] — LuxonTerminal facade

- 7-layer architecture with facade pattern.
- Virtuous feedback loops (BREAK1/2/3).
- 907 tests PASS baseline.

[Unreleased]: https://github.com/YOUR_ORG/luxon-terminal/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/YOUR_ORG/luxon-terminal/releases/tag/v1.0.0
