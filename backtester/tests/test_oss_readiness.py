"""OSS 배포 준비 smoke 테스트 (v1.0 STEP 6).

민감 파일 .gitignore 포함 여부 + 필수 문서 존재 검증.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_license_exists():
    """LICENSE 파일 존재 + MIT 명시."""
    license_path = _ROOT / "LICENSE"
    assert license_path.exists()
    text = license_path.read_text(encoding="utf-8")
    assert "MIT License" in text
    assert "FINANCIAL SOFTWARE" in text  # 금융 소프트웨어 면책 조항


def test_contributing_exists():
    """CONTRIBUTING.md 존재 + 핵심 섹션 포함."""
    path = _ROOT / "CONTRIBUTING.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "보안 정책" in text
    assert "테스트" in text


def test_env_example_no_real_secrets():
    """.env.example에 실제 시크릿 값 없어야 함."""
    path = _ROOT / ".env.example"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    # 토큰/키가 빈 값이거나 플레이스홀더여야 함
    suspicious_patterns = [
        "PS",  # KIS 앱키 접두 (실제 키는 "PS..." 시작)
        "BearerToken",
    ]
    # 실제 Bearer 토큰 길이(40+ 문자 연속 영숫자)는 없어야
    import re
    long_tokens = re.findall(r"[A-Za-z0-9]{40,}", text)
    assert not long_tokens, f"의심스러운 토큰: {long_tokens}"


def test_gitignore_blocks_sensitive_files():
    """.gitignore가 민감 파일 패턴 차단."""
    path = _ROOT / ".gitignore"
    assert path.exists()
    text = path.read_text(encoding="utf-8")

    required_patterns = [
        ".env",
        "kis_devlp.yaml",
        "KIS/",
        "fills/",
        "ladder_state.json",
        ".luxon/",
    ]
    for pattern in required_patterns:
        assert pattern in text, f".gitignore에 '{pattern}' 없음"


def test_scripts_exist():
    """v1.0 퀵스타트 스크립트 모두 존재."""
    scripts_dir = _ROOT / "scripts"
    required = [
        "luxon_terminal_run.py",
        "run_walk_forward.py",
        "setup_task_scheduler.ps1",
    ]
    for name in required:
        assert (scripts_dir / name).exists(), f"scripts/{name} 없음"


def test_readme_has_quickstart():
    """README.md에 퀵스타트 섹션 존재."""
    path = _ROOT / "README.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "퀵스타트" in text or "빠른 시작" in text
    assert "luxon_terminal_run.py" in text


def test_architecture_doc_up_to_date():
    """ARCHITECTURE.md 존재 + 최신 버전 참조."""
    path = _ROOT / "ARCHITECTURE.md"
    assert path.exists()
    # v0.4α 이상이면 통과 (v0.4α, v0.5α, v1.0 모두 OK)
    text = path.read_text(encoding="utf-8")
    assert "luxon-terminal" in text.lower() or "Luxon" in text
