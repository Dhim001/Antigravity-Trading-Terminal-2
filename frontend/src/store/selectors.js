/** Narrow store selectors — return primitives/small tuples to limit re-renders on ticks. */

export function selectActiveSymbolTick(state) {
  const tick = state.tickerData[state.activeSymbol];
  return {
    symbol: state.activeSymbol,
    price: tick?.price ?? null,
    change: tick?.change_24h ?? null,
  };
}

export function selectCashTotal(state) {
  let total = 0;
  for (const row of Object.values(state.balances || {})) {
    total += (row.balance ?? 0) - (row.locked ?? 0);
  }
  return Math.round(total);
}

export function selectInvestedTotal(state) {
  let total = 0;
  for (const [sym, pos] of Object.entries(state.positions || {})) {
    const size = pos?.size ?? 0;
    if (!size) continue;
    const px = state.tickerData[sym]?.price ?? pos.avg_price ?? 0;
    total += size * px;
  }
  return Math.round(total);
}

export function selectDayPnlTotal(state) {
  let dayPnl = 0;
  for (const pos of Object.values(state.positions || {})) {
    dayPnl += pos?.unrealized_pnl ?? pos?.pnl ?? 0;
  }
  return Math.round(dayPnl * 100) / 100;
}

export function selectRunningBotCount(state) {
  return (state.activeBots || []).filter((b) => b.status === 'RUNNING').length;
}

export function selectPositionStats(state) {
  let totalPnl = 0;
  let longCount = 0;
  let shortCount = 0;
  for (const [sym, pos] of Object.entries(state.positions || {})) {
    const mark = state.tickerData[sym]?.price ?? pos.avg_price;
    totalPnl += pos.size * (mark - pos.avg_price);
    if (pos.size >= 0) longCount += 1;
    else shortCount += 1;
  }
  return {
    totalPnl: Math.round(totalPnl * 100) / 100,
    longCount,
    shortCount,
  };
}

export function selectStripItemState(state, sym) {
  const info = state.tickerData[sym];
  return {
    price: info?.price ?? null,
    change: info?.change_24h ?? null,
    isActive: state.activeSymbol === sym,
  };
}
