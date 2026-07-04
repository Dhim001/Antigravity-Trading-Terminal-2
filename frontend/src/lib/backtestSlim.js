/** Memory-bounded backtest payload shaping for store + wire. */

const MAX_EQUITY_POINTS = 2000;
const MAX_DOCK_EQUITY_POINTS = 400;
const MAX_REASONING_TRADES = 50;
const MAX_DOCK_TRADES = 10;
const MAX_OVERLAY_TRADES = 200;

function downsampleSeries(series, maxPoints) {
  if (!Array.isArray(series) || series.length <= maxPoints) return series ?? [];
  const step = Math.ceil(series.length / maxPoints);
  return series.filter((_, i) => i % step === 0 || i === series.length - 1);
}

/** Trim large arrays before storing backtest results (shared by WS + job poll). */
export function trimBacktestPayload(results) {
  if (!results || typeof results !== 'object') return results;
  const out = { ...results };

  if (Array.isArray(out.equity_curve) && out.equity_curve.length > MAX_EQUITY_POINTS) {
    out.equity_curve = downsampleSeries(out.equity_curve, MAX_EQUITY_POINTS);
  }

  if (out.reasoning?.trades && Array.isArray(out.reasoning.trades)) {
    out.reasoning = {
      ...out.reasoning,
      trades: out.reasoning.trades.slice(0, MAX_REASONING_TRADES),
    };
  }

  if (Array.isArray(out.sweep?.results) && out.sweep.results.length > 48) {
    out.sweep = {
      ...out.sweep,
      results: out.sweep.results.slice(0, 48),
      results_truncated: out.sweep.results.length,
    };
  }

  return out;
}

/** Minimal dock preview when Lab holds the full report (avoids duplicate heavy trees). */
export function slimBacktestForDock(results) {
  if (!results) return null;
  const trades = Array.isArray(results.trades) ? results.trades : [];
  return {
    run_id: results.run_id,
    total_pnl: results.total_pnl,
    trade_count: results.trade_count ?? trades.length,
    win_rate: results.win_rate,
    summary: results.summary,
    meta: results.meta,
    portfolio: results.portfolio,
    symbols_tested: results.symbols_tested,
    equity_curve: downsampleSeries(results.equity_curve, MAX_DOCK_EQUITY_POINTS),
    trades: trades.slice(0, MAX_DOCK_TRADES),
    trades_total: results.trades_total ?? trades.length,
  };
}

/** Overlay payload — chart markers only, no full result duplication. */
export function buildBacktestOverlay(results) {
  if (!results?.meta?.symbol || !results?.run_id) return null;
  const trades = Array.isArray(results.trades) ? results.trades : [];
  return {
    runId: results.run_id,
    symbol: results.meta.symbol,
    meta: results.meta,
    trades: trades.slice(0, MAX_OVERLAY_TRADES),
    tradesTotal: results.trades_total ?? trades.length,
    equityCurve: downsampleSeries(results.equity_curve, MAX_EQUITY_POINTS),
    visible: false,
  };
}
