/**
 * Enrich backtest reasoning rows with trade-log fields and run context labels.
 */

export function fmtReasoningTime(sec) {
  if (sec == null || sec === '') return '—';
  const n = Number(sec);
  if (!Number.isFinite(n) || n <= 0) return '—';
  const ms = n > 1e11 ? n : n * 1000;
  try {
    return new Date(ms).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return String(sec);
  }
}

/** Join reasoning rows to trade log — prefers trade_index, falls back to side+price match. */
export function enrichReasoningTrades(reasoning, tradeLog = []) {
  if (!reasoning?.trades?.length) return [];

  const entries = tradeLog.filter((t) => !t.is_exit);

  function findSource(row) {
    const indexed = tradeLog[row.trade_index];
    if (indexed && !indexed.is_exit && indexed.side === row.side) {
      return indexed;
    }
    const price = row.price != null ? Number(row.price) : null;
    return entries.find((t) => {
      if (t.side !== row.side) return false;
      if (price == null || Number.isNaN(price)) return true;
      return Math.abs(Number(t.price) - price) < 1e-4;
    });
  }

  return reasoning.trades.map((row, i) => {
    const src = findSource(row);
    const barTime = row.bar_time ?? row.time ?? src?.time ?? src?.bar_time ?? null;
    const insight = row.insight_snapshot ?? src?.insight_snapshot ?? null;
    return {
      ...row,
      _rowKey: `${row.trade_index}-${barTime ?? 't'}-${row.side ?? 's'}-${i}`,
      bar_time: barTime,
      time: barTime,
      reason: row.reason ?? src?.reason ?? 'ENTRY',
      quantity: row.quantity ?? src?.quantity,
      price: row.price ?? src?.price,
      insight_snapshot: insight,
    };
  });
}

export function resolveReasoningRunContext(results, reasoning) {
  const kind = reasoning?.run_kind
    ?? results?.meta?.reasoning_run_kind
    ?? (results?.walk_forward || results?.meta?.walk_forward
      ? 'walk_forward'
      : results?.sweep
        ? 'sweep'
        : 'single');

  const labels = {
    single: 'Standard backtest',
    sweep: 'Sweep — best config only',
    walk_forward: 'Walk-forward — OOS window',
  };

  const scope = reasoning?.scope
    ?? (kind === 'walk_forward'
      ? `Out-of-sample validation (train ${results?.meta?.train_pct ?? results?.walk_forward?.train_pct ?? 70}%)`
      : kind === 'sweep'
        ? `Winning parameter set${results?.sweep?.configs_tested ? ` · ${results.sweep.configs_tested} configs tested` : ''}`
        : 'All entry fills from this simulation');

  return {
    kind,
    title: reasoning?.run_kind_label ?? labels[kind] ?? kind,
    scope,
  };
}
