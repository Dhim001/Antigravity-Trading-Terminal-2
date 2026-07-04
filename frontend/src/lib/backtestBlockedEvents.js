/** Blocked-entry event helpers for backtest explainability. */

const KIND_LABELS = {
  filter: 'Analyst filter',
  parity_htf: 'HTF parity',
  parity_filter: 'Strategy filter',
  risk_gate: 'Risk gate',
  size: 'Size floor',
};

export function blockedEventKindLabel(kind) {
  return KIND_LABELS[kind] ?? kind ?? 'Blocked';
}

export function resolveBlockedEvents(results) {
  const summary = results?.summary ?? {};
  const events = summary.blocked_events ?? [];
  const total = summary.blocked_events_total ?? events.length;
  const truncated = Boolean(summary.blocked_events_truncated);
  return { events, total, truncated };
}

export function blockedEventRate(results) {
  const { total } = resolveBlockedEvents(results);
  const entries = results?.summary?.blocked_entries ?? total;
  const trades = results?.trade_count ?? results?.summary?.total_trades ?? 0;
  const attempts = entries + (results?.trades?.filter((t) => !t.is_exit).length ?? trades);
  if (!attempts) return null;
  return Math.round((total / attempts) * 1000) / 10;
}
