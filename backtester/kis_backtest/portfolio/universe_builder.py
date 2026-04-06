"""유니버스 빌더 — 섹터 기반 종목 자동 선별

MCP stocks_search + dart_financial_ratios를 사용하여
섹터별 상위 N개 종목을 자동으로 선별한다.

Usage:
    from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
    from kis_backtest.portfolio.universe_builder import UniverseBuilder

    provider = MCPDataProvider()
    builder = UniverseBuilder(provider)
    universe = builder.build_sync()

    # QuantPipeline에 전달할 factor_scores 형태로 변환
    factor_scores = builder.to_factor_scores(universe)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

if TYPE_CHECKING:
    from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider

logger = logging.getLogger(__name__)


@dataclass
class SectorDef:
    """섹터 정의

    candidates: 미리 지정된 후보 종목 {ticker: name}.
                stocks_search가 안 되는 종목을 직접 지정할 때 사용.
                지정하면 MCP 검색 건너뛰고 바로 DART 스크리닝으로.
    """
    name: str
    keywords: List[str]
    top_n: int = 2
    candidates: Optional[Dict[str, str]] = None  # {ticker: name}


@dataclass
class StockInfo:
    """선별된 종목 정보"""
    ticker: str
    name: str
    sector: str
    roe: float = 0.0
    opm: float = 0.0
    dte: float = 0.0
    score: float = 0.0
    market: str = "KOSPI"


@dataclass
class UniverseResult:
    """유니버스 빌드 결과"""
    stocks: Dict[str, Dict[str, Any]]
    etfs: Dict[str, Dict[str, Any]]
    screening_log: List[str] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    @property
    def all_tickers(self) -> List[str]:
        return list(self.stocks.keys()) + list(self.etfs.keys())

    @property
    def stock_count(self) -> int:
        return len(self.stocks)

    @property
    def total_count(self) -> int:
        return len(self.stocks) + len(self.etfs)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stocks": self.stocks,
            "etfs": self.etfs,
            "screening_log": self.screening_log,
            "timestamp": self.timestamp,
            "stock_count": self.stock_count,
            "total_count": self.total_count,
        }


# 기본 섹터 정의 (6섹터)
# candidates: stocks_search 커버리지가 불완전하므로 후보 종목 직접 지정
# 이전 세션에서 MCP stocks_search + dart_financial_ratios로 확정한 종목
DEFAULT_SECTORS = [
    SectorDef("건설", ["건설", "삼성물산"], candidates={
        "028260": "삼성물산", "000720": "현대건설", "000210": "DL",
    }),
    SectorDef("반도체", ["삼성전자", "SK하이닉스"], candidates={
        "000660": "SK하이닉스", "058470": "리노공업", "005930": "삼성전자",
    }),
    SectorDef("우주", ["한화에어로"], candidates={
        "012450": "한화에어로스페이스", "099320": "쎄트렉아이", "047810": "한국항공우주",
    }),
    SectorDef("방산", ["한화에어로", "현대로템"], candidates={
        "064350": "현대로템", "079550": "LIG넥스원", "012450": "한화에어로스페이스",
    }),
    SectorDef("조선", ["HD한국조선", "삼성중공"], candidates={
        "009540": "HD한국조선해양", "010140": "삼성중공업", "329180": "HD현대중공업",
    }),
    SectorDef("로봇", ["로봇"], candidates={
        "090460": "로보스타", "277810": "레인보우로보틱스", "454910": "두산로보틱스",
    }),
]

# 기본 ETF 헤지 자산
DEFAULT_ETFS: Dict[str, Dict[str, str]] = {
    "148070": {"name": "KOSEF 국고채10년", "asset_class": "bond"},
    "132030": {"name": "KODEX 골드선물(H)", "asset_class": "gold"},
    "261220": {"name": "KODEX WTI원유선물(H)", "asset_class": "oil"},
}


def _compute_screening_score(
    roe: float, opm: float, dte: float,
    w_roe: float = 0.4, w_opm: float = 0.3, w_dte: float = 0.3,
) -> float:
    """ROE/OPM/DTE 기반 스크리닝 점수 (0~1)

    ROE, OPM: 높을수록 좋음
    DTE: 낮을수록 좋음 (역수 사용)
    """
    # 정규화: 실제값을 0~1 범위로 매핑 (대략적 한국 시장 기준)
    roe_norm = max(0.0, min(1.0, roe / 30.0))  # ROE 30% = 1.0
    opm_norm = max(0.0, min(1.0, opm / 25.0))  # OPM 25% = 1.0
    dte_norm = max(0.0, min(1.0, dte / 300.0))  # DTE 300% = 1.0 (나쁨)

    return w_roe * roe_norm + w_opm * opm_norm + w_dte * (1.0 - dte_norm)


class UniverseBuilder:
    """섹터 기반 종목 자동 선별기

    Flow:
        1. 섹터별 키워드로 stocks_search MCP 호출
        2. 후보 종목 DART 재무비율 조회
        3. ROE/OPM/DTE 종합 점수 계산
        4. 섹터당 상위 N개 선별
        5. ETF 헤지 자산 추가
    """

    def __init__(
        self,
        mcp: "MCPDataProvider",
        sectors: Optional[List[SectorDef]] = None,
        etfs: Optional[Dict[str, Dict[str, str]]] = None,
        max_candidates_per_keyword: int = 10,
    ):
        self._mcp = mcp
        self._sectors = sectors or DEFAULT_SECTORS
        self._etfs = etfs or DEFAULT_ETFS
        self._max_candidates = max_candidates_per_keyword

    async def build(self) -> UniverseResult:
        """유니버스 빌드 실행"""
        all_stocks: Dict[str, Dict[str, Any]] = {}
        log: List[str] = []
        log.append(f"빌드 시작: {len(self._sectors)}개 섹터")

        for sector in self._sectors:
            try:
                selected = await self._select_sector(sector)
                for stock in selected:
                    all_stocks[stock.ticker] = {
                        "name": stock.name,
                        "sector": sector.name,
                        "roe": stock.roe,
                        "opm": stock.opm,
                        "dte": stock.dte,
                        "score": stock.score,
                        "market": stock.market,
                    }
                names = [s.name for s in selected]
                log.append(f"  {sector.name}: {', '.join(names)} ({len(selected)}종목)")
            except Exception as e:
                log.append(f"  {sector.name}: 실패 — {e}")
                logger.warning("섹터 %s 선별 실패: %s", sector.name, e)

        log.append(f"ETF: {', '.join(v['name'] for v in self._etfs.values())}")
        log.append(f"총 {len(all_stocks)}종목 + {len(self._etfs)}ETF = {len(all_stocks) + len(self._etfs)}")

        return UniverseResult(
            stocks=all_stocks,
            etfs=dict(self._etfs),
            screening_log=log,
        )

    async def _select_sector(self, sector: SectorDef) -> List[StockInfo]:
        """단일 섹터 종목 선별: 후보지정/검색 → 재무 스크리닝 → top N"""
        # 1. 후보 종목: candidates 직접 지정 우선, 없으면 MCP 검색
        candidates: Dict[str, Dict[str, str]] = {}

        if sector.candidates:
            # 직접 지정된 후보 사용 (stocks_search 커버리지 불완전 대응)
            for ticker, name in sector.candidates.items():
                candidates[ticker] = {"ticker": ticker, "name": name, "market": ""}
        else:
            # MCP stocks_search 키워드 검색
            for kw in sector.keywords:
                results = await self._mcp.search_stocks(kw)
                for item in results[:self._max_candidates]:
                    ticker = item.get("ticker", "")
                    if ticker and ticker not in candidates:
                        candidates[ticker] = item

        if not candidates:
            logger.warning("섹터 %s: 후보 종목 없음 (키워드: %s)", sector.name, sector.keywords)
            return []

        # 2. DART 재무비율 조회 + 점수 계산
        scored: List[StockInfo] = []
        for ticker, info in candidates.items():
            try:
                financials = await self._mcp.get_dart_financials(ticker)
                roe = _extract_float(financials, "roe", "return_on_equity")
                opm = _extract_float(financials, "opm", "operating_margin", "operating_profit_margin")
                dte = _extract_float(financials, "dte", "debt_to_equity", "debt_ratio")

                score = _compute_screening_score(roe, opm, dte)
                scored.append(StockInfo(
                    ticker=ticker,
                    name=info.get("name", ticker),
                    sector=sector.name,
                    roe=roe,
                    opm=opm,
                    dte=dte,
                    score=score,
                    market=info.get("market", "KOSPI"),
                ))
            except Exception as e:
                logger.debug("종목 %s DART 조회 실패: %s", ticker, e)

        # 3. 점수 내림차순 정렬 → top N
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:sector.top_n]

    def to_factor_scores(self, result: UniverseResult) -> Dict[str, Dict[str, Any]]:
        """UniverseResult → QuantPipeline.run() factor_scores 형태로 변환

        Returns:
            {ticker: {"name": str, "score": float, "sector": str, "market": str}}
        """
        out: Dict[str, Dict[str, Any]] = {}

        for ticker, info in result.stocks.items():
            out[ticker] = {
                "name": info["name"],
                "score": info.get("score", 0.5),
                "sector": info.get("sector", ""),
                "market": info.get("market", "KOSPI"),
            }

        # ETF는 고정 점수 0.5 (중립)
        for ticker, info in result.etfs.items():
            out[ticker] = {
                "name": info["name"],
                "score": 0.5,
                "sector": info.get("asset_class", "ETF"),
                "market": "KOSPI",
            }

        return out

    def save(self, result: UniverseResult, base_dir: Optional[Path] = None) -> Path:
        """결과를 JSON으로 저장"""
        return self._mcp.save_result(
            result.to_dict(),
            category="universe",
            tag=f"sectors_{result.stock_count}",
        )

    # ── sync 래퍼 ─────────────────────────────────────────────

    def build_sync(self) -> UniverseResult:
        from kis_backtest.portfolio.mcp_data_provider import _run_sync
        return _run_sync(self.build())


def _extract_float(data: Dict[str, Any], *keys: str) -> float:
    """dict에서 여러 키 중 첫 번째 유효한 float 추출"""
    for key in keys:
        val = data.get(key)
        if val is not None:
            try:
                v = float(val)
                # 백분율→비율 변환: 100 이상이면 이미 %단위
                return v if abs(v) < 100 else v
            except (ValueError, TypeError):
                continue
    return 0.0
