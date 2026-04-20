"""Claude Code ↔ OpenCode 양방향 동기화.

매시간 자동 실행 (Luxon-Config-Sync Task Scheduler).

동기화 항목:
    1. ~/.mcp.json              → opencode.json "mcp" 섹션
    2. CLAUDE.md (전역)         → AGENTS.md "## CLAUDE.md 스냅샷" 섹션 자동 주입
    3. MEMORY.md 인덱스          → AGENTS.md "## 메모리 참조" 섹션
    4. gh auth token            → GITHUB_TOKEN User env var

수동:
    python scripts/sync_claude_opencode.py
    python scripts/sync_claude_opencode.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
MCP_JSON = HOME / ".mcp.json"
OPENCODE_JSON = HOME / ".config" / "opencode" / "opencode.json"
CLAUDE_MD = HOME / "CLAUDE.md"
MEMORY_INDEX = HOME / ".claude" / "projects" / "C--Users-lch68" / "memory" / "MEMORY.md"
AGENTS_MD = Path(__file__).resolve().parent.parent / "AGENTS.md"

SNAPSHOT_MARK_START = "<!-- CLAUDE_SYNC_START -->"
SNAPSHOT_MARK_END = "<!-- CLAUDE_SYNC_END -->"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("luxon.sync")


def sync_mcp_servers(*, dry_run: bool) -> bool:
    """~/.mcp.json mcpServers → opencode.json mcp."""
    if not MCP_JSON.exists() or not OPENCODE_JSON.exists():
        logger.warning("파일 누락, MCP sync 스킵")
        return False

    claude_cfg = json.loads(MCP_JSON.read_text(encoding="utf-8"))
    opencode_cfg = json.loads(OPENCODE_JSON.read_text(encoding="utf-8"))

    existing = opencode_cfg.setdefault("mcp", {})
    changed = False

    # 제외 목록 — 수동 유지 필요한 서버 (venv 경로 등 특수 설정)
    SKIP = {"gitlawb", "career-ops-kr"}
    # 공식 OpenCode 스키마: command=[cmd, ...args] 단일 배열, environment(env 아님), enabled/timeout 유효
    for name, spec in claude_cfg.get("mcpServers", {}).items():
        if name in SKIP:
            continue
        if spec.get("type") == "http":
            new_spec = {"type": "remote", "url": spec["url"], "enabled": True}
        elif "command" in spec:
            cmd = [spec["command"]] + list(spec.get("args", []))
            new_spec = {
                "type": "local",
                "command": cmd,
                "enabled": True,
                "environment": spec.get("env", {}),
            }
        else:
            continue

        if existing.get(name) != new_spec:
            existing[name] = new_spec
            changed = True
            logger.info(f"MCP {name} 업데이트")

    if changed and not dry_run:
        OPENCODE_JSON.write_text(
            json.dumps(opencode_cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"opencode.json 저장 ({len(existing)} MCP 서버)")
    return changed


def _extract_section(text: str, start: str, end: str) -> tuple[str, str, str]:
    """(before, inside, after) 튜플."""
    i = text.find(start)
    j = text.find(end)
    if i == -1 or j == -1 or j <= i:
        return text, "", ""
    return text[:i], text[i + len(start):j], text[j + len(end):]


def sync_claude_snapshot(*, dry_run: bool) -> bool:
    """CLAUDE.md 핵심 → AGENTS.md 섹션 자동 주입."""
    if not CLAUDE_MD.exists() or not AGENTS_MD.exists():
        return False

    claude_content = CLAUDE_MD.read_text(encoding="utf-8")
    # 너무 길면 잘라서 주입 (AGENTS.md 비대 방지)
    max_len = 8000
    excerpt = claude_content[:max_len]
    if len(claude_content) > max_len:
        excerpt += "\n\n... [생략 — 원본: <HOME>/CLAUDE.md]"

    snapshot = (
        f"\n\n## CLAUDE.md 자동 스냅샷\n\n"
        f"`<HOME>/CLAUDE.md` 내용 매시간 동기화. 원본 수정 권장.\n\n"
        f"```markdown\n{excerpt}\n```\n"
    )

    agents = AGENTS_MD.read_text(encoding="utf-8")
    before, _, after = _extract_section(agents, SNAPSHOT_MARK_START, SNAPSHOT_MARK_END)

    new_content = (
        before.rstrip() + "\n\n"
        + SNAPSHOT_MARK_START + snapshot + SNAPSHOT_MARK_END
        + (after if after else "\n")
    )

    if new_content == agents:
        return False
    if dry_run:
        logger.info("[DRY] AGENTS.md CLAUDE 스냅샷 갱신 대기")
        return True

    AGENTS_MD.write_text(new_content, encoding="utf-8")
    logger.info("AGENTS.md CLAUDE 스냅샷 갱신")
    return True


def sync_github_token(*, dry_run: bool) -> bool:
    """gh auth token → GITHUB_TOKEN User env var."""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            logger.warning("gh auth token 실패")
            return False
        token = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False

    current = os.environ.get("GITHUB_TOKEN", "")
    if current == token:
        return False

    if dry_run:
        logger.info("[DRY] GITHUB_TOKEN env 갱신 대기")
        return True

    # Windows User-scope env
    if sys.platform == "win32":
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"[Environment]::SetEnvironmentVariable('GITHUB_TOKEN', '{token}', 'User')"],
            timeout=10, check=False,
        )
        logger.info("GITHUB_TOKEN User env 갱신 (재로그인 시 반영)")
    else:
        logger.info("non-Windows: .bashrc 수동 추가 필요")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    changes = 0
    if sync_mcp_servers(dry_run=args.dry_run):
        changes += 1
    if sync_claude_snapshot(dry_run=args.dry_run):
        changes += 1
    if sync_github_token(dry_run=args.dry_run):
        changes += 1

    logger.info(f"sync 완료: {changes}건 변경")
    return 0


if __name__ == "__main__":
    sys.exit(main())
