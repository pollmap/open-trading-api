"""
Luxon Terminal — Phase 1 통합 파이프라인 (Sprint 4 STEP 2)

FRED Hub (Sprint 1) + TickVault (Sprint 3) + MacroRegimeDashboard (Sprint 2.5 R11
완전 해결) 세 개 기둥을 하나의 얇은 오케스트레이터로 묶는 모듈.

설계 원칙:
    - **신규 계산 로직 0줄** — 모든 전송/변환/분류는 기존 모듈이 이미 완성.
      여기서는 DI + lifecycle + 실패 격리만 한다.
    - **silent-fail 금지** — 모든 예외는 `Phase1CheckpointResult.errors`로
      보고하되 raise 하지 않는다. Sprint 2 R11 교훈(MCP 실패를 log.warning으로
      삼켜서 macro_regime이 3/10만 잡혔던 사고) 재발 방지.
    - **교체 가능성** — Phase 2 GothamGraph 진입 시 checkpoint() 결과에 노드 생성
      훅만 추가하면 되도록 dataclass 반환.

무엇이 금지되어 있나:
    ❌ FRED transform, regime classification 같은 계산 로직 신규 작성
    ❌ execution/, providers/kis, providers/upbit 수정
    ❌ 실 MCP 호출 경로 우회 (macro_dashboard.fetch_indicators가 담당)
    ❌ ConvictionSizer 통합 (STEP 3 4E에서 처음 등장)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from kis_backtest.luxon.stream.fred_hub import FREDHub
from kis_backtest.luxon.stream.tick_vault import TickVault
from kis_backtest.portfolio.macro_regime import (
    MacroRegimeDashboard,
    RegimeResult,
)

if TYPE_CHECKING:  # 순환 회피 (MCPDataProvider → macro_regime → ...)
    from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider

logger = logging.getLogger(__name__)


@dataclass
class Phase1CheckpointResult:
    """통합 체크포인트 실행 결과 (관측용 스냅샷).

    이 객체는 **순수 관측 데이터**다. 실데이터 원칙에 따라 목업 필드 0,
    기본값은 "알 수 없음/없음"을 명시적으로 표현할 수 있는 값만 쓴다.
    frozen=False — errors 리스트는 checkpoint 중 추가되므로.

    Attributes:
        timestamp: checkpoint 실행 시각
        fred_series_loaded: FREDHub.load_all()이 성공적으로 반환한 시리즈 수
        fred_stale_count: detect_staleness.is_stale=True 시리즈 수
        tick_vault_stats: TickVault.stats() 그대로 (total_files, buffered_keys 등)
        regime_result: macro_dashboard.classify_regime() 결과. 실패 시 None
        macro_indicator_count: fetch_indicators()가 잡은 지표 수 (R11 10/10 검증용)
        errors: 실패한 컴포넌트 문자열 리스트. 빈 리스트 = 전부 성공
    """

    timestamp: datetime
    fred_series_loaded: int
    fred_stale_count: int
    tick_vault_stats: dict[str, Any]
    regime_result: RegimeResult | None
    macro_indicator_count: int
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """모든 컴포넌트 성공 여부."""
        return not self.errors

    @property
    def partial_success(self) -> bool:
        """최소 1개 컴포넌트는 성공했는가?"""
        return (
            self.fred_series_loaded > 0
            or self.regime_result is not None
            or bool(self.tick_vault_stats)
        )


class Phase1Pipeline:
    """FRED + TickVault + MacroRegime 통합 오케스트레이터.

    생성자에서 이미 생성된 3개 모듈 인스턴스를 주입받는다. 파이프라인 자체는
    상태를 거의 갖지 않으며, 각 컴포넌트의 lifecycle은 해당 모듈이 담당한다.

    Attributes:
        fred_hub: Sprint 1 FREDHub (macro_fred MCP 경로 내장)
        tick_vault: Sprint 3 TickVault (pickle 일별 저장)
        macro_dashboard: Sprint 2.5 MacroRegimeDashboard (R11 10/10 완전 해결본)
        mcp: MCPDataProvider (macro_dashboard.fetch_indicators가 사용)
    """

    def __init__(
        self,
        fred_hub: FREDHub,
        tick_vault: TickVault,
        macro_dashboard: MacroRegimeDashboard,
        mcp: "MCPDataProvider",
    ) -> None:
        self._fred = fred_hub
        self._vault = tick_vault
        self._macro = macro_dashboard
        self._mcp = mcp

    @property
    def fred_hub(self) -> FREDHub:
        return self._fred

    @property
    def tick_vault(self) -> TickVault:
        return self._vault

    @property
    def macro_dashboard(self) -> MacroRegimeDashboard:
        return self._macro

    async def checkpoint(self) -> Phase1CheckpointResult:
        """한 번의 통합 스냅샷 수집.

        순서:
            1. FRED load_all() → 시리즈 수/staleness 집계
            2. macro_dashboard.fetch_indicators(mcp) → 지표 수집
            3. macro_dashboard.classify_regime() → RegimeResult
            4. tick_vault.stats() → 저장소 현황
            5. 실패는 errors 리스트로 수집 (raise 금지)

        이 메서드는 **절대 raise 하지 않는다**. 모든 예외는 errors에 기록.
        실패한 단계의 필드는 "없음/0"으로 기본값 유지 → caller가 `success` /
        `partial_success` 프로퍼티로 상태 판단.

        Returns:
            Phase1CheckpointResult (항상 반환)
        """
        errors: list[str] = []

        # 1. FRED Hub (load_all + staleness)
        fred_count = 0
        stale_count = 0
        try:
            all_series = await self._fred.load_all()
            fred_count = len(all_series)
            for series in all_series.values():
                try:
                    report = self._fred.detect_staleness(series)
                    if getattr(report, "is_stale", False):
                        stale_count += 1
                except Exception as e:  # 개별 staleness 실패는 경미
                    logger.warning(
                        "Phase1Pipeline: staleness 감지 실패 err=%s", e
                    )
        except Exception as e:
            msg = f"FRED: {type(e).__name__}: {e}"
            errors.append(msg)
            logger.warning("Phase1Pipeline %s", msg)

        # 2-3. MacroRegime (fetch → classify)
        regime_result: RegimeResult | None = None
        indicator_count = 0
        try:
            indicators = await self._macro.fetch_indicators(self._mcp)
            indicator_count = len(indicators) if indicators else 0
            try:
                regime_result = self._macro.classify_regime()
            except Exception as e:
                msg = f"MacroRegime.classify: {type(e).__name__}: {e}"
                errors.append(msg)
                logger.warning("Phase1Pipeline %s", msg)
        except Exception as e:
            msg = f"MacroRegime.fetch: {type(e).__name__}: {e}"
            errors.append(msg)
            logger.warning("Phase1Pipeline %s", msg)

        # 4. TickVault stats (항상 sync, 거의 실패하지 않음)
        vault_stats: dict[str, Any] = {}
        try:
            vault_stats = dict(self._vault.stats())
        except Exception as e:
            msg = f"TickVault.stats: {type(e).__name__}: {e}"
            errors.append(msg)
            logger.warning("Phase1Pipeline %s", msg)

        result = Phase1CheckpointResult(
            timestamp=datetime.now(),
            fred_series_loaded=fred_count,
            fred_stale_count=stale_count,
            tick_vault_stats=vault_stats,
            regime_result=regime_result,
            macro_indicator_count=indicator_count,
            errors=errors,
        )
        logger.info(
            "Phase1 checkpoint: fred=%d/stale=%d macro=%d vault_files=%s "
            "errors=%d",
            result.fred_series_loaded,
            result.fred_stale_count,
            result.macro_indicator_count,
            result.tick_vault_stats.get("total_files", "?"),
            len(result.errors),
        )
        return result

    def close(self) -> None:
        """세션 종료 정리. TickVault 버퍼를 디스크로 flush.

        FREDHub/MacroRegime은 stateful한 외부 리소스를 잡지 않으므로 별도
        cleanup 불필요. TickVault.flush_all()은 Sprint 4 STEP 1 M5 패치 덕분에
        부분 실패를 격리한다.
        """
        try:
            self._vault.flush_all()
        except Exception as e:
            logger.warning(
                "Phase1Pipeline.close: TickVault flush 실패 err=%s", e
            )


__all__ = ["Phase1Pipeline", "Phase1CheckpointResult"]
