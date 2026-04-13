"""
Bootstrap Orchestrator — 로컬 LLM 스택 + MCP 서버 전체 자동 기동·헬스체크·워밍업.

흐름:
    1. 현재 엔드포인트 헬스 스캔 (FAST/DEFAULT/HEAVY/LONG + MCP 서버들)
    2. 다운된 LLM 엔드포인트는 PowerShell 스크립트로 자동 기동 시도
    3. 모델 워밍업 (1회 ping으로 모델 메모리 로드)
    4. MCP 서버 initialize
    5. 통합 상태 리포트

CUFA/agentic을 실행하기 전에 `luxon-bootstrap`을 부르면 전 스택 준비 완료.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from kis_backtest.luxon.intelligence.mcp_bridge import MCPClient, MCPUnavailableError
from kis_backtest.luxon.intelligence.mcp_registry import (
    MCPServerInfo,
    list_known_servers,
)
from kis_backtest.luxon.intelligence.router import (
    Tier,
    call,
    health_check,
)


# ── 환경 ──────────────────────────────────────────────────────────

LLM_STACK_SCRIPT = Path(r"C:/scripts/start-llm-stack.ps1")
DEFAULT_WARMUP_TIMEOUT = 120.0  # 초, 첫 모델 로드는 오래 걸릴 수 있음


# ── 상태 ──────────────────────────────────────────────────────────


@dataclass
class TierStatus:
    tier: Tier
    reachable: bool
    warmed_up: bool = False
    warmup_ms: float = 0.0
    error: str = ""


@dataclass
class MCPStatus:
    server: str
    reachable: bool
    tool_count: int = 0
    error: str = ""


@dataclass
class BootstrapReport:
    tiers: list[TierStatus] = field(default_factory=list)
    mcp_servers: list[MCPStatus] = field(default_factory=list)
    stack_script_invoked: bool = False

    @property
    def any_llm_ready(self) -> bool:
        return any(t.reachable for t in self.tiers)

    @property
    def any_mcp_ready(self) -> bool:
        return any(m.reachable for m in self.mcp_servers)

    @property
    def fully_ready(self) -> bool:
        return self.any_llm_ready and all(
            t.reachable for t in self.tiers if t.tier in (Tier.DEFAULT,)
        )

    def format_report(self) -> str:
        lines = ["=== Luxon Bootstrap Report ==="]
        lines.append("")
        lines.append("[LLM Tiers]")
        for t in self.tiers:
            mark = "[OK]" if t.reachable else "[--]"
            warm = (
                f"warmed ({t.warmup_ms:.0f}ms)" if t.warmed_up
                else ("cold" if t.reachable else t.error or "unreachable")
            )
            lines.append(f"  {mark} {t.tier.value.name:<8s} {t.tier.value.model:<40s} {warm}")
        lines.append("")
        lines.append("[MCP Servers]")
        for m in self.mcp_servers:
            mark = "[OK]" if m.reachable else "[--]"
            info = f"{m.tool_count} tools" if m.reachable else m.error or "unreachable"
            lines.append(f"  {mark} {m.server:<20s} {info}")
        lines.append("")
        lines.append(f"[Summary] LLM ready: {self.any_llm_ready} / "
                     f"MCP ready: {self.any_mcp_ready} / "
                     f"Fully ready: {self.fully_ready}")
        return "\n".join(lines)


# ── LLM 스택 기동 ─────────────────────────────────────────────────


def invoke_stack_script(timeout: float = 10.0) -> bool:
    """Windows PowerShell로 start-llm-stack.ps1 실행. 블로킹 아님 (백그라운드 기동)."""
    if not LLM_STACK_SCRIPT.exists():
        return False
    try:
        # 백그라운드 실행 — 스크립트 내부에서 Start-Process로 각 서버 기동
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", str(LLM_STACK_SCRIPT),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def wait_for_tier(tier: Tier, *, timeout: float = 60.0, interval: float = 2.0) -> bool:
    """티어 엔드포인트가 살아날 때까지 폴링."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if health_check(tier, timeout=2.0):
            return True
        time.sleep(interval)
    return False


# ── 워밍업 ────────────────────────────────────────────────────────


def warmup_tier(tier: Tier, timeout: float = DEFAULT_WARMUP_TIMEOUT) -> TierStatus:
    """1회 ping 호출로 모델 메모리 로드."""
    status = TierStatus(tier=tier, reachable=False)
    if not health_check(tier, timeout=3.0):
        status.error = "endpoint unreachable"
        return status
    status.reachable = True
    t0 = time.perf_counter()
    try:
        call(
            tier,
            system="Reply with a single word.",
            user="ping",
            max_tokens=10,
            temperature=0,
            auto_fallback=False,
        )
        status.warmed_up = True
        status.warmup_ms = (time.perf_counter() - t0) * 1000.0
    except Exception as exc:  # noqa: BLE001
        status.error = f"{type(exc).__name__}: {exc}"
    return status


# ── MCP ──────────────────────────────────────────────────────────


def probe_mcp(server: MCPServerInfo, *, timeout: float = 5.0) -> MCPStatus:
    status = MCPStatus(server=server.name, reachable=False)
    client = MCPClient(server, timeout=timeout)
    try:
        tools = client.list_tools()
        status.reachable = True
        status.tool_count = len(tools)
    except MCPUnavailableError as exc:
        status.error = str(exc)
    except Exception as exc:  # noqa: BLE001
        status.error = f"{type(exc).__name__}: {exc}"
    return status


# ── 통합 bootstrap ────────────────────────────────────────────────


def bootstrap(
    *,
    auto_start_stack: bool = True,
    warmup_timeout: float = DEFAULT_WARMUP_TIMEOUT,
    wait_after_start: float = 10.0,
    include_mcp: bool = True,
    tiers_to_check: tuple[Tier, ...] | None = None,
) -> BootstrapReport:
    """전 스택 헬스체크 → 필요 시 기동 → 워밍업 → MCP 프로빙 → 리포트."""
    report = BootstrapReport()
    tiers_to_check = tiers_to_check or tuple(Tier)

    # 1차 헬스체크
    any_down = any(not health_check(t, timeout=2.0) for t in tiers_to_check)
    if any_down and auto_start_stack and LLM_STACK_SCRIPT.exists():
        if invoke_stack_script():
            report.stack_script_invoked = True
            time.sleep(wait_after_start)

    # 각 티어 워밍업
    for tier in tiers_to_check:
        status = warmup_tier(tier, timeout=warmup_timeout)
        report.tiers.append(status)

    # MCP 서버 프로빙
    if include_mcp:
        for srv in list_known_servers().values():
            report.mcp_servers.append(probe_mcp(srv))

    return report
