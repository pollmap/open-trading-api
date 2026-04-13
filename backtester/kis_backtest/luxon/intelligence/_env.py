"""
간단 .env 로더 — python-dotenv 의존성 없이 stdlib만.

검색 순서:
    1. $LUXON_ENV_FILE (명시 경로)
    2. CWD/.env
    3. {backtester}/.env
    4. $HOME/.luxon.env

각 줄 파싱: KEY=VALUE. 따옴표 제거. 주석(#) 지원.
이미 설정된 env var는 덮어쓰지 않음 (시스템 env 우선).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_LINE_PATTERN = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    if env_file := os.environ.get("LUXON_ENV_FILE"):
        paths.append(Path(env_file))
    paths.append(Path.cwd() / ".env")
    # backtester/ 루트 (이 파일 기준 4단계 상위)
    paths.append(Path(__file__).resolve().parents[3] / ".env")
    if home := os.environ.get("USERPROFILE") or os.environ.get("HOME"):
        paths.append(Path(home) / ".luxon.env")
    return paths


def _parse_value(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def load_env_file(path: Path, *, override: bool = False) -> dict[str, str]:
    if not path.exists() or not path.is_file():
        return {}
    loaded: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _LINE_PATTERN.match(stripped)
        if not m:
            continue
        key, raw_value = m.group(1), m.group(2)
        # inline 주석 제거 (따옴표 밖의 #)
        if "#" in raw_value and not (raw_value.startswith('"') or raw_value.startswith("'")):
            raw_value = raw_value.split("#", 1)[0]
        value = _parse_value(raw_value)
        if override or key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def autoload() -> dict[str, list[str]]:
    """알려진 경로 전부 순회 로드. 반환: {path: [keys]}."""
    out: dict[str, list[str]] = {}
    for p in _candidate_paths():
        loaded = load_env_file(p)
        if loaded:
            out[str(p)] = list(loaded.keys())
    return out
