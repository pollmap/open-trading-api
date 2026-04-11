"""
Luxon Terminal — Phase 1 E2E 실 MCP 스모크 (Sprint 4 STEP 3 / 4D)

목적:
    Phase1Pipeline.checkpoint()을 **실 Nexus Finance MCP**로 한 번 찌르고,
    Phase 1 재료 준비 100% 공식 선언 조건을 검증한다. 이 스크립트는 단위
    테스트에서 검증할 수 없는 실 네트워크 경로 — FREDHub → macro_fred MCP,
    MacroRegimeDashboard → ecos_*/macro_fred 10개 지표, MCPDataProvider
    세션 관리 — 의 장(live) 검증을 담당한다.

성공 기준 (모두 만족해야 Phase 1 완료):
    ✓ fred_series_loaded   >= 8   (Sprint 1 FREDHub, 10개 시리즈 중 8개 이상)
    ✓ macro_indicator_count >= 9  (Sprint 2.5 R11 10/10의 여유 1)
    ✓ len(errors)          <= 1   (일시적 네트워크 장애 1건 허용)
    ✓ regime_result        != None (classify_regime 성공)

실행:
    cd backtester
    ./.venv/Scripts/python.exe scripts/smoke_phase1_e2e.py

사전 연결성 검증:
    1. http://{VPS_HOST}/health 200 OK 확인 (exit 1 if fail)
    2. 토큰은 선택 — 현재 nexus-finance는 IP 제한/nginx auth 운영 중이라
       Bearer 토큰이 없어도 접근 가능. 있으면 provider 체인이 자동 주입.

제약 ❌:
    - 실 주문 전송 금지 (execution/ 진입 금지)
    - ConvictionBridge propose() 호출 금지 — 이 스크립트는 Phase1Pipeline만
      검증. ConvictionBridge는 단위 테스트에서 커버.
    - TickVault 경로 오염 금지 — 임시 디렉터리 사용 후 cleanup.

실데이터 원칙:
    이 스크립트는 목업을 만들지 않는다. 실 MCP가 응답하지 않으면 실패로
    보고하고 exit 2. "검증 통과"를 가짜로 찍지 않음 (R11 silent-fail 사고
    재발 방지).
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


def _format_header(title: str) -> str:
    bar = "=" * 72
    return f"\n{bar}\n  {title}\n{bar}"


def _print_result(result: Phase1CheckpointResult) -> None:
    """체크포인트 결과를 사람이 읽을 수 있게 출력."""
    print(_format_header("Phase 1 Checkpoint 결과"))
    print(f"  timestamp:            {result.timestamp.isoformat()}")
    print(f"  fred_series_loaded:   {result.fred_series_loaded}")
    print(f"  fred_stale_count:     {result.fred_stale_count}")
    print(f"  macro_indicator_count:{result.macro_indicator_count}")
    print(f"  tick_vault_stats:     {result.tick_vault_stats}")
    if result.regime_result is not None:
        print(f"  regime:               {result.regime_result.summary()}")
    else:
        print("  regime:               (None — classify_regime 실패)")
    print(f"  errors ({len(result.errors)}):")
    for err in result.errors:
        print(f"    - {err}")


def _evaluate(result: Phase1CheckpointResult) -> tuple[bool, list[str]]:
    """성공 기준 평가. (전부 통과?, 실패 이유 리스트)."""
    failures: list[str] = []

    if result.fred_series_loaded < FRED_MIN:
        failures.append(
            f"fred_series_loaded={result.fred_series_loaded} < "
            f"{FRED_MIN} (required)"
        )
    if result.macro_indicator_count < MACRO_MIN:
        failures.append(
            f"macro_indicator_count={result.macro_indicator_count} < "
            f"{MACRO_MIN} (required)"
        )
    if len(result.errors) > ERRORS_MAX:
        failures.append(
            f"errors={len(result.errors)} > {ERRORS_MAX} (max allowed)"
        )
    if result.regime_result is None:
        failures.append("regime_result=None (classify_regime failed)")

    return (not failures, failures)


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
    print(_format_header("Luxon Phase 1 E2E 실 MCP 스모크"))
    print("  target: Nexus Finance MCP (macro_fred + ecos_* 10개 지표)")

    # ── 컴포넌트 조립 ─────────────────────────────────────────
    # TickVault는 임시 디렉터리로 격리 (운영 경로 오염 방지).
    # MCPDataProvider는 내부에서 토큰 해결 체인을 스스로 돌림:
    #   인자 → MCP_VPS_TOKEN env → ~/.mcp.json → ""
    with tempfile.TemporaryDirectory(prefix="luxon_smoke_") as tmpdir:
        mcp = MCPDataProvider()

        # 사전 health 체크 — 서버가 죽어있으면 checkpoint() 60초 기다릴 필요 없음
        vps_host = getattr(mcp, "_vps_host", "62.171.141.206")
        ok, detail = _check_health(vps_host)
        if not ok:
            print(
                f"\n❌ MCP health check 실패 (http://{vps_host}/health): "
                f"{detail}",
                file=sys.stderr,
            )
            print(
                "   서버가 살아있는지, 현재 IP가 nginx 화이트리스트에\n"
                "   포함되어 있는지 확인 후 재실행.",
                file=sys.stderr,
            )
            return 1
        print(f"  health: http://{vps_host}/health → {detail}")

        resolved_token = getattr(mcp, "_vps_token", "")
        if resolved_token:
            masked = (
                f"{'*' * 12}{resolved_token[-4:]}"
                if len(resolved_token) > 4
                else "****"
            )
            print(f"  token:  {masked}  (Bearer 주입됨)")
        else:
            print("  token:  (없음, IP 제한 기반 인증)")
        fred_hub = FREDHub(mcp=mcp)
        tick_vault = TickVault(root_dir=Path(tmpdir))
        macro_dashboard = MacroRegimeDashboard()
        pipeline = Phase1Pipeline(
            fred_hub=fred_hub,
            tick_vault=tick_vault,
            macro_dashboard=macro_dashboard,
            mcp=mcp,
        )

        # ── 실행 ─────────────────────────────────────────────
        print("\n  → pipeline.checkpoint() 실행 중... (60초 이상 소요 가능)")
        try:
            result = await pipeline.checkpoint()
        except Exception as exc:  # noqa: BLE001
            print(
                f"\n❌ 예기치 않은 예외 (checkpoint는 raise 하지 말아야 함): "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 2
        finally:
            pipeline.close()

        _print_result(result)

        # ── 평가 ─────────────────────────────────────────────
        ok, failures = _evaluate(result)

        print(_format_header("검증 결과"))
        if ok:
            print("  ✅ 전 항목 통과 — Phase 1 재료 준비 100% 완료")
            print("     · FRED Hub:            OK")
            print("     · Macro Regime 10/10:  OK")
            print("     · classify_regime:     OK")
            print("     · 에러 허용 범위 내:    OK")
            print("\n  다음 단계: Sprint 5 — Phase 2 GothamGraph 진입")
            return 0

        print("  ❌ 일부 실패:")
        for reason in failures:
            print(f"     · {reason}")
        print(
            "\n  참고: R11 silent-fail 버그 이력 때문에 '부분 성공'을 성공으로\n"
            "        표기하지 않음. 실제 데이터가 임계값을 못 넘기면 실패."
        )
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
