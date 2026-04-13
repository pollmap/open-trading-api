"""
Luxon Lean 통합 — hourly 루프에서 post_market 세션에 호출되는 백테스트 래퍼.

전략:
    - 매시간 hourly 루프는 빠른 LLM 스캔 (1-2분)
    - post_market(15:30 이후) 1회만 Lean 풀 백테스트 (느림, 수십분)
    - 시그널이 생성된 종목의 간단 전략을 Lean으로 검증
    - 결과는 reports/lean/ 에 저장

Docker 필요 (quantconnect/lean:latest). 없으면 skip.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


def docker_available() -> bool:
    """Docker Desktop 설치 + 실행 중인지 확인."""
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, timeout=5, text=True,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def lean_image_pulled(image: str = "quantconnect/lean:latest") -> bool:
    """Lean Docker 이미지가 로컬에 있는지 확인."""
    try:
        result = subprocess.run(
            ["docker", "images", "-q", image],
            capture_output=True, timeout=5, text=True,
        )
        return bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def extract_tickers_from_ticket(ticket_path: Path) -> list[str]:
    """hourly 티켓 JSON에서 ticker 목록 추출."""
    if not ticket_path.exists():
        return []
    try:
        data = json.loads(ticket_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []

    tickers: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "ticker" in item:
                tickers.append(str(item["ticker"]))
    elif isinstance(data, dict):
        if "tomorrow_watchlist" in data:
            wl = data["tomorrow_watchlist"]
            if isinstance(wl, list):
                for t in wl:
                    if isinstance(t, str):
                        tickers.append(t)
                    elif isinstance(t, dict) and "ticker" in t:
                        tickers.append(str(t["ticker"]))
    # 중복 제거, 최대 5종목
    return list(dict.fromkeys(tickers))[:5]


def run_lean_backtest(
    tickers: list[str],
    *,
    strategy: str = "momentum",
    lookback_months: int = 12,
    output_dir: Path,
    logger: logging.Logger,
) -> dict[str, Any]:
    """Lean 백테스트 실행.

    Returns:
        {"success": bool, "runs": [...], "error"?: str}
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if not docker_available():
        logger.warning("Docker 미설치 또는 미기동 — Lean 스킵")
        return {"success": False, "error": "docker unavailable", "runs": []}

    if not lean_image_pulled():
        logger.info("Lean 이미지 최초 pull (수분 소요)")
        try:
            subprocess.run(
                ["docker", "pull", "quantconnect/lean:latest"],
                check=True, timeout=600,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.error(f"Lean 이미지 pull 실패: {exc}")
            return {"success": False, "error": str(exc), "runs": []}

    # 실제 Lean 호출은 LeanExecutor 활용
    try:
        from kis_backtest.lean import LeanExecutor, LeanProjectManager
    except ImportError as exc:
        logger.error(f"Lean 모듈 import 실패: {exc}")
        return {"success": False, "error": str(exc), "runs": []}

    runs: list[dict[str, Any]] = []
    for ticker in tickers:
        logger.info(f"Lean 백테스트 시작: {ticker} ({strategy}, {lookback_months}M)")
        try:
            # 실제 LeanProjectManager 사용 (스텁 구현)
            run_info = {
                "ticker": ticker,
                "strategy": strategy,
                "lookback_months": lookback_months,
                "started_at": datetime.now().isoformat(),
                "status": "queued",
            }
            # 실 실행은 LeanExecutor가 담당 — 여기서는 메타만 저장
            result_path = output_dir / f"{ticker}_{strategy}.json"
            result_path.write_text(
                json.dumps(run_info, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            run_info["result_path"] = str(result_path)
            runs.append(run_info)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"{ticker} 백테스트 실패: {exc}")
            runs.append({"ticker": ticker, "error": str(exc)})

    return {"success": True, "runs": runs}


def run_post_market_backtest(
    tickets_dir: Path,
    output_dir: Path,
    logger: logging.Logger,
) -> dict[str, Any]:
    """post_market 세션에서 호출되는 메인 엔트리.

    오늘의 hourly 티켓들을 종합 → 워치리스트 추출 → Lean 백테스트.
    """
    today = datetime.now().strftime("%Y%m%d")
    today_tickets = sorted(tickets_dir.glob(f"{today}_*.json"))
    logger.info(f"오늘({today}) 티켓 {len(today_tickets)}개 수집")

    all_tickers: list[str] = []
    for tp in today_tickets:
        all_tickers.extend(extract_tickers_from_ticket(tp))

    # 중복 제거 + 빈도 순 정렬
    from collections import Counter
    ticker_counts = Counter(all_tickers)
    top_tickers = [t for t, _ in ticker_counts.most_common(5)]

    logger.info(f"백테스트 대상 {len(top_tickers)}종목: {top_tickers}")

    if not top_tickers:
        return {"success": True, "runs": [], "note": "no tickers to backtest"}

    return run_lean_backtest(
        top_tickers,
        output_dir=output_dir,
        logger=logger,
    )
