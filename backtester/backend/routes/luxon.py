"""Luxon Terminal 웹 API 라우터.

POST /api/luxon/analyze — 종목 분석 + 의사결정 리포트
POST /api/luxon/execute — 주문 실행 (dry_run 기본)
GET  /api/luxon/graph   — GothamGraph HTML 반환
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(tags=["luxon"])


# ── 요청/응답 스키마 ───────────────────────────────────────


class AnalyzeRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1, examples=[["005930", "000660"]])
    convictions: dict[str, float] = Field(
        default_factory=dict, description="종목별 확신도 1-10 (없으면 5.0)",
    )
    total_capital: float = Field(default=100_000_000.0, description="총 자본 KRW")


class DecisionItem(BaseModel):
    symbol: str
    action: str
    weight: float
    catalyst_score: float
    conviction: float


class PositionItem(BaseModel):
    symbol: str
    weight: float
    amount: float


class AnalyzeResponse(BaseModel):
    regime: str
    regime_confidence: float
    decisions: list[DecisionItem]
    position_sizes: list[PositionItem]
    cross_references: dict[str, list[str]]
    summary_markdown: str


# ── 엔드포인트 ────────────────────────────────────────────


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    """Luxon 종목 분석. LuxonOrchestrator.run_workflow 호출."""
    try:
        from kis_backtest.luxon.orchestrator import LuxonOrchestrator

        orch = LuxonOrchestrator(total_capital=req.total_capital)

        convictions = req.convictions or {s: 5.0 for s in req.symbols}
        for s in req.symbols:
            convictions.setdefault(s, 5.0)

        report = orch.run_workflow(req.symbols, base_convictions=convictions)

        return AnalyzeResponse(
            regime=report.regime,
            regime_confidence=report.regime_confidence,
            decisions=[
                DecisionItem(
                    symbol=d.symbol,
                    action=d.action,
                    weight=d.final_weight,
                    catalyst_score=d.catalyst_score,
                    conviction=d.conviction,
                )
                for d in report.portfolio.decisions
            ],
            position_sizes=[
                PositionItem(
                    symbol=ps.symbol, weight=ps.weight, amount=ps.amount,
                )
                for ps in report.position_sizes
            ],
            cross_references=report.cross_references,
            summary_markdown=report.summary(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Luxon analyze failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/graph")
async def get_graph() -> dict:
    """최근 생성된 GothamGraph HTML 경로 반환."""
    from pathlib import Path
    graph_path = Path(__file__).resolve().parent.parent.parent / "out" / "luxon_watchlist.html"
    if graph_path.exists():
        return {"path": str(graph_path), "exists": True}
    return {"path": str(graph_path), "exists": False}
