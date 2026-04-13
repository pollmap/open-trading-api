"""
MCP 서버 레지스트리 — kis-backtest, nexus-finance, drawio.

정적 구성 + 환경변수로 VPS 토큰 주입.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from kis_backtest.luxon.intelligence.router import Tier


@dataclass(frozen=True)
class MCPServerInfo:
    name: str
    url: str
    transport: str  # "http" | "streamable-http" | "stdio"
    default_tier: Tier
    token_env: str | None = None  # Bearer token 환경변수 키
    description: str = ""


def _kis_backtest() -> MCPServerInfo:
    return MCPServerInfo(
        name="kis-backtest",
        url=os.environ.get("KIS_BACKTEST_MCP_URL", "http://127.0.0.1:3846"),
        transport="http",
        default_tier=Tier.DEFAULT,
        description="KIS 백테스터 — 전략 실행/성과/지표",
    )


def _nexus_finance() -> MCPServerInfo:
    return MCPServerInfo(
        name="nexus-finance",
        url=os.environ.get("NEXUS_MCP_URL", "http://127.0.0.1:8100"),
        transport="http",
        default_tier=Tier.HEAVY,
        token_env="MCP_VPS_TOKEN",
        description="Nexus Finance MCP — 398도구 (DART/KRX/ECOS/재무)",
    )


def _drawio() -> MCPServerInfo:
    return MCPServerInfo(
        name="drawio",
        url=os.environ.get("DRAWIO_MCP_URL", "http://127.0.0.1:8420"),
        transport="http",
        default_tier=Tier.DEFAULT,
        description="drawio — 다이어그램 생성",
    )


def list_known_servers() -> dict[str, MCPServerInfo]:
    """기본 레지스트리. 환경변수로 URL 오버라이드 가능."""
    return {
        s.name: s
        for s in (_kis_backtest(), _nexus_finance(), _drawio())
    }


def get_server(name: str) -> MCPServerInfo:
    servers = list_known_servers()
    if name not in servers:
        raise KeyError(f"Unknown MCP server: {name}. Known: {list(servers.keys())}")
    return servers[name]


def get_auth_header(server: MCPServerInfo) -> dict[str, str]:
    if not server.token_env:
        return {}
    token = os.environ.get(server.token_env, "")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}
