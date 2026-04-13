"""
Luxon Terminal — CUFA digest → conviction 자동 계산 브릿지 (STEP 3 / v0.7).

CUFA 기업분석보고서가 생성하는 digest(dict/JSON)에서:
    - investment_points (IP) 수
    - kill_conditions 트리거 수
를 기반으로 conviction(1-10)을 산출하여 FeedbackAdapter.save_convictions()에 주입.

설계 원칙:
    - 신규 계산 로직 최소 — 기존 `CUFABridge.parse_kill_conditions`를 재사용.
    - HTML 파싱 새로 만들지 않음 — CUFA가 뱉는 dict/JSON digest에서 바로 계산.
    - 모든 실패는 스킵 + 로깅 (raise 금지). 선순환 루프가 CUFA 없이도 동작해야 함.

Conviction 공식 (단순, 감사 가능):
    base = 5.0
    bonus = min(ip_count, 4) × 1.0            # IP 4개까지 +4 가산 (max +9)
    penalty = triggered_kills × 2.0            # 트리거 당 -2 감점
    conviction = clamp(base + bonus - penalty, 1.0, 10.0)

선순환 흐름:
    CUFA digest.json (파일)
        → load_cufa_digests_from_dir()
        → compute_conviction_from_digest(digest)
        → {symbol: conviction}
        → FeedbackAdapter.save_convictions()
        → LuxonTerminal.cycle() step 4 load_persisted_convictions() 로 재주입
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kis_backtest.portfolio.cufa_bridge import CUFABridge

logger = logging.getLogger(__name__)


# Conviction 튜닝 파라미터 — 공식의 유일 출처
_BASE_CONVICTION: float = 5.0
_IP_BONUS_PER: float = 1.0
_IP_MAX_BONUS: int = 4  # 4개 이상은 diminishing return (중복 회피)
_KILL_PENALTY_PER: float = 2.0
_MIN_CONVICTION: float = 1.0
_MAX_CONVICTION: float = 10.0


@dataclass(frozen=True)
class CufaConviction:
    """CUFA digest → conviction 변환 결과 (감사 가능 스냅샷).

    Attributes:
        symbol: 종목 코드
        conviction: 최종 확신도 (1.0 ~ 10.0)
        ip_count: 추출된 IP 수
        triggered_kill_count: 트리거된 Kill Condition 수
        reasoning: 계산 근거 라인 (로깅/디버깅용)
    """
    symbol: str
    conviction: float
    ip_count: int
    triggered_kill_count: int
    reasoning: list[str] = field(default_factory=list)


def compute_conviction_from_digest(digest: dict[str, Any]) -> CufaConviction | None:
    """CUFA digest dict → CufaConviction 변환.

    Expected digest shape (CUFA skill v14.1 표준):
        {
            "ticker": "005930" | "symbol": "005930",
            "investment_points": [{"id": 1, "type": "growth", ...}, ...],
            "kill_conditions": [{"metric": "opm", "trigger": 0.10,
                                 "current": 0.13, ...}, ...],
        }

    Args:
        digest: CUFA digest dict. ticker 없으면 None 반환.

    Returns:
        CufaConviction 또는 None (digest 파싱 실패 시).
    """
    symbol = str(
        digest.get("ticker")
        or digest.get("symbol")
        or ""
    ).strip()
    if not symbol:
        logger.warning("CUFA digest에 ticker/symbol 없음 — 스킵")
        return None

    ips = digest.get("investment_points", []) or []
    ip_count = sum(1 for ip in ips if isinstance(ip, dict))

    # 트리거된 Kill Condition 수 집계 (CUFABridge 재사용 → current 값으로 자동 판정)
    kcs = CUFABridge.parse_kill_conditions(digest)
    triggered = sum(1 for kc in kcs if kc.is_triggered)

    bonus = min(ip_count, _IP_MAX_BONUS) * _IP_BONUS_PER
    penalty = triggered * _KILL_PENALTY_PER
    raw = _BASE_CONVICTION + bonus - penalty
    conviction = max(_MIN_CONVICTION, min(_MAX_CONVICTION, raw))

    reasoning = [
        f"base={_BASE_CONVICTION:.1f}",
        f"ip_count={ip_count} → bonus={bonus:+.1f}",
        f"triggered_kills={triggered} → penalty={-penalty:+.1f}",
        f"conviction={conviction:.1f} (raw={raw:.1f})",
    ]
    logger.info(
        "CUFA conviction %s: %.1f (ip=%d, kill_triggered=%d)",
        symbol, conviction, ip_count, triggered,
    )
    return CufaConviction(
        symbol=symbol,
        conviction=conviction,
        ip_count=ip_count,
        triggered_kill_count=triggered,
        reasoning=reasoning,
    )


def load_cufa_digests_from_dir(
    digests_dir: str | Path,
) -> list[dict[str, Any]]:
    """디렉토리에서 CUFA digest JSON 파일들 로드.

    규칙:
        - `*.json` 파일만 읽음
        - 각 파일은 단일 digest dict (CUFA v14.1 표준 shape)
        - 파싱 실패한 파일은 로깅 후 스킵 (선순환 유지)

    Args:
        digests_dir: digest JSON 들이 있는 디렉토리.

    Returns:
        성공적으로 로드된 digest dict 리스트. 디렉토리 없거나 비어있으면 빈 리스트.
    """
    path = Path(digests_dir).expanduser()
    if not path.exists() or not path.is_dir():
        logger.debug("CUFA digest 디렉토리 없음: %s", path)
        return []

    digests: list[dict[str, Any]] = []
    for json_path in sorted(path.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                digests.append(data)
            else:
                logger.warning("CUFA digest %s: dict 아님 — 스킵", json_path.name)
        except Exception as exc:
            logger.warning("CUFA digest %s 로드 실패: %s", json_path.name, exc)

    logger.info("CUFA digest 로드 완료: %d개 (%s)", len(digests), path)
    return digests


def build_convictions_from_digests(
    digests: list[dict[str, Any]],
) -> dict[str, float]:
    """digest 리스트 → {symbol: conviction} 맵.

    동일 symbol이 여러 digest에 나오면 **마지막** 값으로 덮어씀 (최신 보고서 우선
    관례: 파일명 sort 순서가 시간순이도록 보관할 것).
    """
    result: dict[str, float] = {}
    for digest in digests:
        computed = compute_conviction_from_digest(digest)
        if computed is not None:
            result[computed.symbol] = computed.conviction
    return result


__all__ = [
    "CufaConviction",
    "compute_conviction_from_digest",
    "load_cufa_digests_from_dir",
    "build_convictions_from_digests",
]
