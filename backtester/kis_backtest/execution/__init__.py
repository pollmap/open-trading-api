"""실전 매매 실행 패키지

QuantPipeline의 PortfolioOrder를 KIS 브로커리지 API로 연결하는 실행 계층.

Components:
    - models: PlannedTrade, ExecutionReport 등 실행 데이터모델
    - order_executor: LiveOrderExecutor (시그널 → 주문 변환 + 실행)
    - kill_switch: 파일 기반 긴급 정지
    - risk_gateway: 주문 전 7개 리스크 체크
    - alerts: AlertSystem (콘솔 + Discord 알림)
    - fill_tracker: FillTracker (체결 대사 + ReconciliationReport)
    - live_monitor: 실시간 포지션 모니터 + 드로다운 알림
    - vault_writer: Obsidian Vault 저장
    - review_scheduler: 일간 스냅샷 + 주간 복기 오케스트레이션
"""

from kis_backtest.execution.models import (
    PlannedTrade,
    ExecutionReport,
    TradeReason,
    TransactionCostEstimate,
)
from kis_backtest.execution.order_executor import LiveOrderExecutor
from kis_backtest.execution.kill_switch import KillSwitch
from kis_backtest.execution.risk_gateway import RiskGateway, GatewayDecision
from kis_backtest.execution.alerts import AlertSystem, AlertLevel
from kis_backtest.execution.fill_tracker import (
    FillTracker,
    TrackedOrder,
    ReconciliationReport,
)
from kis_backtest.execution.live_monitor import (
    LiveMonitor,
    MonitorState,
    PositionSnapshot,
)
from kis_backtest.execution.vault_writer import VaultWriter
from kis_backtest.execution.review_scheduler import (
    DailySnapshot,
    ReviewScheduler,
)

__all__ = [
    # models
    "PlannedTrade",
    "ExecutionReport",
    "TradeReason",
    "TransactionCostEstimate",
    # Phase 1
    "LiveOrderExecutor",
    # Phase 2
    "KillSwitch",
    "RiskGateway",
    "GatewayDecision",
    # Phase 3
    "AlertSystem",
    "AlertLevel",
    "FillTracker",
    "TrackedOrder",
    "ReconciliationReport",
    "LiveMonitor",
    "MonitorState",
    "PositionSnapshot",
    # Phase 4
    "VaultWriter",
    "ReviewScheduler",
    "DailySnapshot",
]
