"""프롬프트 템플릿 파일들. .md 파일은 load_prompt()로 로드."""
from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """프롬프트 파일 읽어서 반환. 확장자 생략 가능."""
    if not name.endswith(".md"):
        name = f"{name}.md"
    path = _DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def split_system_user(prompt_md: str) -> tuple[str, str]:
    """
    .md 프롬프트를 '## System' / '## User Template' 섹션으로 분리.

    반환: (system_text, user_template_text)
    """
    parts = prompt_md.split("## System", 1)
    if len(parts) < 2:
        raise ValueError("Prompt missing '## System' section")
    body = parts[1]
    sys_and_rest = body.split("## User Template", 1)
    if len(sys_and_rest) < 2:
        raise ValueError("Prompt missing '## User Template' section")
    system_text = sys_and_rest[0].strip()
    user_and_rest = sys_and_rest[1].split("## Expected Output", 1)[0]
    user_template = user_and_rest.strip()
    # 마크다운 코드블록 래퍼 제거
    if user_template.startswith("```"):
        lines = user_template.split("\n")
        user_template = "\n".join(lines[1:-1]).strip() if lines[-1].strip() == "```" else user_template
    return system_text, user_template


__all__ = ["load_prompt", "split_system_user"]
