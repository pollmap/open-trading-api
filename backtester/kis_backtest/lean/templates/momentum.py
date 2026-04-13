"""단순 모멘텀 Lean 전략 템플릿.

12개월 수익률 상위 종목 동일가중, 매월 리밸런싱.
"""

MOMENTUM_MAIN_PY = '''
from AlgorithmImports import *


class Algorithm(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2024, 1, 1)
        self.SetEndDate(2026, 4, 1)
        self.SetCash(10_000_000)

        symbols_str = self.GetParameter("symbols") or ""
        self.tickers = [s.strip() for s in symbols_str.split(",") if s.strip()]

        self.symbols = []
        for t in self.tickers:
            eq = self.AddEquity(t, Resolution.Daily)
            self.symbols.append(eq.Symbol)

        self.lookback = 252  # 12개월
        self.rebalance_day = None

        self.Schedule.On(
            self.DateRules.MonthStart(),
            self.TimeRules.AfterMarketOpen(self.tickers[0] if self.tickers else "SPY", 30),
            self.Rebalance,
        )

    def Rebalance(self):
        if not self.symbols:
            return

        perf = {}
        for sym in self.symbols:
            hist = self.History(sym, self.lookback, Resolution.Daily)
            if hist.empty or "close" not in hist.columns:
                continue
            closes = hist["close"].values
            if len(closes) < 2:
                continue
            ret = (closes[-1] / closes[0]) - 1.0
            perf[sym] = ret

        if not perf:
            return

        ranked = sorted(perf.items(), key=lambda kv: kv[1], reverse=True)
        top = [sym for sym, _ in ranked[:5]]

        weight = 1.0 / len(top) if top else 0.0
        for sym in self.symbols:
            if sym in top:
                self.SetHoldings(sym, weight)
            else:
                self.Liquidate(sym)

    def OnData(self, data):
        pass
'''
