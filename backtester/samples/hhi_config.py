"""
HD현대중공업(329180) CUFA v16 샘플 config.

로컬 LLM으로 테스트 빌드용 최소 템플릿.
실전 빌드 시 MCP/DART/KRX에서 실제 수치로 덮어쓸 것.

사용:
    python -m kis_backtest.luxon.intelligence cufa \\
        --config=./samples/hhi_config.py \\
        --out=./output/hhi.html
"""
from __future__ import annotations

# ── 1. 메타 ──────────────────────────────────────────────────────

META = {
    "ticker": "329180",
    "company_name": "HD현대중공업",
    "industry": "조선",
    "report_date": "2026-04-13",
    "analyst": "Luxon AI (로컬 LLM)",
}

# ── 2. 주가/시총 ─────────────────────────────────────────────────

PRICE = {
    "current": 475000,
    "52w_high": 520000,
    "52w_low": 280000,
    "market_cap_krw": 4_200_000_000_000,  # 4.2조
}

# ── 3. 목표주가 ──────────────────────────────────────────────────

TARGET_PRICE = {
    "weighted": 800000,
    "upside_pct": 68.4,
    "risk_reward": 3.5,
    "horizon_months": 12,
    "basis": "Bear/Base/Bull 확률 가중",
}

# ── 4. 투자 논지 3축 ──────────────────────────────────────────────

THESIS = [
    {
        "title": "LNG 운반선 슈퍼사이클 진입",
        "evidence": "2025-2028 글로벌 LNG 운반선 발주 250척+, 카타르 2차 발주 임박",
        "falsify": "2026 상반기 신규 수주 50척 미만 시 무효",
        "summary": "슈퍼사이클 확정, 수주잔고 4년치 확보",
    },
    {
        "title": "건조단가 상승 + 후판가 안정",
        "evidence": "CGT당 단가 2020 대비 +45%, 후판가 피크아웃",
        "falsify": "후판가 30% 상승 + 전가율 50% 미만",
        "summary": "단가 상방 + 원가 하방 → 마진 확장",
    },
    {
        "title": "해양플랜트 흑자 전환",
        "evidence": "2024 해양 마진 +3.2% 전환, 2025 추가 수주 품질 개선",
        "falsify": "2025 해양 영업손실 재발",
        "summary": "클레임 해소, 리스크 디스카운트 축소",
    },
]

# ── 5. 사업 구조 ─────────────────────────────────────────────────

BUSINESS = {
    "segments": [
        {"name": "조선", "revenue_pct": 78, "op_margin_pct": 6.5},
        {"name": "해양플랜트", "revenue_pct": 15, "op_margin_pct": 3.2},
        {"name": "엔진기계", "revenue_pct": 7, "op_margin_pct": 9.0},
    ],
    "key_metrics": {
        "수주잔고": "$49B",
        "Slot 가용": "2028까지 100%",
        "LNG 선종 비중": "42%",
    },
    "moat_keys": [
        "세계 1위 조선소 규모",
        "LNG 운반선 건조 기술력",
        "현대중공업 그룹 수직계열화",
    ],
}

# ── 6. 재무 (CFS 기준) ────────────────────────────────────────────

IS_CFS = {
    "2023": {"revenue": 21_293_000_000_000, "op_income": 234_000_000_000, "net_income": 89_000_000_000},
    "2024": {"revenue": 24_700_000_000_000, "op_income": 1_060_000_000_000, "net_income": 620_000_000_000},
    "2025E": {"revenue": 28_500_000_000_000, "op_income": 1_700_000_000_000, "net_income": 1_100_000_000_000},
    "2026E": {"revenue": 32_000_000_000_000, "op_income": 2_350_000_000_000, "net_income": 1_650_000_000_000},
}

# ── 7. Peer/Multiple ─────────────────────────────────────────────

PEERS = {
    "summary": "삼성중공업/대우조선해양 대비 PBR 프리미엄 20%. 수주잔고 1위 반영.",
    "table": [
        {"name": "HD현대중공업", "per_12mf": 15.2, "pbr": 1.9, "ev_ebitda": 8.5},
        {"name": "삼성중공업", "per_12mf": 13.8, "pbr": 1.6, "ev_ebitda": 7.8},
        {"name": "한화오션", "per_12mf": 18.5, "pbr": 2.1, "ev_ebitda": 9.2},
    ],
}

WACC = 9.5

# ── 8. Valuation 시나리오 ────────────────────────────────────────

VALUATION_SCENARIOS = {
    "bear": {
        "price": 350000,
        "prob_pct": 25,
        "condition": "수주 둔화 + 후판가 급등 + 해양 손실 재발",
    },
    "base": {
        "price": 750000,
        "prob_pct": 50,
        "condition": "2026E 가이던스 부합, 마진 6~7%",
    },
    "bull": {
        "price": 1_000_000,
        "prob_pct": 25,
        "condition": "LNG 발주 +20% 상향 + 마진 8%+",
    },
}

VALUATION = {
    "methods": ["DCF (WACC 9.5%)", "EV/EBITDA 8배", "P/B 2.0배"],
    "target_method": "Football Field 가중 평균",
}

# ── 9. 리스크 ────────────────────────────────────────────────────

RISK_MATRIX = {
    "factors": [
        "후판가 민감도 ±10% → 마진 ±2%p",
        "환율 USD/KRW 변동 (수출 비중 90%)",
        "해양플랜트 잔존 클레임",
        "중국 조선사 저가 수주 경쟁",
        "IMO 탈탄소 규제 가속",
    ],
}

EPS_SENSITIVITY = {
    "후판 +10%": -18.2,
    "환율 +5%": 7.1,
    "인건비 +5%": -4.5,
}

KILL_CONDITIONS = (
    "2026 상반기 신규 수주 50척 미만 → 논리 무효화",
    "후판가 30%↑ 동반 전가율 50% 미만 → 논리 무효화",
    "해양플랜트 영업손실 재발 → 논리 무효화",
)

# ── 10. Catalyst Timeline ────────────────────────────────────────

CATALYST_TIMELINE = [
    {"date": "Q2 2026", "event": "1분기 실적 발표", "upside_delta_pct": 5},
    {"date": "Q3 2026", "event": "카타르 LNG 2차 발주", "upside_delta_pct": 12},
    {"date": "Q4 2026", "event": "연간 가이던스 상향", "upside_delta_pct": 8},
    {"date": "H1 2027", "event": "IMO 탈탄소 기준 확정", "upside_delta_pct": 6},
]

# ── 11. Trade Ticket ─────────────────────────────────────────────

trade_ticket = {
    "opinion": "BUY",
    "entry_price": 475000,
    "stop_loss": 420000,
    "target_price": 800000,
    "horizon_months": 12,
    "position_size_pct": 7.0,
    "risk_reward": 3.5,
    "backtest_engine": "open-trading-api/QuantPipeline",
}

# ── 12. 데이터 출처 ───────────────────────────────────────────────

DATA_SOURCES = [
    "DART (연결 재무제표)",
    "KRX (주가/시총)",
    "Nexus MCP (398도구, ECOS 매크로)",
    "Clarksons (조선 수주잔고)",
    "FnGuide (컨센서스)",
]
