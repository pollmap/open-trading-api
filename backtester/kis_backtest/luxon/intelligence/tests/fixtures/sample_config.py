"""CUFA 테스트용 샘플 config dict (최소 필드)."""
from __future__ import annotations


def build_sample_config() -> dict:
    return {
        "META": {
            "ticker": "329180",
            "company_name": "HD현대중공업",
            "industry": "조선",
        },
        "PRICE": {"current": 475000},
        "TARGET_PRICE": {"weighted": 800000, "upside_pct": 68.4, "risk_reward": 3.5},
        "THESIS": [
            {
                "title": "LNG 운반선 슈퍼사이클",
                "evidence": "2025-2028 글로벌 LNG 운반선 발주 250척+",
                "falsify": "2026 상반기 신규 수주 50척 미만",
                "summary": "슈퍼사이클 진입 확정, 수주잔고 4년치 확보",
            },
            {
                "title": "건조단가 상승",
                "evidence": "CGT당 단가 2020 대비 +45%",
                "falsify": "후판가 30% 상승 + 전가율 50% 미만",
                "summary": "단가 ↑ + 후판 하락 → 마진 확장",
            },
            {
                "title": "해양플랜트 흑자 전환",
                "evidence": "2024 해양 마진 +3.2% 전환",
                "falsify": "2025 해양 영업손실 재발",
                "summary": "클레임 해소, 신규 수주 품질 개선",
            },
        ],
        "BUSINESS": {
            "segments": [
                {"name": "조선", "revenue_pct": 78, "op_margin_pct": 6.5},
                {"name": "해양플랜트", "revenue_pct": 15, "op_margin_pct": 3.2},
                {"name": "엔진기계", "revenue_pct": 7, "op_margin_pct": 9.0},
            ],
            "key_metrics": {"수주잔고": "$49B", "Slot가용": "2028까지 100%"},
            "moat_keys": ["세계 1위 조선소", "LNG 기술력", "규모의 경제"],
        },
        "PEERS": {"summary": "삼성중공업/대우조선 대비 PBR 프리미엄 20%"},
        "WACC": 9.5,
        "VALUATION_SCENARIOS": {
            "bear": {"price": 350000, "prob_pct": 25, "condition": "수주 둔화 + 후판가 급등"},
            "base": {"price": 750000, "prob_pct": 50, "condition": "가이던스 부합"},
            "bull": {"price": 1000000, "prob_pct": 25, "condition": "LNG 발주 +20% 상향"},
        },
        "RISK_MATRIX": {
            "factors": [
                "후판가 민감도 ±10%",
                "환율 USD/KRW 변동",
                "해양플랜트 잔존 리스크",
                "중국 조선사 경쟁 심화",
            ],
        },
        "EPS_SENSITIVITY": {"후판 +10%": -18.2, "환율 +5%": +7.1},
        "CATALYST_TIMELINE": [
            {"date": "2026-05-15", "event": "1분기 실적 발표", "upside_delta_pct": 5},
            {"date": "2026-Q3", "event": "카타르 LNG 2차 발주", "upside_delta_pct": 12},
            {"date": "2026-11-30", "event": "연간 가이던스 상향", "upside_delta_pct": 8},
        ],
        "trade_ticket": {
            "opinion": "BUY",
            "entry_price": 475000,
            "stop_loss": 420000,
            "position_size_pct": 7.0,
            "horizon_months": 12,
        },
        "DATA_SOURCES": ["DART", "KRX", "Nexus MCP", "Clarksons"],
        "VALUATION": {"methods": ["DCF", "EV/EBITDA", "P/B"]},
        "KILL_CONDITIONS": [
            "2026 상반기 신규 수주 50척 미만 → 논리 무효화",
            "후판가 30%↑ 동반 전가율 50% 미만 → 논리 무효화",
            "해양플랜트 영업손실 재발 → 논리 무효화",
        ],
    }
