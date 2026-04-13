"""Luxon Terminal — AI quant trading loop.

Top-level facade:
    >>> from kis_backtest.luxon import LuxonTerminal, TerminalConfig
    >>> terminal = LuxonTerminal(symbols=["005930", "000660"])
    >>> terminal.boot()
    >>> report = terminal.cycle()
    >>> print(report.summary())

Sub-packages:
    stream/       — data hub (FRED, TickVault)
    graph/        — entity graph (GothamGraph)
    integration/  — CUFA → conviction bridge + Phase 1 pipeline
    intelligence/ — MCP bridge + LLM router (optional)

Design principles:
    - Existing portfolio/execution/providers untouched, extended via adapters
    - All order paths enforce RiskGateway → KillSwitch → CapitalLadder
    - Real data only (no mocks in production paths)
"""
from __future__ import annotations

import logging

__version__ = "1.2.0"

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Terminal (통합 파사드) — 핵심 공개 API
# ---------------------------------------------------------------------------

from kis_backtest.luxon.terminal import (  # noqa: E402
    CycleReport,
    LuxonTerminal,
    TerminalConfig,
)

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

from kis_backtest.luxon.orchestrator import (  # noqa: E402
    LuxonOrchestrator,
    OrchestrationReport,
)

# ---------------------------------------------------------------------------
# GothamGraph
# ---------------------------------------------------------------------------

from kis_backtest.luxon.graph.graph import GothamGraph  # noqa: E402

# ---------------------------------------------------------------------------
# Graph Ingestors
# ---------------------------------------------------------------------------

from kis_backtest.luxon.graph.ingestors.ta_signal_ingestor import (  # noqa: E402
    TASignal,
    TASignalIngestor,
)

try:
    from kis_backtest.luxon.graph.ingestors.signal_accuracy_tracker import (  # noqa: E402
        SignalAccuracyTracker,
    )
except Exception as _e:  # pragma: no cover
    log.warning("SignalAccuracyTracker import 실패: %s", _e)
    SignalAccuracyTracker = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# 공개 API 목록
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Integration (CUFA + Phase1Pipeline)
# ---------------------------------------------------------------------------

try:
    from kis_backtest.luxon.integration.cufa_conviction import (  # noqa: E402
        CufaConviction,
        build_convictions_from_digests,
        compute_conviction_from_digest,
        load_cufa_digests_from_dir,
    )
except Exception as _e:  # pragma: no cover
    log.debug("CUFA integration import 실패: %s", _e)
    CufaConviction = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

__all__: list[str] = [
    "__version__",
    # Terminal
    "LuxonTerminal",
    "TerminalConfig",
    "CycleReport",
    # Orchestrator
    "LuxonOrchestrator",
    "OrchestrationReport",
    # Graph
    "GothamGraph",
    # Ingestors
    "TASignalIngestor",
    "TASignal",
    "SignalAccuracyTracker",
    # Integration (CUFA)
    "CufaConviction",
    "compute_conviction_from_digest",
    "load_cufa_digests_from_dir",
    "build_convictions_from_digests",
]
