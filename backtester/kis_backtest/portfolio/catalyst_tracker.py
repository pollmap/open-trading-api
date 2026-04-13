"""카탈리스트 트래커 — Ackman의 "왜 지금?" 시스템

종목별 카탈리스트(실적발표, 규제변화, M&A 등)를 등록·추적하고,
날짜×확률×영향도 → 카탈리스트 스코어를 산출한다.

Bill Ackman 철학: "카탈리스트 없으면 매수 없다."
- 카탈리스트 스코어 ≥ threshold → conviction_sizer 비중 증가
- 스코어 0 → 매수 보류 (아무리 저평가여도)

Usage:
    from kis_backtest.portfolio.catalyst_tracker import CatalystTracker

    tracker = CatalystTracker()

    # 카탈리스트 등록
    tracker.add(
        symbol="005930",
        name="삼성전자 HBM4 양산",
        catalyst_type="industry",
        expected_date="2026-06-15",
        probability=0.7,
        impact=8,
        description="SK하이닉스 독점 깨고 HBM4 납품 시작 예상",
    )

    # 종목 스코어 조회
    score = tracker.score("005930")
    print(score)  # CatalystScore(symbol='005930', total=5.6, ...)

    # DART 공시 자동 스캔 (MCP 연동)
    new_catalysts = await tracker.scan_dart("005930", mcp_provider)
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

if TYPE_CHECKING:
    from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider

logger = logging.getLogger(__name__)


class CatalystType(str, Enum):
    """카탈리스트 유형"""
    EARNINGS = "earnings"          # 실적 발표 / 어닝 서프라이즈
    REGULATION = "regulation"      # 규제 변화 / 정책
    MA = "ma"                      # M&A / 구조조정
    INDUSTRY = "industry"          # 산업 이벤트 / 기술 변화
    MACRO = "macro"                # 거시경제 (금리, 환율)
    DIVIDEND = "dividend"          # 배당 / 자사주 매입
    MANAGEMENT = "management"      # 경영진 변경 / 지배구조
    VALUATION = "valuation"        # 밸류에이션 리레이팅
    TECHNICAL = "technical"        # 기술적 지표 신호 (RSI/MACD/Bollinger)


# 유형별 기본 영향도 범위 가이드 (참고용)
TYPE_IMPACT_GUIDE: Dict[CatalystType, str] = {
    CatalystType.EARNINGS: "±3~8 (서프라이즈 정도)",
    CatalystType.REGULATION: "±5~10 (규제 강도)",
    CatalystType.MA: "±7~10 (딜 규모)",
    CatalystType.INDUSTRY: "±3~8 (구조적 변화)",
    CatalystType.MACRO: "±2~6 (민감도)",
    CatalystType.DIVIDEND: "+2~5 (수익률)",
    CatalystType.TECHNICAL: "±2~7 (신호 강도)",
    CatalystType.MANAGEMENT: "±3~7 (신임도)",
    CatalystType.VALUATION: "+2~6 (갭 크기)",
}


@dataclass(frozen=True)
class Catalyst:
    """단일 카탈리스트

    Attributes:
        symbol: 종목 코드
        name: 카탈리스트 이름
        catalyst_type: 유형
        expected_date: 예상 발생일 (YYYY-MM-DD)
        probability: 실현 확률 (0.0 ~ 1.0)
        impact: 영향도 (-10 ~ +10, 양수=긍정, 음수=부정)
        description: 상세 설명
        source: 출처 (DART, 뉴스, 분석 등)
        created_at: 등록 시점
        resolved: 해결 여부
        resolved_at: 해결 시점
        actual_impact: 실제 영향도 (사후 평가)
    """
    symbol: str
    name: str
    catalyst_type: CatalystType
    expected_date: str
    probability: float
    impact: float
    description: str = ""
    source: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    resolved: bool = False
    resolved_at: Optional[str] = None
    actual_impact: Optional[float] = None

    @property
    def id(self) -> str:
        """고유 ID (symbol + name hash)"""
        return f"{self.symbol}_{hash(self.name) & 0xFFFF:04x}"

    @property
    def days_until(self) -> int:
        """예상일까지 남은 일수 (음수 = 이미 지남)"""
        try:
            target = datetime.strptime(self.expected_date, "%Y-%m-%d").date()
            return (target - date.today()).days
        except ValueError:
            return 999

    @property
    def time_weight(self) -> float:
        """시간 가중치 — 가까울수록 높음 (Gaussian decay)

        0일 → 1.0, 30일 → 0.61, 90일 → 0.14, 180일+ → ~0
        이미 지난 카탈리스트: 7일까지는 1.0 유지, 이후 급감
        """
        d = self.days_until
        if d < -30:
            return 0.0
        if d < 0:
            # 이미 지남: 7일까지 유효, 이후 감소
            return max(0.0, 1.0 - abs(d) / 30)
        # Gaussian decay: σ = 60일
        return math.exp(-(d ** 2) / (2 * 60 ** 2))

    @property
    def weighted_score(self) -> float:
        """단일 카탈리스트 가중 스코어

        = probability × abs(impact) × time_weight
        부정적 카탈리스트는 음수 스코어.
        """
        sign = 1.0 if self.impact >= 0 else -1.0
        return sign * self.probability * abs(self.impact) * self.time_weight


@dataclass(frozen=True)
class CatalystScore:
    """종목별 카탈리스트 종합 스코어"""
    symbol: str
    total: float                      # 종합 스코어 (양수=긍정적 카탈리스트 우세)
    positive_score: float             # 긍정 카탈리스트 합산
    negative_score: float             # 부정 카탈리스트 합산
    catalyst_count: int               # 활성 카탈리스트 수
    top_catalyst: Optional[str]       # 최대 영향 카탈리스트 이름
    urgency: str                      # "imminent" / "near" / "distant" / "none"

    @property
    def has_catalyst(self) -> bool:
        """카탈리스트가 존재하는가?"""
        return self.catalyst_count > 0

    @property
    def is_actionable(self) -> bool:
        """Ackman 기준: 매수 가능한 수준인가? (스코어 ≥ 2.0)"""
        return self.total >= 2.0

    def summary(self) -> str:
        """한줄 요약"""
        status = "🟢 매수가능" if self.is_actionable else "🔴 보류"
        return (
            f"[{self.symbol}] 스코어={self.total:.1f} "
            f"(+{self.positive_score:.1f}/-{self.negative_score:.1f}) "
            f"카탈리스트={self.catalyst_count} "
            f"긴급도={self.urgency} {status}"
        )


class CatalystTracker:
    """카탈리스트 추적기

    종목별 카탈리스트를 관리하고, 파이프라인에서 사용할 스코어를 산출한다.
    JSON 파일로 상태를 영속화.
    """

    def __init__(self, state_file: Optional[str] = None) -> None:
        self._catalysts: List[Catalyst] = []
        self._state_file = state_file
        if state_file:
            self._load(state_file)

    # ── 카탈리스트 CRUD ─────────────────────────────────────

    def add(
        self,
        symbol: str,
        name: str,
        catalyst_type: str | CatalystType,
        expected_date: str,
        probability: float,
        impact: float,
        description: str = "",
        source: str = "",
    ) -> Catalyst:
        """카탈리스트 등록

        Raises:
            ValueError: probability가 0~1 범위 밖이거나 impact가 -10~+10 밖일 때
        """
        if not 0.0 <= probability <= 1.0:
            raise ValueError(f"probability는 0~1 범위: {probability}")
        if not -10.0 <= impact <= 10.0:
            raise ValueError(f"impact는 -10~+10 범위: {impact}")

        if isinstance(catalyst_type, str):
            catalyst_type = CatalystType(catalyst_type)

        catalyst = Catalyst(
            symbol=symbol,
            name=name,
            catalyst_type=catalyst_type,
            expected_date=expected_date,
            probability=probability,
            impact=impact,
            description=description,
            source=source,
        )
        self._catalysts.append(catalyst)
        self._save()
        logger.info("카탈리스트 등록: %s %s (스코어=%.1f)", symbol, name, catalyst.weighted_score)
        return catalyst

    def remove(self, symbol: str, name: str) -> bool:
        """이름으로 카탈리스트 제거"""
        before = len(self._catalysts)
        self._catalysts = [
            c for c in self._catalysts
            if not (c.symbol == symbol and c.name == name)
        ]
        removed = len(self._catalysts) < before
        if removed:
            self._save()
        return removed

    def resolve(
        self,
        symbol: str,
        name: str,
        actual_impact: Optional[float] = None,
    ) -> Optional[Catalyst]:
        """카탈리스트를 해결됨으로 표시 (사후 평가용)"""
        for i, c in enumerate(self._catalysts):
            if c.symbol == symbol and c.name == name:
                resolved = Catalyst(
                    symbol=c.symbol,
                    name=c.name,
                    catalyst_type=c.catalyst_type,
                    expected_date=c.expected_date,
                    probability=c.probability,
                    impact=c.impact,
                    description=c.description,
                    source=c.source,
                    created_at=c.created_at,
                    resolved=True,
                    resolved_at=datetime.now().strftime("%Y-%m-%d"),
                    actual_impact=actual_impact,
                )
                self._catalysts[i] = resolved
                self._save()
                return resolved
        return None

    def list_by_symbol(self, symbol: str, active_only: bool = True) -> List[Catalyst]:
        """종목별 카탈리스트 목록"""
        result = [c for c in self._catalysts if c.symbol == symbol]
        if active_only:
            result = [c for c in result if not c.resolved and c.time_weight > 0]
        return sorted(result, key=lambda c: c.weighted_score, reverse=True)

    def list_all_active(self) -> List[Catalyst]:
        """전체 활성 카탈리스트"""
        return [
            c for c in self._catalysts
            if not c.resolved and c.time_weight > 0
        ]

    def symbols_with_catalysts(self) -> List[str]:
        """카탈리스트가 있는 종목 목록"""
        return list({c.symbol for c in self.list_all_active()})

    # ── 스코어링 ─────────────────────────────────────────────

    def score(self, symbol: str) -> CatalystScore:
        """종목별 카탈리스트 종합 스코어 산출

        Ackman 원칙: 카탈리스트가 없으면 매수 보류.
        스코어 = Σ(probability × impact × time_weight)
        """
        active = self.list_by_symbol(symbol, active_only=True)

        if not active:
            return CatalystScore(
                symbol=symbol,
                total=0.0,
                positive_score=0.0,
                negative_score=0.0,
                catalyst_count=0,
                top_catalyst=None,
                urgency="none",
            )

        positive = sum(c.weighted_score for c in active if c.weighted_score > 0)
        negative = sum(c.weighted_score for c in active if c.weighted_score < 0)
        total = positive + negative

        # 최대 영향 카탈리스트
        top = max(active, key=lambda c: abs(c.weighted_score))

        # 긴급도: 가장 가까운 카탈리스트 기준
        min_days = min(c.days_until for c in active)
        if min_days <= 7:
            urgency = "imminent"
        elif min_days <= 30:
            urgency = "near"
        else:
            urgency = "distant"

        return CatalystScore(
            symbol=symbol,
            total=round(total, 2),
            positive_score=round(positive, 2),
            negative_score=round(negative, 2),
            catalyst_count=len(active),
            top_catalyst=top.name,
            urgency=urgency,
        )

    def score_all(self) -> Dict[str, CatalystScore]:
        """전체 종목 스코어 (카탈리스트 있는 종목만)"""
        symbols = self.symbols_with_catalysts()
        return {s: self.score(s) for s in symbols}

    # ── MCP 자동 스캔 ────────────────────────────────────────

    async def scan_dart(
        self,
        symbol: str,
        mcp: MCPDataProvider,
        days_back: int = 30,
    ) -> List[Catalyst]:
        """DART 공시에서 카탈리스트 자동 탐지

        MCP dart_disclosure_search 도구로 최근 공시를 검색하고,
        카탈리스트 후보를 자동 등록한다.
        """
        new_catalysts: List[Catalyst] = []
        try:
            disclosures = await mcp._call_vps_tool(
                "dart_disclosure_search",
                {"corp_code": symbol, "days": days_back},
            )

            items = disclosures.get("result", [])
            if isinstance(items, list):
                for item in items[:10]:  # 최근 10건만
                    catalyst = self._parse_dart_disclosure(symbol, item)
                    if catalyst:
                        new_catalysts.append(catalyst)

        except Exception as e:
            logger.warning("DART 스캔 실패 (%s): %s", symbol, e)

        return new_catalysts

    async def scan_news(
        self,
        symbol: str,
        mcp: MCPDataProvider,
        keyword: Optional[str] = None,
    ) -> List[Catalyst]:
        """뉴스에서 카탈리스트 자동 탐지

        MCP news_search 도구로 종목 관련 뉴스를 검색.
        """
        new_catalysts: List[Catalyst] = []
        try:
            query = keyword or symbol
            news = await mcp._call_vps_tool(
                "news_search",
                {"query": query, "limit": 10},
            )

            items = news.get("result", [])
            if isinstance(items, list):
                for item in items[:5]:
                    catalyst = self._parse_news_item(symbol, item)
                    if catalyst:
                        new_catalysts.append(catalyst)

        except Exception as e:
            logger.warning("뉴스 스캔 실패 (%s): %s", symbol, e)

        return new_catalysts

    def _parse_dart_disclosure(
        self, symbol: str, item: Dict[str, Any]
    ) -> Optional[Catalyst]:
        """DART 공시 → 카탈리스트 변환 (주요 공시만)"""
        title = item.get("report_nm", "")
        report_date = item.get("rcept_dt", "")

        # 주요 카탈리스트 키워드 필터
        catalyst_keywords = {
            "합병": (CatalystType.MA, 8),
            "분할": (CatalystType.MA, 7),
            "유상증자": (CatalystType.MA, -5),
            "무상증자": (CatalystType.DIVIDEND, 3),
            "자기주식": (CatalystType.DIVIDEND, 4),
            "배당": (CatalystType.DIVIDEND, 3),
            "대표이사": (CatalystType.MANAGEMENT, 5),
            "임원": (CatalystType.MANAGEMENT, 3),
            "영업실적": (CatalystType.EARNINGS, 5),
            "실적": (CatalystType.EARNINGS, 5),
            "투자": (CatalystType.INDUSTRY, 4),
            "특허": (CatalystType.INDUSTRY, 3),
        }

        for keyword, (cat_type, impact) in catalyst_keywords.items():
            if keyword in title:
                # 중복 체크
                existing = [c for c in self._catalysts if c.symbol == symbol and c.name == title]
                if existing:
                    return None

                return self.add(
                    symbol=symbol,
                    name=title[:80],
                    catalyst_type=cat_type,
                    expected_date=_normalize_date(report_date),
                    probability=0.9,  # 공시 = 거의 확정
                    impact=impact,
                    description=f"DART 공시: {title}",
                    source="DART",
                )

        return None

    def _parse_news_item(
        self, symbol: str, item: Dict[str, Any]
    ) -> Optional[Catalyst]:
        """뉴스 아이템 → 카탈리스트 (주요 키워드만)"""
        title = item.get("title", "")
        pub_date = item.get("date", item.get("pub_date", ""))

        news_keywords = {
            "인수": (CatalystType.MA, 7, 0.5),
            "합병": (CatalystType.MA, 8, 0.5),
            "실적": (CatalystType.EARNINGS, 5, 0.6),
            "흑자전환": (CatalystType.EARNINGS, 7, 0.7),
            "적자전환": (CatalystType.EARNINGS, -6, 0.7),
            "금리": (CatalystType.MACRO, 4, 0.6),
            "규제": (CatalystType.REGULATION, 5, 0.4),
            "허가": (CatalystType.REGULATION, 6, 0.5),
            "상장폐지": (CatalystType.MA, -10, 0.3),
        }

        for keyword, (cat_type, impact, prob) in news_keywords.items():
            if keyword in title:
                existing = [c for c in self._catalysts if c.symbol == symbol and c.name == title[:80]]
                if existing:
                    return None

                return self.add(
                    symbol=symbol,
                    name=title[:80],
                    catalyst_type=cat_type,
                    expected_date=_normalize_date(pub_date) if pub_date else datetime.now().strftime("%Y-%m-%d"),
                    probability=prob,
                    impact=impact,
                    description=f"뉴스: {title}",
                    source="NEWS",
                )

        return None

    # ── 영속성 ───────────────────────────────────────────────

    def _save(self) -> None:
        """JSON 파일에 상태 저장"""
        if not self._state_file:
            return
        path = Path(self._state_file)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": 1,
            "updated_at": datetime.now().isoformat(),
            "catalysts": [_catalyst_to_dict(c) for c in self._catalysts],
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self, state_file: str) -> None:
        """JSON 파일에서 상태 로드"""
        path = Path(state_file)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data.get("catalysts", []):
                self._catalysts.append(_dict_to_catalyst(item))
            logger.info("카탈리스트 %d개 로드 (%s)", len(self._catalysts), state_file)
        except Exception as e:
            logger.warning("카탈리스트 로드 실패: %s", e)


# ── 유틸 함수 ────────────────────────────────────────────────

def _normalize_date(raw: str) -> str:
    """다양한 날짜 형식 → YYYY-MM-DD 정규화"""
    raw = raw.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def _catalyst_to_dict(c: Catalyst) -> Dict[str, Any]:
    """Catalyst → JSON-safe dict"""
    d = asdict(c)
    d["catalyst_type"] = c.catalyst_type.value
    return d


def _dict_to_catalyst(d: Dict[str, Any]) -> Catalyst:
    """dict → Catalyst"""
    d["catalyst_type"] = CatalystType(d["catalyst_type"])
    return Catalyst(**d)
