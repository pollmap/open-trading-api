"""투자 서한 자동 생성기

Bill Ackman 스타일의 분기별 투자 서한을 자동 생성한다.
포트폴리오 성과, 포지션별 투자논리, 교훈을 구조화된 마크다운으로 출력.

Flow:
    LetterMetrics + List[PositionEntry]
      ↓
    LetterGenerator.generate()
      ↓
    InvestorLetter (frozen dataclass)
      ↓
    .to_markdown() / .to_blog_post() / .save()
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionEntry:
    """개별 포지션 요약"""

    symbol: str
    name: str
    weight: float
    return_pct: float
    thesis: str
    catalyst: str
    lesson: str  # 진행 중이면 빈 문자열


@dataclass(frozen=True)
class LetterMetrics:
    """분기 성과 지표"""

    period: str  # e.g. "2026-Q2"
    total_return: float
    benchmark_return: float
    alpha: float  # total - benchmark
    sharpe: float
    max_dd: float
    win_rate: float
    positions_count: int


@dataclass(frozen=True)
class InvestorLetter:
    """투자 서한 (불변)"""

    period: str
    metrics: LetterMetrics
    positions: List[PositionEntry]
    macro_regime: str
    outlook: str
    created_at: str
    author: str = "Luxon AI"
    fund_name: str = "Luxon Quant Fund"

    def to_markdown(self) -> str:
        """Ackman 스타일 마크다운 서한 생성"""
        m = self.metrics
        lines: list[str] = []

        lines.append(f"# {self.fund_name} 투자 서한 — {self.period}")
        lines.append("")
        lines.append("## 성과 요약")
        lines.append("| 지표 | 값 |")
        lines.append("|------|-----|")
        lines.append(f"| 총 수익률 | {m.total_return:.2f}% |")
        lines.append(f"| 벤치마크 | {m.benchmark_return:.2f}% |")
        lines.append(f"| 알파 | {m.alpha:.2f}% |")
        lines.append(f"| Sharpe | {m.sharpe:.2f} |")
        lines.append(f"| 최대 낙폭 | {m.max_dd:.2f}% |")
        lines.append(f"| 승률 | {m.win_rate:.2f}% |")
        lines.append("")

        lines.append("## 매크로 환경")
        lines.append(self.macro_regime)
        lines.append("")

        lines.append("## 포지션별 분석")
        for pos in self.positions:
            lines.append(
                f"### {pos.symbol} ({pos.name}) — 비중 {pos.weight:.1f}%"
            )
            lines.append(f"**논리:** {pos.thesis}")
            lines.append(f"**카탈리스트:** {pos.catalyst}")
            lines.append(f"**수익률:** {pos.return_pct:.2f}%")
            lesson_text = pos.lesson if pos.lesson else "(진행 중)"
            lines.append(f"**교훈:** {lesson_text}")
            lines.append("")

        lines.append("## 전망")
        lines.append(self.outlook)
        lines.append("")

        lines.append("---")
        lines.append(f"*{self.author} | {self.created_at}*")

        return "\n".join(lines)

    def to_blog_post(self) -> Dict[str, str]:
        """블로그 포스트 형태로 변환"""
        title = f"{self.fund_name} {self.period} 투자 서한"
        slug = _slugify(f"{self.fund_name}-{self.period}")
        content = self.to_markdown()
        return {"title": title, "slug": slug, "content": content}


def _slugify(text: str) -> str:
    """텍스트를 URL-safe slug로 변환"""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


class LetterGenerator:
    """투자 서한 생성기"""

    def __init__(
        self,
        author: str = "Luxon AI",
        fund_name: str = "Luxon Quant Fund",
    ) -> None:
        self.author = author
        self.fund_name = fund_name

    def generate(
        self,
        period: str,
        metrics: LetterMetrics,
        positions: List[PositionEntry],
        macro_regime: str,
        outlook: str,
    ) -> InvestorLetter:
        """투자 서한 생성"""
        if not period:
            raise ValueError("period는 비어있을 수 없습니다")
        if not positions:
            raise ValueError("positions는 비어있을 수 없습니다")

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        logger.info("투자 서한 생성: period=%s, positions=%d", period, len(positions))

        return InvestorLetter(
            period=period,
            metrics=metrics,
            positions=list(positions),
            macro_regime=macro_regime,
            outlook=outlook,
            created_at=created_at,
            author=self.author,
            fund_name=self.fund_name,
        )

    def generate_from_reviews(
        self,
        review_snapshots: List[Dict[str, Any]],
        period: str,
    ) -> InvestorLetter:
        """ReviewEngine 스냅샷으로부터 투자 서한 자동 생성

        review_snapshots 형태 예시:
        [
            {
                "symbol": "005930",
                "name": "삼성전자",
                "weight": 25.0,
                "return_pct": 12.5,
                "thesis": "반도체 사이클 저점 진입",
                "catalyst": "HBM 수주 확대",
                "lesson": "",
                "portfolio_return": 8.5,
                "benchmark_return": 3.2,
                "sharpe": 1.2,
                "max_dd": -5.3,
                "win_rate": 65.0,
                "macro_regime": "확장기",
                "outlook": "하반기 반도체 업사이클 기대",
            }
        ]
        """
        if not review_snapshots:
            raise ValueError("review_snapshots는 비어있을 수 없습니다")

        positions: list[PositionEntry] = []
        total_return = 0.0
        benchmark_return = 0.0
        sharpe_sum = 0.0
        max_dd = 0.0
        win_count = 0
        macro_regime = ""
        outlook = ""

        for snap in review_snapshots:
            positions.append(
                PositionEntry(
                    symbol=snap["symbol"],
                    name=snap["name"],
                    weight=snap.get("weight", 0.0),
                    return_pct=snap.get("return_pct", 0.0),
                    thesis=snap.get("thesis", ""),
                    catalyst=snap.get("catalyst", ""),
                    lesson=snap.get("lesson", ""),
                )
            )
            total_return += snap.get("return_pct", 0.0) * snap.get("weight", 0.0) / 100.0
            if snap.get("return_pct", 0.0) > 0:
                win_count += 1

        # 첫 스냅샷에서 포트폴리오 수준 지표 추출
        first = review_snapshots[0]
        benchmark_return = first.get("benchmark_return", 0.0)
        sharpe_val = first.get("sharpe", 0.0)
        max_dd = first.get("max_dd", 0.0)
        macro_regime = first.get("macro_regime", "정보 없음")
        outlook = first.get("outlook", "정보 없음")

        win_rate = (win_count / len(positions)) * 100.0 if positions else 0.0

        metrics = LetterMetrics(
            period=period,
            total_return=round(total_return, 2),
            benchmark_return=benchmark_return,
            alpha=round(total_return - benchmark_return, 2),
            sharpe=sharpe_val,
            max_dd=max_dd,
            win_rate=round(win_rate, 2),
            positions_count=len(positions),
        )

        return self.generate(
            period=period,
            metrics=metrics,
            positions=positions,
            macro_regime=macro_regime,
            outlook=outlook,
        )

    def save(self, letter: InvestorLetter, output_dir: str) -> str:
        """투자 서한을 .md 파일로 저장"""
        os.makedirs(output_dir, exist_ok=True)
        filename = f"investor_letter_{letter.period}.md"
        filepath = os.path.join(output_dir, filename)

        content = letter.to_markdown()
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info("투자 서한 저장: %s", filepath)
        return filepath
