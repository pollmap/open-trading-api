"""
Luxon Quant Hourly Loop — 매시간 자동 실행되는 퀀트 스캔 + 백테스트 트리거.

Task Scheduler에서 매시간 호출되며 다음을 수행:
    1. 로컬 LLM 스택 헬스체크
    2. 장중 시간대(09:00-15:30 KST)면 agentic 스캔 실행
    3. 시그널 생성 → Simons 평가 → Trade Ticket 저장
    4. 결과를 logs/, reports/, tickets/ 로 분산 저장
    5. 실패 시 재시도 (exponential backoff 2회)

명령 예시:
    python scripts/luxon_quant_hourly.py
    python scripts/luxon_quant_hourly.py --dry-run
    python scripts/luxon_quant_hourly.py --force   # 장외 시간도 실행
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback
from datetime import datetime, time as dtime, timezone, timedelta
from pathlib import Path

# 경로 주입
BACKTESTER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKTESTER))

from kis_backtest.luxon.intelligence import (  # noqa: E402
    AgenticLoopExhausted,
    Tier,
    agentic_run,
    health_check_all,
)


# ── 상수 ──────────────────────────────────────────────────────────

KST = timezone(timedelta(hours=9))
MARKET_OPEN = dtime(9, 0)     # 09:00
MARKET_CLOSE = dtime(15, 30)  # 15:30

LOG_DIR = BACKTESTER / "logs" / "hourly"
REPORT_DIR = BACKTESTER / "reports" / "hourly"
TICKET_DIR = BACKTESTER / "tickets" / "hourly"

# Tier 선택 정책 (바벨 전략)
TIER_PRE_MARKET = Tier.DEFAULT     # 08:00-09:00: 빠르게 준비
TIER_INTRADAY = Tier.DEFAULT       # 09:00-15:30: 빠른 반응
TIER_POST_MARKET = Tier.HEAVY      # 15:30-18:00: 정밀 복기


# ── 설정 ──────────────────────────────────────────────────────────


def setup_logging(run_id: str) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    TICKET_DIR.mkdir(parents=True, exist_ok=True)

    log_file = LOG_DIR / f"{run_id}.log"
    logger = logging.getLogger(f"luxon.hourly.{run_id}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(ch)
    return logger


# ── 세션 판정 ─────────────────────────────────────────────────────


def now_kst() -> datetime:
    return datetime.now(KST)


def session_phase(t: datetime) -> str:
    """현재 시각 → 세션 구분."""
    if t.weekday() >= 5:
        return "weekend"
    t_time = t.time()
    if t_time < dtime(8, 0):
        return "pre_open"
    if t_time < MARKET_OPEN:
        return "pre_market"      # 08:00-09:00
    if t_time <= MARKET_CLOSE:
        return "intraday"        # 09:00-15:30
    if t_time < dtime(18, 0):
        return "post_market"     # 15:30-18:00
    return "after_hours"


def select_tier(phase: str) -> Tier:
    return {
        "pre_market": TIER_PRE_MARKET,
        "intraday": TIER_INTRADAY,
        "post_market": TIER_POST_MARKET,
    }.get(phase, Tier.DEFAULT)


def should_run(phase: str, *, force: bool) -> bool:
    if force:
        return True
    return phase in ("pre_market", "intraday", "post_market")


# ── 프롬프트 생성 ─────────────────────────────────────────────────


PROMPTS_BY_PHASE = {
    "pre_market": """장 시작 전 스캔 (매시간).
이전 일봉 데이터 기준:
- KOSPI200 중 모멘텀 상위 5종목 (가격 변동 + 거래량)
- 주요 매크로 이벤트 (환율/금리/원자재) 변화
- 각 종목별 진입 후보 여부 판단
JSON 배열로 반환: [{"ticker": "...", "rationale": "...", "action": "BUY|WATCH|AVOID"}]""",

    "intraday": """장중 실시간 스캔 (매시간).
현재 시점 기준:
- 관심 종목(KOSPI200 상위 10) 가격/거래량 확인
- 급등락 (±3% 이상) 발생 종목 체크
- Kill Condition 임박 종목 (손절가 -5% 이내) 알림
- 매크로 뉴스 급변 사항
JSON: [{"ticker": "...", "event": "SPIKE|DROP|KILL_NEAR|NEWS", "detail": "..."}]""",

    "post_market": """장 마감 후 복기 (Simons 5번 원칙: 실패 = 데이터).
오늘 데이터 요약:
- 시가총액 상위 20 종목 일봉 요약 (시/고/저/종/거래량)
- 어떤 시그널이 맞았나 / 틀렸나
- 내일 관찰 대상 3종목 + 근거
JSON: {"market_summary": "...", "hits": [...], "misses": [...], "tomorrow_watchlist": [...]}""",
}


def build_prompt(phase: str) -> str:
    return PROMPTS_BY_PHASE.get(phase, PROMPTS_BY_PHASE["intraday"])


# ── 실행 ──────────────────────────────────────────────────────────


def execute_with_retry(
    prompt: str,
    tier: Tier,
    *,
    mcp_servers: list[str],
    max_steps: int,
    logger: logging.Logger,
    max_retries: int = 2,
):
    for attempt in range(max_retries + 1):
        try:
            logger.info(f"agentic_run tier={tier.value.name} attempt={attempt + 1}")
            return agentic_run(
                prompt,
                tier=tier,
                mcp_servers=mcp_servers,
                max_steps=max_steps,
                temperature=0.2,
            )
        except AgenticLoopExhausted as exc:
            logger.warning(f"max_steps 초과: {exc}")
            return None  # 재시도 의미 없음
        except Exception as exc:  # noqa: BLE001
            logger.error(f"실패 attempt={attempt + 1}: {type(exc).__name__}: {exc}")
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.info(f"재시도 대기 {wait}s")
                time.sleep(wait)
            else:
                logger.error(f"최종 실패: {traceback.format_exc()}")
                return None
    return None


def save_artifacts(run_id: str, phase: str, result, logger: logging.Logger) -> None:
    if result is None:
        return
    # 마크다운 리포트
    report_path = REPORT_DIR / f"{run_id}.md"
    report_path.write_text(
        f"# Luxon Hourly Run — {run_id}\n\n"
        f"- Phase: `{phase}`\n"
        f"- Steps: {len(result.steps)}\n"
        f"- Tool calls: {result.total_tool_calls}\n\n"
        f"## Final Content\n\n{result.final_content}\n",
        encoding="utf-8",
    )
    logger.info(f"리포트 저장: {report_path}")

    # Trade Ticket JSON (LLM 응답에서 추출 시도)
    try:
        content = result.final_content.strip()
        # JSON 코드블록 추출
        import re as _re
        m = _re.search(r"```(?:json)?\s*([\{\[].*?[\}\]])\s*```", content, _re.DOTALL)
        json_str = m.group(1) if m else content
        # 괄호 기반 파싱
        for start_ch, end_ch in [("[", "]"), ("{", "}")]:
            s = json_str.find(start_ch)
            e = json_str.rfind(end_ch)
            if s != -1 and e != -1 and e > s:
                parsed = json.loads(json_str[s : e + 1])
                ticket_path = TICKET_DIR / f"{run_id}.json"
                ticket_path.write_text(
                    json.dumps(parsed, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info(f"티켓 저장: {ticket_path}")
                return
    except (json.JSONDecodeError, ValueError):
        logger.warning("JSON 추출 실패, 리포트만 저장됨")


# ── 메인 ──────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="실제 호출 없이 판정만")
    parser.add_argument("--force", action="store_true", help="장외 시간도 실행")
    parser.add_argument("--max-steps", type=int, default=5)
    parser.add_argument("--servers", default="kis-backtest,nexus-finance")
    parser.add_argument("--skip-lean", action="store_true", help="post_market Lean 백테스트 스킵")
    args = parser.parse_args()

    t = now_kst()
    run_id = t.strftime("%Y%m%d_%H%M")
    phase = session_phase(t)
    logger = setup_logging(run_id)

    logger.info(f"=== Luxon Hourly Run {run_id} ===")
    logger.info(f"시각: {t.isoformat()} ({phase})")

    if not should_run(phase, force=args.force):
        logger.info(f"장외 시간 ({phase}) — 스킵 (--force로 강제 실행 가능)")
        return 0

    # 헬스체크
    health = health_check_all()
    logger.info(f"헬스: {health}")
    if not health.get("DEFAULT"):
        logger.error("DEFAULT 티어 다운 — 실행 중단")
        return 2

    tier = select_tier(phase)
    prompt = build_prompt(phase)
    servers = [s.strip() for s in args.servers.split(",") if s.strip()]

    if args.dry_run:
        logger.info(f"[DRY RUN] tier={tier.value.name}, servers={servers}")
        logger.info(f"[DRY RUN] prompt={prompt[:200]}...")
        return 0

    result = execute_with_retry(
        prompt, tier,
        mcp_servers=servers,
        max_steps=args.max_steps,
        logger=logger,
    )

    save_artifacts(run_id, phase, result, logger)

    # post_market 세션이면 Lean 백테스트 추가 실행 (느림, 선택)
    if phase == "post_market" and not args.skip_lean:
        try:
            from luxon_lean_integration import run_post_market_backtest
            lean_dir = BACKTESTER / "reports" / "lean"
            logger.info("post_market Lean 백테스트 시작 (최대 30분)")
            lean_result = run_post_market_backtest(
                tickets_dir=TICKET_DIR,
                output_dir=lean_dir,
                logger=logger,
            )
            logger.info(f"Lean 결과: success={lean_result.get('success')}, "
                        f"runs={len(lean_result.get('runs', []))}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Lean 백테스트 건너뜀: {exc}")

    if result is None:
        logger.error("실행 실패")
        return 1
    logger.info(f"완료: steps={len(result.steps)}, tool_calls={result.total_tool_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
