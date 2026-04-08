#!/usr/bin/env python3
"""복기 → 블로그 + Vault 피드백 루프

주간 복기 결과를 Luxon 블로그와 Obsidian Vault에 자동 발행.
ReviewScheduler → VaultWriter → publish_review.py → luxon-blog

Usage:
    # 주간 복기 결과 발행
    python scripts/publish_review.py --type weekly

    # 월간 퀀트 리포트 발행
    python scripts/publish_review.py --type monthly --title "2026년 4월 퀀트 리포트"

    # 마일스톤 업데이트 발행
    python scripts/publish_review.py --type milestone --title "v0.3α: Walk-Forward + Capital Ladder"
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("publish_review")

# 블로그 레포 경로 (로컬 클론)
BLOG_REPO = Path.home() / "Desktop" / "luxon-blog-fix"
BLOG_CONTENT_DIR = BLOG_REPO / "src" / "content" / "blog"

# Vault 경로
VAULT_ROOT = Path.home() / "obsidian-vault"
VAULT_TRADING = VAULT_ROOT / "02-Areas" / "trading-ops"


def _load_latest_weekly_report() -> Optional[dict]:
    """최신 주간 복기 리포트 로드 (Vault에서)"""
    weekly_dir = VAULT_TRADING / "weekly"
    if not weekly_dir.exists():
        return None

    md_files = sorted(weekly_dir.glob("*.md"), reverse=True)
    if not md_files:
        return None

    content = md_files[0].read_text(encoding="utf-8")
    return {
        "file": str(md_files[0]),
        "content": content,
        "filename": md_files[0].stem,
    }


def _load_latest_equity_curve() -> list[dict]:
    """최신 자산 곡선 CSV 로드"""
    csv_path = VAULT_TRADING / "equity_curve.csv"
    if not csv_path.exists():
        return []

    import csv
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows[-30:]  # 최근 30일


def _build_weekly_post(report: dict) -> tuple[str, str, str]:
    """주간 복기 → 블로그 포스트 변환

    Returns:
        (title, slug, content)
    """
    week_id = report["filename"]  # e.g., "2026-W14"
    title = f"Luxon Quant 주간 복기 — {week_id}"
    slug = f"quant-weekly-{week_id.lower()}"

    content = f"""
## {week_id} 주간 퀀트 복기

{report['content']}

---

이 복기는 Luxon AI 퀀트 파이프라인의 자동 복기 시스템(ReviewScheduler)에서 생성되었습니다.

- 파이프라인: QuantPipeline → RiskGateway → Capital Ladder → VaultWriter
- 도구: Nexus Finance MCP 398도구
- 코드: [pollmap/open-trading-api](https://github.com/pollmap/open-trading-api)
"""
    return title, slug, content


def _build_milestone_post(title: str, body: str) -> tuple[str, str, str]:
    """마일스톤 업데이트 → 블로그 포스트"""
    slug = title.lower().replace(" ", "-").replace(":", "")[:50]
    # 한글/특수문자 제거
    import re
    slug = re.sub(r'[^a-z0-9-]', '', slug).strip('-')
    if not slug:
        slug = f"update-{datetime.now().strftime('%Y%m%d')}"

    return title, slug, body


def _publish_to_blog(title: str, slug: str, content: str, tags: list[str]) -> Optional[str]:
    """luxon-blog에 발행

    Returns:
        발행된 URL 또는 None
    """
    if not BLOG_CONTENT_DIR.exists():
        logger.warning("블로그 디렉토리 없음: %s", BLOG_CONTENT_DIR)
        logger.info("luxon-blog 클론 필요: git clone https://github.com/pollmap/luxon-blog.git ~/Desktop/luxon-blog-fix")
        return None

    today = datetime.now().strftime("%Y-%m-%d")
    tags_str = "[" + ", ".join(f'"{t}"' for t in tags) + "]"

    frontmatter = f"""---
title: "{title}"
date: {today}
description: "{title}"
tags: {tags_str}
---

"""
    # Astro 호환: ** → <strong>
    import re
    processed = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content, flags=re.DOTALL)
    processed = re.sub(r'\*\*', '', processed)

    filepath = BLOG_CONTENT_DIR / f"{today}-{slug}.md"
    filepath.write_text(frontmatter + processed, encoding="utf-8")
    logger.info("블로그 파일 생성: %s", filepath)

    # Git commit + push
    try:
        subprocess.run(["git", "add", "-A"], cwd=BLOG_REPO, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"post: {title}"],
            cwd=BLOG_REPO, check=True,
        )
        result = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=BLOG_REPO, capture_output=True, text=True,
        )
        if result.returncode == 0:
            url = f"https://pollmap.github.io/luxon-blog/blog/{today}-{slug}/"
            logger.info("발행 완료: %s", url)
            return url
        else:
            logger.error("Push 실패: %s", result.stderr)
    except Exception as e:
        logger.error("Git 작업 실패: %s", e)

    return None


def _save_to_vault(title: str, content: str, blog_url: Optional[str] = None) -> Path:
    """Vault에 발행 기록 저장"""
    publish_dir = VAULT_ROOT / "02-Areas" / "trading-ops" / "published"
    publish_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        "---",
        f"date: {today}",
        f"title: \"{title}\"",
        "type: published-review",
        f"blog_url: \"{blog_url or 'N/A'}\"",
        "tags: [trading, published, review]",
        "---",
        "",
        f"# {title}",
        "",
        content,
    ]

    if blog_url:
        lines.extend(["", f"발행 URL: [{blog_url}]({blog_url})"])

    filepath = publish_dir / f"{today}-{title[:30]}.md"
    filepath.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Vault 발행 기록: %s", filepath)
    return filepath


def main() -> int:
    parser = argparse.ArgumentParser(description="복기 → 블로그 + Vault 피드백 루프")
    parser.add_argument("--type", choices=["weekly", "monthly", "milestone"], default="weekly")
    parser.add_argument("--title", default="")
    parser.add_argument("--body", default="")
    parser.add_argument("--tags", default="퀀트,Luxon,AI")
    parser.add_argument("--dry-run", action="store_true", help="발행 없이 미리보기만")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    tags = [t.strip() for t in args.tags.split(",")]

    if args.type == "weekly":
        report = _load_latest_weekly_report()
        if not report:
            print("주간 복기 파일 없음 — ReviewScheduler 먼저 실행하세요")
            return 1
        title, slug, content = _build_weekly_post(report)
        tags.extend(["주간복기", "포트폴리오"])

    elif args.type == "milestone":
        title = args.title or f"Luxon Quant 업데이트 {datetime.now().strftime('%Y-%m-%d')}"
        body = args.body or sys.stdin.read()
        title, slug, content = _build_milestone_post(title, body)
        tags.extend(["마일스톤", "업데이트"])

    else:
        print(f"미지원 타입: {args.type}")
        return 1

    print(f"\n{'='*60}")
    print(f"  제목: {title}")
    print(f"  태그: {', '.join(tags)}")
    print(f"  길이: {len(content)}자")
    print(f"{'='*60}\n")

    if args.dry_run:
        print(content[:500])
        print("\n... (dry-run, 발행 안 함)")
        return 0

    # 블로그 발행
    blog_url = _publish_to_blog(title, slug, content, tags)

    # Vault 기록
    _save_to_vault(title, content, blog_url)

    print(f"\n발행 완료!")
    if blog_url:
        print(f"  블로그: {blog_url}")
    print(f"  Vault: 02-Areas/trading-ops/published/")

    return 0


if __name__ == "__main__":
    sys.exit(main())
