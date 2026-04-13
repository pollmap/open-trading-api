"""Luxon Intelligence CLI — `python -m kis_backtest.luxon.intelligence`.

서브명령:
    bootstrap    — 전 스택 헬스체크 + 자동 기동 + 워밍업
    health       — 빠른 헬스 스캔
    security     — 보안 preflight (토큰·엔드포인트·경로)
    bench        — 3티어 TPS 벤치마크
    ask          — 단일 프롬프트 실행 (--tier 선택)
    agent        — agentic_run 엔드투엔드 (MCP 연결 필요)
    cufa         — CUFA 보고서 빌드 (local_runner 트리거)
"""
from __future__ import annotations

import argparse
import io
import sys

# Windows cp949 콘솔에서 유니코드 기호 출력 가능하게 UTF-8 강제.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, io.UnsupportedOperation):
        pass

from kis_backtest.luxon.intelligence import (
    Tier,
    agentic_run,
    call,
    health_check_all,
)


def _cmd_bootstrap(args) -> int:
    from kis_backtest.luxon.intelligence.bootstrap import bootstrap

    rep = bootstrap(
        auto_start_stack=not args.no_start,
        warmup_timeout=args.warmup_timeout,
    )
    print(rep.format_report())
    return 0 if rep.any_llm_ready else 1


def _cmd_health(args) -> int:
    print("=== Luxon Health Scan ===")
    h = health_check_all()
    for name, ok in h.items():
        mark = "[OK]" if ok else "[--]"
        print(f"  {mark} {name}")
    return 0 if any(h.values()) else 1


def _cmd_security(args) -> int:
    from kis_backtest.luxon.intelligence.security import preflight

    rep = preflight()
    print(rep.format_report())
    return 1 if rep.has_blockers else 0


def _cmd_bench(args) -> int:
    from kis_backtest.luxon.intelligence import bench as bench_mod

    return bench_mod.main()


def _cmd_ask(args) -> int:
    tier = Tier[args.tier]
    out = call(
        tier,
        system=args.system or "간결히 한국어로 답.",
        user=args.prompt,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
    )
    print(out)
    return 0


def _cmd_agent(args) -> int:
    servers = args.servers.split(",") if args.servers else None
    result = agentic_run(
        args.prompt,
        tier=Tier[args.tier],
        mcp_servers=servers,
        max_steps=args.max_steps,
    )
    print("=== Agentic Run ===")
    print(result.final_content)
    print(f"\n[steps: {len(result.steps)}, tool_calls: {result.total_tool_calls}]")
    return 0


def _cmd_cufa(args) -> int:
    import runpy
    from pathlib import Path

    runner = Path(r"C:/Users/lch68/.claude/skills/cufa-equity-report/local_runner.py")
    if not runner.exists():
        print(f"[ERR] local_runner.py not found at {runner}", file=sys.stderr)
        return 2
    sys.argv = ["local_runner", "--config", args.config, "--out", args.out]
    if args.heavy_thesis:
        sys.argv.append("--heavy-thesis")
    if args.skip_health:
        sys.argv.append("--skip-health-check")
    runpy.run_path(str(runner), run_name="__main__")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="luxon-intelligence")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("bootstrap", help="전 스택 기동+헬스+워밍업")
    sp.add_argument("--no-start", action="store_true", help="자동 기동 스크립트 실행 안 함")
    sp.add_argument("--warmup-timeout", type=float, default=120.0)
    sp.set_defaults(func=_cmd_bootstrap)

    sp = sub.add_parser("health", help="빠른 헬스 스캔")
    sp.set_defaults(func=_cmd_health)

    sp = sub.add_parser("security", help="보안 preflight")
    sp.set_defaults(func=_cmd_security)

    sp = sub.add_parser("bench", help="3티어 TPS 벤치마크")
    sp.set_defaults(func=_cmd_bench)

    sp = sub.add_parser("ask", help="단일 프롬프트")
    sp.add_argument("prompt")
    sp.add_argument("--tier", choices=[t.name for t in Tier], default="DEFAULT")
    sp.add_argument("--system", default="")
    sp.add_argument("--max-tokens", type=int, default=300)
    sp.add_argument("--temperature", type=float, default=0.3)
    sp.set_defaults(func=_cmd_ask)

    sp = sub.add_parser("agent", help="agentic run (MCP tool-calling)")
    sp.add_argument("prompt")
    sp.add_argument("--tier", choices=[t.name for t in Tier], default="DEFAULT")
    sp.add_argument("--servers", default="", help="쉼표 구분 MCP 서버명")
    sp.add_argument("--max-steps", type=int, default=5)
    sp.set_defaults(func=_cmd_agent)

    sp = sub.add_parser("cufa", help="CUFA 보고서 로컬 빌드")
    sp.add_argument("--config", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--heavy-thesis", action="store_true")
    sp.add_argument("--skip-health", action="store_true")
    sp.set_defaults(func=_cmd_cufa)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
