"""Luxon Phase 1 E2E 실 MCP 스모크 (Sprint 4 STEP 3 / 4D).

Phase1Pipeline.checkpoint()을 실 Nexus Finance MCP로 한 번 찌르고 Phase 1
재료 준비 완료 조건을 검증한다. 단위 테스트가 커버할 수 없는 실 네트워크
경로(FREDHub → macro_fred, MacroRegime → ecos_*/macro_fred 10지표)의 live
검증 전용. 목업 0, 가짜 성공 찍지 않음 (R11 silent-fail 재발 방지).

Exit code: 0=통과, 1=health 실패, 2=예외, 3=임계값 미달
실행:      ./.venv/Scripts/python.exe scripts/smoke_phase1_e2e.py
임계값:    fred>=8, macro>=9, errors<=1, regime!=None
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

# Windows 콘솔 cp949 → UTF-8 강제. CLAUDE.md 인코딩 규칙 따름.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

# 프로젝트 루트에서 실행하지 않아도 import가 되도록
_BACKTESTER_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKTESTER_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKTESTER_ROOT))

from kis_backtest.luxon.integration.phase1_pipeline import (
    Phase1CheckpointResult,
    Phase1Pipeline,
)
from kis_backtest.luxon.stream.fred_hub import FREDHub
from kis_backtest.luxon.stream.tick_vault import TickVault
from kis_backtest.portfolio.macro_regime import MacroRegimeDashboard
from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider


# 성공 임계값 — 핸드오프 DoD 정확히 반영
FRED_MIN = 8
MACRO_MIN = 9
ERRORS_MAX = 1


def _hr(title: str) -> str:
    """섹션 구분 헤더."""
    bar = "=" * 72
    return f"\n{bar}\n  {title}\n{bar}"


def _print_result(r: Phase1CheckpointResult) -> None:
    """체크포인트 결과를 사람이 읽을 수 있게 출력."""
    regime_line = (
        r.regime_result.summary()
        if r.regime_result
        else "(None — classify_regime 실패)"
    )
    print(_hr("Phase 1 Checkpoint 결과"))
    print(f"  timestamp:             {r.timestamp.isoformat()}")
    print(f"  fred_series_loaded:    {r.fred_series_loaded}")
    print(f"  fred_stale_count:      {r.fred_stale_count}")
    print(f"  macro_indicator_count: {r.macro_indicator_count}")
    print(f"  tick_vault_stats:      {r.tick_vault_stats}")
    print(f"  regime:                {regime_line}")
    print(f"  errors ({len(r.errors)}):")
    for err in r.errors:
        print(f"    - {err}")


def _evaluate(r: Phase1CheckpointResult) -> list[str]:
    """임계값 평가. 실패 이유 리스트 (빈 리스트 = 전부 통과)."""
    checks = [
        (r.fred_series_loaded >= FRED_MIN,
         f"fred_series_loaded={r.fred_series_loaded} < {FRED_MIN}"),
        (r.macro_indicator_count >= MACRO_MIN,
         f"macro_indicator_count={r.macro_indicator_count} < {MACRO_MIN}"),
        (len(r.errors) <= ERRORS_MAX,
         f"errors={len(r.errors)} > {ERRORS_MAX}"),
        (r.regime_result is not None,
         "regime_result=None (classify_regime failed)"),
    ]
    return [msg for ok, msg in checks if not ok]


def _check_health(vps_host: str, timeout: float = 5.0) -> tuple[bool, str]:
    """MCP 서버 /health 200 OK 사전 확인."""
    url = f"http://{vps_host}/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status == 200:
                return True, body.strip()
            return False, f"status={resp.status} body={body[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


async def _run_smoke() -> int:
    """실 MCP 체크포인트 1회 실행. exit code 반환."""
    print(_hr("Luxon Phase 1 E2E 실 MCP 스모크"))
    print("  target: Nexus Finance MCP (macro_fred + ecos_* 10개 지표)")

    # MCPDataProvider 내부 토큰 체인: 인자 → MCP_VPS_TOKEN → ~/.mcp.json → ""
    # TickVault는 임시 디렉터리로 격리(운영 경로 오염 방지).
    with tempfile.TemporaryDirectory(prefix="luxon_smoke_") as tmpdir:
        mcp = MCPDataProvider()

        # 사전 health 체크 — 서버가 죽어있으면 60초 기다릴 필요 없이 즉시 실패
        vps_host = getattr(mcp, "_vps_host", os.environ.get("MCP_VPS_HOST", ""))
        ok, detail = _check_health(vps_host)
        if not ok:
            print(
                f"\n❌ MCP health 실패 (http://{vps_host}/health): {detail}\n"
                f"   서버 상태 또는 nginx IP 화이트리스트 확인 후 재실행.",
                file=sys.stderr,
            )
            return 1
        print(f"  health: http://{vps_host}/health → {detail}")

        tok = getattr(mcp, "_vps_token", "") or ""
        print(
            f"  token:  {'*' * 12}{tok[-4:]}  (Bearer 주입됨)"
            if tok
            else "  token:  (없음, IP 제한 기반 인증)"
        )

        pipeline = Phase1Pipeline(
            fred_hub=FREDHub(mcp=mcp),
            tick_vault=TickVault(root_dir=Path(tmpdir)),
            macro_dashboard=MacroRegimeDashboard(),
            mcp=mcp,
        )

        print("\n  → pipeline.checkpoint() 실행 중... (60초 이상 소요 가능)")
        try:
            result = await pipeline.checkpoint()
        except Exception as exc:  # noqa: BLE001 — checkpoint는 raise 하면 안 됨
            print(
                f"\n❌ 예기치 않은 예외: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 2
        finally:
            pipeline.close()

        _print_result(result)

        failures = _evaluate(result)
        print(_hr("검증 결과"))
        if not failures:
            print("  ✅ 전 항목 통과 — Phase 1 재료 준비 100% 완료")
            print("  다음 단계: Sprint 5 — Phase 2 GothamGraph 진입")
            return 0

        print("  ❌ 일부 실패:")
        for reason in failures:
            print(f"     · {reason}")
        return 3


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # 너무 시끄러운 httpx/httpcore는 WARNING으로
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        return asyncio.run(_run_smoke())
    except KeyboardInterrupt:
        print("\n(중단됨)", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
