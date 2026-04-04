"""MCP 결과 정규화 + VPS 연결 유틸리티

Claude Code에서 MCP 도구를 호출한 결과(dict)를 MCPBridge가 소화할 수 있는
표준 형태로 변환한다.

VPS health check와 도구 목록 확인도 포함.

Usage (Claude Code 스킬 내에서):
    # MCP 호출 결과를 정규화
    from kis_backtest.portfolio.mcp_connector import normalize_factor_scores, normalize_bl_weights

    # factor_score MCP 결과
    raw = mcp_call("factor_score", {...})
    factor_scores = normalize_factor_scores(raw)

    # BL 결과
    raw_bl = mcp_call("portadv_black_litterman", {...})
    weights = normalize_bl_weights(raw_bl)

    # pipeline에 전달
    from kis_backtest.core.pipeline import QuantPipeline
    pipeline = QuantPipeline()
    result = pipeline.run(factor_scores=factor_scores, optimal_weights=weights)
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple


# VPS 정보 (환경변수 우선, 미설정 시 기본값)
MCP_VPS_HOST = os.environ.get("MCP_VPS_HOST", "62.171.141.206")
MCP_HEALTH_URL = f"http://{MCP_VPS_HOST}/health"
MCP_ENDPOINT = f"http://{MCP_VPS_HOST}/mcp"


def check_health(timeout: int = 10) -> Dict[str, Any]:
    """VPS MCP 서버 health check

    Returns:
        {"status": "ok", "version": "8.0.0-phase12", "loaded_servers": 64, "tool_count": 364}
    """
    try:
        result = subprocess.run(
            ["curl", "-s", "--connect-timeout", str(timeout), MCP_HEALTH_URL],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass

    return {"status": "error", "message": "MCP 서버 연결 실패"}


def normalize_factor_scores(
    raw_result: Dict[str, Any],
    default_market: str = "KOSPI",
) -> Dict[str, Dict]:
    """MCP factor_score 결과를 MCPBridge 입력 형태로 변환

    MCP 결과 예시:
    {
        "success": True,
        "data": {
            "scores": [
                {"ticker": "005930", "name": "삼성전자", "score": 0.82, "sector": "IT"},
                ...
            ]
        }
    }

    출력:
    {
        "005930": {"name": "삼성전자", "score": 0.82, "sector": "IT", "market": "KOSPI"},
        ...
    }
    """
    if not raw_result or not raw_result.get("success"):
        return {}

    data = raw_result.get("data", {})
    scores = data.get("scores", [])

    if isinstance(scores, list):
        return {
            item["ticker"]: {
                "name": item.get("name", item["ticker"]),
                "score": item.get("score", 0.0),
                "sector": item.get("sector", ""),
                "market": item.get("market", default_market),
            }
            for item in scores
            if "ticker" in item
        }

    # dict 형태인 경우
    if isinstance(scores, dict):
        return {
            ticker: {
                "name": info.get("name", ticker),
                "score": info.get("score", 0.0),
                "sector": info.get("sector", ""),
                "market": info.get("market", default_market),
            }
            for ticker, info in scores.items()
        }

    return {}


def normalize_bl_weights(
    raw_result: Dict[str, Any],
) -> Dict[str, float]:
    """MCP portadv_black_litterman 결과를 MCPBridge 입력 형태로 변환

    MCP 결과 예시:
    {
        "success": True,
        "data": {
            "optimal_weights": {"005930": 0.15, "000660": 0.12, ...},
            "posterior_returns": {...}
        }
    }

    출력:
    {"005930": 0.15, "000660": 0.12, ...}
    """
    if not raw_result or not raw_result.get("success"):
        return {}

    data = raw_result.get("data", {})
    weights = data.get("optimal_weights", data.get("weights", {}))

    if isinstance(weights, dict):
        return {k: float(v) for k, v in weights.items()}

    # list of dicts
    if isinstance(weights, list):
        return {
            item.get("ticker", item.get("name", "")): float(item.get("weight", 0))
            for item in weights
            if item.get("ticker") or item.get("name")
        }

    return {}


def normalize_returns(
    raw_result: Dict[str, Any],
) -> Dict[str, List[float]]:
    """MCP stocks_history 결과를 일간 수익률 dict로 변환

    MCP 결과 예시:
    {
        "success": True,
        "data": [
            {"date": "2026-01-02", "close": 55000},
            {"date": "2026-01-03", "close": 55500},
            ...
        ]
    }

    출력: [0.009, -0.003, ...]  (일간 수익률)
    """
    if not raw_result or not raw_result.get("success"):
        return {}

    data = raw_result.get("data", [])

    if isinstance(data, list) and data:
        prices = [item.get("close", item.get("price", 0)) for item in data if item.get("close") or item.get("price")]
        if len(prices) >= 2:
            returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices)) if prices[i-1] > 0]
            return returns

    return []
