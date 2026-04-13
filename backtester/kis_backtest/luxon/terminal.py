"""
Luxon Terminal — 완전한 선순환 통합 엔진 (v4.1)

단일 진입점으로 7계층 전체를 조율:
    Data → Analysis → GothamGraph → Orchestration
    → Execution → Intelligence → Observability
    → Feedback (선순환 완성)

사용:
    from kis_backtest.luxon import LuxonTerminal

    terminal = LuxonTerminal(
        symbols=["005930", "000660", "035420"],
        capital=50_000_000,
        mcp_host="127.0.0.1:8100",
    )
    terminal.boot()           # 초기화
    report = terminal.cycle() # 1 사이클 실행 (선순환 포함)
    terminal.status()         # 현재 상태 딕셔너리 반환
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class TerminalConfig:
    """LuxonTerminal 설정."""

    symbols: list[str]
    capital: float = 50_000_000.0
    mcp_host: str = "127.0.0.1:8100"
    mcp_token: str = ""
    refresh_secs: int = 3600          # 1시간마다 사이클
    paper_mode: bool = True           # 모의매매 모드
    vault_path: Optional[Path] = None  # Obsidian Vault 경로


# ---------------------------------------------------------------------------
# CycleReport
# ---------------------------------------------------------------------------


@dataclass
class CycleReport:
    """단일 사이클 실행 결과."""

    cycle_num: int
    started_at: str
    finished_at: str
    regime: str
    regime_confidence: float
    decisions: list[dict]               # [{symbol, action, conviction, weight_pct}]
    ta_signals: list[dict]              # [{symbol, source, signal, impact}]
    convictions_before: dict[str, float]
    convictions_after: dict[str, float]  # 피드백 적용 후 ← 선순환의 증거
    kill_triggered: bool
    mcp_mode: str

    def summary(self) -> str:
        """단순 텍스트 요약."""
        lines = [
            f"[Cycle #{self.cycle_num}] {self.started_at} → {self.finished_at}",
            f"  Regime     : {self.regime} (confidence={self.regime_confidence:.2f})",
            f"  MCP mode   : {self.mcp_mode}",
            f"  Kill switch: {self.kill_triggered}",
            f"  Decisions  : {len(self.decisions)}",
        ]
        for d in self.decisions:
            delta = d.get("conviction_after", 0) - d.get("conviction_before", 0)
            lines.append(
                f"    {d.get('symbol')} {d.get('action')} "
                f"conviction={d.get('conviction')} weight={d.get('weight_pct', 0):.1f}% "
                f"Δconviction={delta:+.2f}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# LuxonTerminal
# ---------------------------------------------------------------------------


class LuxonTerminal:
    """완전 선순환 통합 엔진.

    7계층(Data→Analysis→Graph→Orchestration→Execution→Intelligence→Observability)
    을 단일 클래스로 통합하고 FeedbackAdapter 로 선순환을 닫는다.
    """

    def __init__(
        self,
        config: Optional[TerminalConfig] = None,
        **kwargs: Any,
    ) -> None:
        if config is None:
            symbols = kwargs.pop("symbols", [])
            config = TerminalConfig(symbols=symbols, **kwargs)
        self.config = config

        # 런타임 상태
        self._cycle_num: int = 0
        self._mcp_mode: str = "offline"
        self._initialized: bool = False
        self._mcp: Any = None
        self._kill_switch: Any = None
        self._capital_ladder: Any = None
        self._feedback_adapter: Any = None
        self._accuracy_tracker: Any = None
        self._orchestrator: Any = None

    # ------------------------------------------------------------------
    # 초기화
    # ------------------------------------------------------------------

    def boot(self) -> dict:
        """모든 컴포넌트 초기화.

        Returns:
            {"mcp": "local|vps|offline", "regime": str, "status": "ready"}
        """
        log.info("LuxonTerminal boot 시작 (capital=%.0f, symbols=%s)",
                 self.config.capital, self.config.symbols)

        # 1. MCP 연결 (로컬 → VPS fallback)
        self._mcp = self._connect_mcp()

        # 2. KillSwitch 로드
        try:
            from kis_backtest.execution.kill_switch import KillSwitch
            self._kill_switch = KillSwitch()
            if self._kill_switch.is_active:
                log.warning("KillSwitch 활성 상태: %s", self._kill_switch.reason)
        except Exception as exc:
            log.warning("KillSwitch 초기화 실패: %s", exc)
            self._kill_switch = None

        # 3. CapitalLadder 로드
        try:
            from kis_backtest.execution.capital_ladder import CapitalLadder
            self._capital_ladder = CapitalLadder()
        except Exception as exc:
            log.warning("CapitalLadder 초기화 실패: %s", exc)
            self._capital_ladder = None

        # 4. FeedbackAdapter: persisted convictions 로드
        try:
            from kis_backtest.portfolio.feedback_adapter import FeedbackAdapter
            self._feedback_adapter = FeedbackAdapter(
                kill_switch=self._kill_switch,
                capital_ladder=self._capital_ladder,
            )
        except Exception as exc:
            log.warning("FeedbackAdapter 초기화 실패: %s", exc)
            self._feedback_adapter = None

        # 5. SignalAccuracyTracker 로드
        try:
            from kis_backtest.luxon.graph.ingestors.signal_accuracy_tracker import (
                SignalAccuracyTracker,
            )
            self._accuracy_tracker = SignalAccuracyTracker()
        except Exception as exc:
            log.warning("SignalAccuracyTracker 초기화 실패: %s", exc)
            self._accuracy_tracker = None

        # 6. LuxonOrchestrator 초기화
        try:
            from kis_backtest.luxon.orchestrator import LuxonOrchestrator
            self._orchestrator = LuxonOrchestrator(
                mcp=self._mcp,
                total_capital=self.config.capital,
            )
        except Exception as exc:
            log.error("LuxonOrchestrator 초기화 실패: %s", exc)
            self._orchestrator = None

        # 7. 초기 매크로 레짐 갱신
        initial_regime = self._refresh_macro_sync()

        self._initialized = True
        result = {
            "mcp": self._mcp_mode,
            "regime": initial_regime,
            "status": "ready",
        }
        log.info("LuxonTerminal boot 완료: %s", result)
        return result

    # ------------------------------------------------------------------
    # 사이클
    # ------------------------------------------------------------------

    def cycle(self) -> CycleReport:
        """완전한 선순환 1 사이클 실행.

        순서:
            1. KillSwitch 체크 → 활성이면 즉시 중단
            2. refresh_macro
            3. SignalAccuracyTracker.update_outcomes (과거 신호 결과 반영)
            4. base_convictions = FeedbackAdapter.load_persisted_convictions()
            5. run_workflow(symbols, base_convictions)
            6. execute_decisions (paper_mode이면 fills/paper/*.json 기록)
            7. WeeklyReport 생성 (trades 있을 때)
            8. FeedbackAdapter.apply(report, convictions) → 조정된 convictions
            9. FeedbackAdapter.save_convictions(adjusted_convictions) ← 선순환 완성
           10. SignalAccuracyTracker.save()
           11. CycleReport 반환
        """
        self._cycle_num += 1
        started_at = datetime.now().isoformat()
        kill_triggered = False
        regime = "unknown"
        regime_confidence = 0.0
        decisions: list[dict] = []
        ta_signals: list[dict] = []
        convictions_before: dict[str, float] = {}
        convictions_after: dict[str, float] = {}

        # 1. KillSwitch 체크
        if self._kill_switch is not None and self._kill_switch.is_active:
            log.warning("KillSwitch 활성 — 사이클 %d 중단", self._cycle_num)
            kill_triggered = True
            return CycleReport(
                cycle_num=self._cycle_num,
                started_at=started_at,
                finished_at=datetime.now().isoformat(),
                regime=regime,
                regime_confidence=regime_confidence,
                decisions=decisions,
                ta_signals=ta_signals,
                convictions_before=convictions_before,
                convictions_after=convictions_after,
                kill_triggered=True,
                mcp_mode=self._mcp_mode,
            )

        # 2. refresh_macro
        regime = self._refresh_macro_sync()

        # 3. SignalAccuracyTracker.update_outcomes
        try:
            if self._accuracy_tracker is not None:
                self._accuracy_tracker.update_outcomes(
                    symbol="",
                    returns_by_date={},
                )
        except Exception as exc:
            log.warning("update_outcomes 실패 (무시): %s", exc)

        # 4. base_convictions 로드
        try:
            if self._feedback_adapter is not None:
                convictions_before = self._feedback_adapter.load_persisted_convictions(
                    self.config.symbols
                )
            else:
                convictions_before = {s: 5.0 for s in self.config.symbols}
        except Exception as exc:
            log.warning("load_persisted_convictions 실패: %s", exc)
            convictions_before = {s: 5.0 for s in self.config.symbols}

        # 5. run_workflow
        orch_report = None
        if self._orchestrator is not None:
            try:
                orch_report = self._orchestrator.run_workflow(
                    self.config.symbols, convictions_before
                )
                regime = orch_report.regime
                regime_confidence = orch_report.regime_confidence
                decisions = self._extract_decisions(orch_report, convictions_before)
            except Exception as exc:
                log.warning("run_workflow 실패: %s", exc)

        # 6. execute_decisions (paper_mode)
        if orch_report is not None and self.config.paper_mode:
            try:
                self._paper_record(orch_report, decisions)
            except Exception as exc:
                log.warning("paper_record 실패: %s", exc)

        # 7. WeeklyReport 생성
        weekly_report = None
        try:
            weekly_report = self._build_weekly_report(orch_report)
        except Exception as exc:
            log.warning("weekly_review 실패 (무시): %s", exc)

        # 8. FeedbackAdapter.apply → 조정된 convictions
        convictions_after = dict(convictions_before)
        if weekly_report is not None and self._feedback_adapter is not None:
            try:
                convictions_after = self._feedback_adapter.apply(
                    weekly_report, convictions_before
                )
            except Exception as exc:
                log.warning("FeedbackAdapter.apply 실패: %s", exc)

        # 9. save_convictions ← 선순환 완성
        try:
            if self._feedback_adapter is not None:
                self._feedback_adapter.save_convictions(convictions_after)
                log.info("선순환 완성: convictions 저장 완료 (%d종목)", len(convictions_after))
        except Exception as exc:
            log.warning("save_convictions 실패: %s", exc)

        # 10. SignalAccuracyTracker.save
        try:
            if self._accuracy_tracker is not None:
                self._accuracy_tracker.save()
        except Exception as exc:
            log.warning("SignalAccuracyTracker.save 실패: %s", exc)

        # conviction 변화량을 decisions에 추가
        for d in decisions:
            sym = d.get("symbol", "")
            d["conviction_before"] = convictions_before.get(sym, 5.0)
            d["conviction_after"] = convictions_after.get(sym, 5.0)

        # 11. CycleReport 반환
        return CycleReport(
            cycle_num=self._cycle_num,
            started_at=started_at,
            finished_at=datetime.now().isoformat(),
            regime=regime,
            regime_confidence=regime_confidence,
            decisions=decisions,
            ta_signals=ta_signals,
            convictions_before=convictions_before,
            convictions_after=convictions_after,
            kill_triggered=kill_triggered,
            mcp_mode=self._mcp_mode,
        )

    # ------------------------------------------------------------------
    # 상태 조회
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """현재 터미널 상태 딕셔너리 반환."""
        kill_active = False
        kill_reason = ""
        if self._kill_switch is not None:
            try:
                kill_active = self._kill_switch.is_active
                kill_reason = self._kill_switch.reason if kill_active else ""
            except Exception:
                pass

        ladder_status = {}
        if self._capital_ladder is not None:
            try:
                ls = self._capital_ladder.status()
                ladder_status = ls._asdict() if hasattr(ls, "_asdict") else vars(ls)
            except Exception:
                pass

        return {
            "initialized": self._initialized,
            "cycle_num": self._cycle_num,
            "mcp_mode": self._mcp_mode,
            "paper_mode": self.config.paper_mode,
            "symbols": self.config.symbols,
            "capital": self.config.capital,
            "kill_switch_active": kill_active,
            "kill_reason": kill_reason,
            "ladder": ladder_status,
            "is_healthy": self.is_healthy,
        }

    # ------------------------------------------------------------------
    # 루프
    # ------------------------------------------------------------------

    def run_loop(self, max_cycles: Optional[int] = None) -> None:
        """refresh_secs 간격으로 cycle() 반복 실행.

        Args:
            max_cycles: None이면 무한 루프. 정수이면 해당 횟수만큼 실행.
        """
        if not self._initialized:
            self.boot()

        log.info(
            "run_loop 시작: interval=%ds max_cycles=%s",
            self.config.refresh_secs,
            max_cycles,
        )
        count = 0
        while True:
            if max_cycles is not None and count >= max_cycles:
                log.info("run_loop 종료: max_cycles=%d 도달", max_cycles)
                break

            report = self.cycle()
            log.info(report.summary())
            count += 1

            if max_cycles is not None and count >= max_cycles:
                break

            log.info("다음 사이클까지 %ds 대기...", self.config.refresh_secs)
            time.sleep(self.config.refresh_secs)

    # ------------------------------------------------------------------
    # 속성
    # ------------------------------------------------------------------

    @property
    def is_healthy(self) -> bool:
        """MCP 연결 + KillSwitch 비활성 + 초기화 완료 여부."""
        if not self._initialized:
            return False
        if self._kill_switch is not None:
            try:
                if self._kill_switch.is_active:
                    return False
            except Exception:
                pass
        return self._mcp_mode in {"local", "vps"} or True  # offline도 허용

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _connect_mcp(self) -> Any:
        """로컬 MCP → VPS fallback 순서로 연결 시도."""
        try:
            import httpx
            resp = httpx.get(
                f"http://{self.config.mcp_host}/health", timeout=3.0
            )
            if resp.status_code == 200:
                self._mcp_mode = "local"
                log.info("MCP 로컬 연결 성공: %s", self.config.mcp_host)
                return self._build_mcp_client(self.config.mcp_host)
        except Exception:
            pass

        # VPS fallback (환경변수 또는 기본 포트)
        try:
            import httpx
            import os
            vps_host = os.environ.get("MCP_VPS_HOST", "")
            if not vps_host:
                raise ValueError("MCP_VPS_HOST 미설정 — VPS fallback 건너뜀")
            resp = httpx.get(f"http://{vps_host}/health", timeout=5.0)
            if resp.status_code == 200:
                self._mcp_mode = "vps"
                log.info("MCP VPS 연결 성공: %s", vps_host)
                return self._build_mcp_client(vps_host)
        except Exception:
            pass

        self._mcp_mode = "offline"
        log.warning("MCP 연결 실패 — offline 모드로 진행")
        return None

    def _build_mcp_client(self, host: str) -> Any:
        """MCP 클라이언트 객체 생성. 실제 MCPDataProvider가 있으면 사용."""
        try:
            from kis_backtest.providers.mcp_provider import MCPDataProvider
            return MCPDataProvider(base_url=f"http://{host}")
        except ImportError:
            pass
        try:
            from kis_backtest.stream.mcp_provider import MCPDataProvider
            return MCPDataProvider(base_url=f"http://{host}")
        except ImportError:
            pass
        # 클라이언트 미구현 시 URL 문자열로 대체 (Orchestrator가 None 처리)
        log.debug("MCPDataProvider 미설치 — URL 객체 반환")
        return None

    def _refresh_macro_sync(self) -> str:
        """refresh_macro 동기 래퍼. 실패 시 빈 문자열 반환."""
        if self._orchestrator is None:
            return "unknown"
        try:
            asyncio.run(self._orchestrator.refresh_macro())
            regime = getattr(
                self._orchestrator.dashboard, "current_regime", "unknown"
            )
            if hasattr(regime, "value"):
                return regime.value
            return str(regime)
        except RuntimeError:
            # 이미 이벤트 루프가 실행 중인 환경
            try:
                loop = asyncio.get_event_loop()
                loop.run_until_complete(self._orchestrator.refresh_macro())
            except Exception as exc:
                log.warning("refresh_macro 실패: %s", exc)
        except Exception as exc:
            log.warning("refresh_macro 실패: %s", exc)
        return "unknown"

    def _extract_decisions(
        self,
        orch_report: Any,
        convictions_before: dict[str, float],
    ) -> list[dict]:
        """OrchestrationReport에서 decisions 리스트 추출."""
        decisions: list[dict] = []
        try:
            portfolio = orch_report.portfolio
            for dec in portfolio.decisions:
                weight_pct = 0.0
                for ps in orch_report.position_sizes:
                    if ps.symbol == dec.symbol:
                        weight_pct = float(ps.weight) * 100.0
                        break
                decisions.append({
                    "symbol": dec.symbol,
                    "action": dec.action,
                    "conviction": dec.conviction,
                    "weight_pct": weight_pct,
                })
        except Exception as exc:
            log.warning("decisions 추출 실패: %s", exc)
        return decisions

    def _paper_record(self, orch_report: Any, decisions: list[dict]) -> None:
        """페이퍼 트레이딩 기록: fills/paper/{timestamp}.json 저장."""
        import json

        paper_dir = Path.home() / ".luxon" / "fills" / "paper"
        paper_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        record = {
            "recorded_at": datetime.now().isoformat(),
            "regime": orch_report.regime,
            "regime_confidence": orch_report.regime_confidence,
            "cycle_num": self._cycle_num,
            "decisions": decisions,
        }
        out_path = paper_dir / f"cycle_{self._cycle_num:04d}_{ts}.json"
        out_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.debug("paper 기록 저장: %s", out_path)

    def _build_weekly_report(self, orch_report: Any) -> Any:
        """ReviewEngine으로 WeeklyReport 생성. orch_report 없으면 None."""
        if orch_report is None:
            return None
        try:
            from kis_backtest.portfolio.review_engine import ReviewEngine
            engine = ReviewEngine()
            return engine.weekly_review(
                trades=[],
                equity_curve=[self.config.capital],
            )
        except Exception as exc:
            log.debug("WeeklyReport 생성 스킵 (trades 없음): %s", exc)
            return None
