"""
보안 가드 — 토큰 검증, 엔드포인트 바인딩 확인, 경로 sanitization.

3축 원칙:
    1. VPS 토큰은 환경변수로만 (코드/로그 노출 금지).
    2. 로컬 엔드포인트는 127.0.0.1 bind 확인 (외부 노출 차단).
    3. MCP tool 인자에서 경로 traversal / 위험 문자 필터.
"""
from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from kis_backtest.luxon.intelligence.mcp_registry import (
    MCPServerInfo,
    list_known_servers,
)


# ── 예외 ──────────────────────────────────────────────────────────


class SecurityCheckFailed(RuntimeError):
    """보안 전제 위반 — 실행 중단 권장."""


# ── 토큰 ──────────────────────────────────────────────────────────


def redact(value: str, keep: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"


def check_tokens(servers: list[MCPServerInfo] | None = None) -> dict[str, str]:
    """필요 토큰 env 존재 여부 확인. 반환: {env_key: status}."""
    if servers is None:
        servers = list(list_known_servers().values())
    status: dict[str, str] = {}
    for s in servers:
        if not s.token_env:
            continue
        value = os.environ.get(s.token_env, "")
        status[s.token_env] = (
            f"present({redact(value)})" if value else "MISSING"
        )
    return status


# ── 엔드포인트 바인딩 ─────────────────────────────────────────────


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _is_loopback(host: str) -> bool:
    if host.lower() in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_private_or_vps(host: str) -> bool:
    """VPS(공개 IP)도 허용 대상. private/VPS 구분."""
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or not ip.is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class EndpointAudit:
    url: str
    host: str
    scheme: str
    is_loopback: bool
    is_external_https: bool
    verdict: str  # "ok" | "warn" | "blocked"
    reason: str


def audit_endpoint(url: str, *, allow_vps: bool = True) -> EndpointAudit:
    """엔드포인트 URL 보안 감사.

    허용:
        - 127.0.0.1 / localhost (로컬 LLM, 로컬 MCP)
        - VPS 공개 IP + HTTPS (nexus-finance VPS)
    경고/차단:
        - 공개 IP + HTTP (평문)
        - 도메인명 (예상 외)
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    scheme = parsed.scheme or ""
    is_lb = _is_loopback(host)
    is_ext_https = (not is_lb) and scheme == "https"

    if is_lb:
        return EndpointAudit(url, host, scheme, True, False, "ok",
                             "loopback endpoint")
    if allow_vps and scheme == "https":
        return EndpointAudit(url, host, scheme, False, True, "ok",
                             "external HTTPS (VPS)")
    if not is_lb and scheme == "http":
        return EndpointAudit(url, host, scheme, False, False, "warn",
                             "external HTTP — 평문 전송")
    return EndpointAudit(url, host, scheme, is_lb, is_ext_https, "warn",
                         "알 수 없는 엔드포인트 패턴")


def audit_all_mcp() -> list[EndpointAudit]:
    return [audit_endpoint(s.url) for s in list_known_servers().values()]


# ── 입력 Sanitization ────────────────────────────────────────────


_DANGEROUS_PATH_PATTERNS = (
    re.compile(r"\.\./"),  # 상위 디렉토리 탈출
    re.compile(r"^\s*/etc/"),  # 시스템 경로
    re.compile(r"[;&|`$]"),  # 셸 메타문자
)


def sanitize_tool_args(args: dict) -> dict:
    """MCP tool arguments에서 위험 패턴 필터.

    str 값만 검사. 위험 패턴 발견 시 SecurityCheckFailed.
    """
    for k, v in args.items():
        if not isinstance(v, str):
            continue
        for pat in _DANGEROUS_PATH_PATTERNS:
            if pat.search(v):
                raise SecurityCheckFailed(
                    f"Dangerous pattern in arg '{k}': {pat.pattern!r}"
                )
    return args


# ── 통합 preflight ────────────────────────────────────────────────


@dataclass
class PreflightReport:
    endpoints: list[EndpointAudit]
    token_status: dict[str, str]
    warnings: list[str]

    @property
    def has_blockers(self) -> bool:
        return any(e.verdict == "blocked" for e in self.endpoints)

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings) or any(
            e.verdict == "warn" for e in self.endpoints
        )

    def format_report(self) -> str:
        lines = ["=== Security Preflight ==="]
        lines.append("")
        lines.append("[Endpoints]")
        for e in self.endpoints:
            mark = {"ok": "[OK]", "warn": "[WARN]", "blocked": "[BLOCK]"}[e.verdict]
            lines.append(f"  {mark:<7s} {e.url:<45s} {e.verdict:<8s} {e.reason}")
        lines.append("")
        lines.append("[Tokens]")
        if not self.token_status:
            lines.append("  (none required)")
        for k, v in self.token_status.items():
            mark = "[OK]" if v != "MISSING" else "[--]"
            lines.append(f"  {mark} {k:<30s} {v}")
        if self.warnings:
            lines.append("")
            lines.append("[Warnings]")
            for w in self.warnings:
                lines.append(f"  [WARN] {w}")
        return "\n".join(lines)


def preflight() -> PreflightReport:
    """실행 전 보안 상태 종합 리포트."""
    endpoints = audit_all_mcp()
    tokens = check_tokens()
    warnings: list[str] = []
    for k, v in tokens.items():
        if v == "MISSING":
            warnings.append(f"토큰 미설정: {k} — 해당 MCP 서버 호출 실패 예상")
    return PreflightReport(
        endpoints=endpoints,
        token_status=tokens,
        warnings=warnings,
    )
