/** Closed-fill Realized P&L helpers for the History blotter. */

export const MAX_TRADE_HISTORY = 2000;

export function closedFillPnl(trade) {
  if (!trade || trade.status !== 'FILLED') return null;
  if (trade.realized_pnl == null || trade.realized_pnl === '') return null;
  const pnl = Number(trade.realized_pnl);
  return Number.isFinite(pnl) ? pnl : null;
}

export function computeTradeStats(trades) {
  let wins = 0;
  let losses = 0;
  let total_exits = 0;
  let total_pnl = 0;
  let totalWinPnl = 0;
  let totalLossPnl = 0;
  let best_trade = 0;
  let worst_trade = 0;
  let total_fills = 0;
  let gross_volume = 0;

  for (const trade of trades) {
    if (trade.status === 'FILLED') total_fills += 1;
    const pnl = closedFillPnl(trade);
    if (pnl == null) continue;
    total_exits += 1;
    total_pnl += pnl;
    gross_volume += Number(trade.trade_value) || 0;
    if (total_exits === 1 || pnl > best_trade) best_trade = pnl;
    if (total_exits === 1 || pnl < worst_trade) worst_trade = pnl;
    if (pnl > 0) {
      wins += 1;
      totalWinPnl += pnl;
    } else if (pnl < 0) {
      losses += 1;
      totalLossPnl += pnl;
    }
  }

  const win_rate = total_exits > 0 ? (wins / total_exits) * 100 : 0;
  const profit_factor = Math.abs(totalLossPnl) > 0
    ? totalWinPnl / Math.abs(totalLossPnl)
    : (totalWinPnl > 0 ? 99.9 : 0);
  return {
    total_pnl,
    wins,
    losses,
    total_exits,
    win_rate,
    profit_factor,
    best_trade,
    worst_trade,
    avg_win: wins > 0 ? totalWinPnl / wins : 0,
    avg_loss: losses > 0 ? totalLossPnl / losses : 0,
    total_fills,
    gross_volume,
  };
}

/** Local calendar start for "Today"; rolling windows for 7D/30D. */
export function historyRangeCutoff(label, now = Date.now()) {
  if (!label || label === 'All') return 0;
  if (label === 'Today') {
    const d = new Date(now);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  }
  const days = { '7D': 7, '30D': 30 }[label];
  if (!days) return 0;
  return now - days * 86400000;
}

export function normalizeTradeTimestamp(ts, fallback = Date.now()) {
  if (typeof ts === 'number') {
    return ts < 10000000000 ? ts * 1000 : ts;
  }
  if (typeof ts === 'string' && ts) {
    const raw = ts.trim();
    const normalized = /Z|[+-]\d{2}:?\d{2}$/.test(raw)
      ? raw
      : `${raw.replace(' ', 'T')}Z`;
    const parsed = new Date(normalized).getTime();
    return Number.isNaN(parsed) ? fallback : parsed;
  }
  return fallback;
}
