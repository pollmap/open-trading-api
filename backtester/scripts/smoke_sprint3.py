"""
Sprint 3 DoD 수동 검증 스크립트 — Luxon Terminal TickVault 실 스트림 스모크.

목적:
    - 실제 Upbit(기본) 또는 KIS WebSocket에 연결해 N초 동안 틱 수집
    - TickVault(pickle)가 기대한 경로에 파일을 남기는지 검증
    - 저장된 파일을 TickReplayer로 즉시 재생해 라운드트립 검증
    - 목업/가짜 데이터 금지 — 실 스트림이 0틱이면 exit(2)로 실패 고지

실행 예:
    # Upbit (기본, 인증 불필요)
    python -m backtester.scripts.smoke_sprint3 \
        --exchange upbit \
        --symbols "KRW-BTC,KRW-ETH" \
        --duration 60 \
        --max-ticks 500

    # KIS (paper 인증 필요: ~/KIS/config/kis_devlp.yaml 또는 env)
    python -m backtester.scripts.smoke_sprint3 \
        --exchange kis \
        --symbols "005930,000660" \
        --duration 60

환경:
    - Python 3.13 / Windows / WSL / Linux 모두 호환
    - 임시 저장소: ~/.luxon/data/ticks_smoke (환경변수 LUXON_TICK_DATA_DIR 덮어씀)
    - 기존 ~/.luxon/data/ticks 저장소는 절대 건드리지 않음

금지:
    - 목업/가짜 틱 생성 금지 (실데이터 절대 원칙)
    - providers/* 파일 수정 금지 (사이드카 원칙)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path 보정: `python backtester/scripts/smoke_sprint3.py`로 직접 실행되는
# 경우에도 `kis_backtest` 패키지를 임포트할 수 있도록 backtester 디렉토리를
# sys.path에 선등록한다. `python -m backtester.scripts.smoke_sprint3` 모드에서는
# 이미 등록돼 있으므로 중복은 무해.
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_BACKTESTER_DIR = _THIS_FILE.parent.parent  # .../backtester
if str(_BACKTESTER_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKTESTER_DIR))

# 임시 저장소 경로 — TickVault 기본값을 덮어쓴다. 기존 운영 데이터 오염 방지.
_SMOKE_TICK_DIR = (Path.home() / ".luxon" / "data" / "ticks_smoke").expanduser()
os.environ["LUXON_TICK_DATA_DIR"] = str(_SMOKE_TICK_DIR)
# flush 간격을 1로 낮춰 수집 즉시 디스크 반영 → 검증 안정성 향상.
os.environ.setdefault("LUXON_TICK_FLUSH_INTERVAL", "1")
# 한글 출력 안정화 (Windows cp949 우회)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from kis_backtest.luxon.stream.replay import TickReplayer  # noqa: E402
from kis_backtest.luxon.stream.schema import Exchange, ReplaySpec  # noqa: E402
from kis_backtest.luxon.stream.tick_vault import TickVault  # noqa: E402

logger = logging.getLogger("smoke_sprint3")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Luxon Sprint 3 TickVault 수동 스모크 (실 스트림)",
    )
    parser.add_argument(
        "--exchange",
        choices=["upbit", "kis"],
        default="upbit",
        help="거래소 선택 (기본 upbit — KIS는 인증 설정 필요)",
    )
    parser.add_argument(
        "--symbols",
        default="KRW-BTC,KRW-ETH",
        help='쉼표 구분 심볼 리스트. 예: "KRW-BTC,KRW-ETH" 또는 "005930,000660"',
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="수집 시간(초). 기본 60",
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=500,
        help="저장할 최대 틱 수. 기본 500",
    )
    return parser.parse_args()


def _split_symbols(raw: str) -> list[str]:
    symbols = [s.strip() for s in raw.split(",") if s.strip()]
    if not symbols:
        raise SystemExit("[smoke] --symbols 가 비어 있음")
    return symbols


# ---------------------------------------------------------------------------
# Upbit path
# ---------------------------------------------------------------------------


async def _run_upbit(
    vault: TickVault,
    symbols: list[str],
    duration: int,
    max_ticks: int,
) -> int:
    """Upbit 스트림을 구독해 vault에 적재. 수집된 틱 수 반환."""
    from kis_backtest.luxon.stream.upbit_tick_tap import UpbitTickTap
    from kis_backtest.providers.upbit.websocket import UpbitWebSocket

    ws = UpbitWebSocket()
    tap = UpbitTickTap(vault=vault, ws=ws)

    logger.info(
        "[upbit] 구독 시작: codes=%s duration=%ds max_ticks=%d",
        symbols,
        duration,
        max_ticks,
    )

    try:
        await tap.run(
            codes=symbols,
            message_type="trade",
            max_ticks=max_ticks,
            duration_seconds=float(duration),
        )
    except asyncio.CancelledError:
        logger.warning("[upbit] 취소됨")
    except Exception as e:  # pragma: no cover - 네트워크 실패 등
        logger.error("[upbit] 구독 중 예외: %s", e)

    return tap.tick_count


# ---------------------------------------------------------------------------
# KIS path
# ---------------------------------------------------------------------------


def _run_kis(
    vault: TickVault,
    symbols: list[str],
    duration: int,
) -> int:
    """KIS 실시간 체결가 스트림 구독. 수집된 틱 수 반환.

    인증 실패(kis_devlp.yaml 없음 등)는 명확한 메시지로 고지.
    KISWebSocket.start()는 자체 asyncio.run을 호출하는 블로킹 API이므로
    timeout으로 duration 전달한다.
    """
    try:
        from kis_backtest.luxon.stream.kis_tick_tap import KISTickTap
        from kis_backtest.providers.kis.auth import KISAuth
        from kis_backtest.providers.kis.websocket import KISWebSocket
    except Exception as e:
        print(
            "[kis] 모듈 임포트 실패 — KIS 스택이 설치돼 있는지 확인 필요: "
            f"{e}",
            file=sys.stderr,
        )
        return 0

    try:
        auth = KISAuth.from_env("paper")
    except Exception as e:
        print(
            "[kis] 인증 로드 실패 — ~/KIS/config/kis_devlp.yaml 또는 환경변수 확인. "
            f"원인: {e}",
            file=sys.stderr,
        )
        return 0

    try:
        ws = KISWebSocket.from_auth(auth)
    except Exception as e:
        print(f"[kis] WebSocket 초기화 실패: {e}", file=sys.stderr)
        return 0

    tap = KISTickTap(vault=vault)

    try:
        ws.subscribe_price(symbols, tap.on_realtime_price)
    except Exception as e:
        print(f"[kis] subscribe_price 실패: {e}", file=sys.stderr)
        return 0

    logger.info(
        "[kis] 구독 시작: symbols=%s duration=%ds (블로킹)",
        symbols,
        duration,
    )
    try:
        ws.start(timeout=float(duration))
    except Exception as e:  # pragma: no cover - 네트워크/인증 런타임 실패
        logger.error("[kis] start 예외: %s", e)
    finally:
        try:
            ws.stop()
        except Exception:
            pass

    return tap.tick_count


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def _print_header(exchange: str, symbols: list[str], duration: int) -> None:
    print("=== Luxon Sprint 3 Smoke ===")
    print(f"Exchange: {exchange}")
    print(f"Symbols: {', '.join(symbols)}")
    print(f"Duration: {duration}s (collected)")
    print("-----------------------------")


def _print_symbol_row(
    symbol: str, tick_count: int, path: Path, size_bytes: int
) -> None:
    print(
        f"{symbol}: {tick_count:>4d} ticks → {path} ({_human_bytes(size_bytes)})"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    args = _parse_args()
    symbols = _split_symbols(args.symbols)
    exchange_enum = Exchange.UPBIT if args.exchange == "upbit" else Exchange.KIS

    # 임시 저장소 준비 (오염 방지)
    _SMOKE_TICK_DIR.mkdir(parents=True, exist_ok=True)
    vault = TickVault(root_dir=_SMOKE_TICK_DIR, flush_interval=1)

    _print_header(args.exchange, symbols, args.duration)

    # ---- 1) 수집 ---------------------------------------------------------
    if args.exchange == "upbit":
        collected = asyncio.run(
            _run_upbit(
                vault=vault,
                symbols=symbols,
                duration=args.duration,
                max_ticks=args.max_ticks,
            )
        )
    else:
        collected = _run_kis(
            vault=vault,
            symbols=symbols,
            duration=args.duration,
        )

    # 강제 flush — 세션 종료 시 누락 방지
    vault.flush_all()

    if collected <= 0:
        print("-----------------------------")
        print(
            "스트림 수신 0 — 거래소 비활성화 또는 네트워크 문제",
            file=sys.stderr,
        )
        return 2

    # ---- 2) describe 출력 -----------------------------------------------
    today = date.today()
    per_symbol_counts: dict[str, int] = {}
    for symbol in symbols:
        meta = vault.describe(exchange_enum, symbol, today)
        if meta is None:
            print(f"{symbol}:    0 ticks → (파일 없음)")
            per_symbol_counts[symbol] = 0
            continue
        _print_symbol_row(
            symbol=symbol,
            tick_count=meta.tick_count,
            path=meta.path,
            size_bytes=meta.bytes_on_disk,
        )
        per_symbol_counts[symbol] = meta.tick_count

    print("-----------------------------")

    # ---- 3) Replay 라운드트립 검증 --------------------------------------
    primary_symbol = symbols[0]
    collected_primary = per_symbol_counts.get(primary_symbol, 0)
    replayer = TickReplayer(vault)
    replayed = replayer.replay_list(
        exchange_enum,
        primary_symbol,
        today,
        ReplaySpec(speed=-1),
    )
    loaded_count = len(replayed)

    passed = loaded_count == collected_primary and loaded_count > 0
    status = "PASS" if passed else "FAIL"
    print(
        f"Replay check ({primary_symbol}): "
        f"{loaded_count} loaded == {collected_primary} collected → {status}"
    )

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
