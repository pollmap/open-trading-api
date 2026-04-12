"use client";

import { useState } from "react";

interface Decision {
  symbol: string;
  action: string;
  weight: number;
  catalyst_score: number;
  conviction: number;
}

interface PositionSize {
  symbol: string;
  weight: number;
  amount: number;
}

interface AnalyzeResult {
  regime: string;
  regime_confidence: number;
  decisions: Decision[];
  position_sizes: PositionSize[];
  cross_references: Record<string, string[]>;
  summary_markdown: string;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8002";

const DEFAULT_SYMBOLS = "005930,000660,035420,373220,207940";

export default function LuxonPage() {
  const [symbols, setSymbols] = useState(DEFAULT_SYMBOLS);
  const [capital, setCapital] = useState(100_000_000);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AnalyzeResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleAnalyze = async () => {
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const symbolList = symbols
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);

      const res = await fetch(`${API_BASE}/api/luxon/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbols: symbolList,
          total_capital: capital,
        }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `HTTP ${res.status}`);
      }

      setResult(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold mb-6">Luxon Terminal</h1>
      <p className="text-slate-500 dark:text-slate-400 mb-8">
        Ackman + Druckenmiller AI Investment Analysis
      </p>

      {/* Input */}
      <div className="space-y-4 mb-8">
        <div>
          <label className="block text-sm font-medium mb-1">
            Symbols (comma separated)
          </label>
          <input
            type="text"
            value={symbols}
            onChange={(e) => setSymbols(e.target.value)}
            className="w-full px-3 py-2 border rounded-lg dark:bg-slate-800 dark:border-slate-700"
            placeholder="005930,000660,035420"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">
            Total Capital (KRW)
          </label>
          <input
            type="number"
            value={capital}
            onChange={(e) => setCapital(Number(e.target.value))}
            className="w-full px-3 py-2 border rounded-lg dark:bg-slate-800 dark:border-slate-700"
          />
        </div>
        <button
          onClick={handleAnalyze}
          disabled={loading}
          className="w-full sm:w-auto px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? "Analyzing..." : "Analyze"}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="p-4 mb-6 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-red-700 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="space-y-6">
          {/* Regime */}
          <div className="p-4 bg-slate-50 dark:bg-slate-800 rounded-lg">
            <div className="text-sm text-slate-500">Macro Regime</div>
            <div className="text-xl font-bold">
              {result.regime}{" "}
              <span className="text-sm font-normal text-slate-500">
                ({(result.regime_confidence * 100).toFixed(0)}% confidence)
              </span>
            </div>
          </div>

          {/* Decisions Table */}
          <div>
            <h2 className="text-lg font-semibold mb-3">Decisions</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b dark:border-slate-700">
                    <th className="text-left py-2 px-2">Symbol</th>
                    <th className="text-left py-2 px-2">Action</th>
                    <th className="text-right py-2 px-2">Weight</th>
                    <th className="text-right py-2 px-2">Catalyst</th>
                    <th className="text-right py-2 px-2">Conviction</th>
                  </tr>
                </thead>
                <tbody>
                  {result.decisions.map((d) => (
                    <tr
                      key={d.symbol}
                      className="border-b dark:border-slate-800"
                    >
                      <td className="py-2 px-2 font-mono">{d.symbol}</td>
                      <td className="py-2 px-2">
                        <span
                          className={
                            d.action === "buy"
                              ? "text-green-600 font-bold"
                              : "text-slate-400"
                          }
                        >
                          {d.action.toUpperCase()}
                        </span>
                      </td>
                      <td className="py-2 px-2 text-right">
                        {(d.weight * 100).toFixed(1)}%
                      </td>
                      <td className="py-2 px-2 text-right">
                        {d.catalyst_score.toFixed(2)}
                      </td>
                      <td className="py-2 px-2 text-right">
                        {d.conviction.toFixed(1)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Position Sizes */}
          {result.position_sizes.length > 0 && (
            <div>
              <h2 className="text-lg font-semibold mb-3">
                Position Sizes (Half-Kelly)
              </h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {result.position_sizes.map((ps) => (
                  <div
                    key={ps.symbol}
                    className="p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg"
                  >
                    <div className="font-mono font-bold">{ps.symbol}</div>
                    <div className="text-sm text-slate-600 dark:text-slate-400">
                      {(ps.weight * 100).toFixed(1)}% /{" "}
                      {ps.amount.toLocaleString()} KRW
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Cross References */}
          {Object.keys(result.cross_references).length > 0 && (
            <div>
              <h2 className="text-lg font-semibold mb-3">
                Graph Cross-References
              </h2>
              <div className="space-y-2">
                {Object.entries(result.cross_references).map(([sym, refs]) =>
                  refs.length > 0 ? (
                    <div key={sym} className="text-sm">
                      <span className="font-mono font-bold">{sym}</span>
                      <span className="text-slate-400"> &larr; </span>
                      <span className="text-slate-600 dark:text-slate-400">
                        {refs.join(", ")}
                      </span>
                    </div>
                  ) : null,
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
