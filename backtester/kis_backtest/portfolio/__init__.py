"""포트폴리오 분석 모듈

Docs:
- docs/p2-portfolio-plan.md

다중 자산 포트폴리오 분석 및 최적화 기능 제공.

Example:
    from kis_backtest.portfolio import PortfolioAnalyzer, PortfolioVisualizer
    
    # 분석
    analyzer = PortfolioAnalyzer(returns_df, weights)
    metrics = analyzer.analyze()
    
    # 시각화
    fig = PortfolioVisualizer.correlation_heatmap(metrics)
"""

from .analyzer import PortfolioAnalyzer, PortfolioMetrics
from .rebalance import RebalanceSimulator, RebalanceResult
from .visualizer import PortfolioVisualizer
from .mcp_bridge import MCPBridge, PortfolioOrder, StockAllocation, OrderAction
from .mcp_data_provider import MCPDataProvider
from .cufa_bridge import CUFABridge
from .review_engine import ReviewEngine, WeeklyReport, TradeRecord, KillCondition

__all__ = [
    "PortfolioAnalyzer",
    "PortfolioMetrics",
    "RebalanceSimulator",
    "RebalanceResult",
    "PortfolioVisualizer",
    "MCPBridge",
    "MCPDataProvider",
    "CUFABridge",
    "PortfolioOrder",
    "StockAllocation",
    "OrderAction",
    "ReviewEngine",
    "WeeklyReport",
    "TradeRecord",
    "KillCondition",
]
