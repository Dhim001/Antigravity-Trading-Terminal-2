/** Watchlist column visibility — symbol/price always shown. */

/** @typedef {'change_abs' | 'change_24h' | 'volume_24h' | 'avg_volume'} WatchlistOptionalColumn */

/** @type {Record<WatchlistOptionalColumn, boolean>} */
export const DEFAULT_WATCHLIST_COLUMNS = {
  change_abs: true,
  change_24h: true,
  volume_24h: true,
  avg_volume: true,
};

/** @param {unknown} raw */
export function normalizeWatchlistColumns(raw) {
  const out = { ...DEFAULT_WATCHLIST_COLUMNS };
  if (!raw || typeof raw !== 'object') return out;
  for (const key of Object.keys(out)) {
    if (typeof raw[key] === 'boolean') out[key] = raw[key];
  }
  if (!Object.values(out).some(Boolean)) {
    out.change_24h = true;
  }
  return out;
}

/** @param {unknown} a @param {unknown} b */
export function watchlistColumnsEqual(a, b) {
  const left = normalizeWatchlistColumns(a);
  const right = normalizeWatchlistColumns(b);
  return Object.keys(DEFAULT_WATCHLIST_COLUMNS).every((key) => left[key] === right[key]);
}

/** @param {Record<WatchlistOptionalColumn, boolean>} cols @param {string} id */
export function isWatchlistColumnVisible(cols, id) {
  if (id === 'symbol' || id === 'price') return true;
  return cols[id] !== false;
}

/**
 * @param {'LIVE_MASSIVE' | string | null | undefined} terminalMode
 * @returns {Array<{ id: string, field: string | null, label: string, col: string, align: 'left' | 'right', title?: string, optional?: boolean }>}
 */
export function watchlistColumnDefs(terminalMode) {
  const chgAbs = terminalMode === 'LIVE_MASSIVE' ? 'Chg' : '24h Chg';
  const chgPct = terminalMode === 'LIVE_MASSIVE' ? 'Chg%' : '24h%';
  const rolling = terminalMode === 'LIVE_MASSIVE';

  return [
    { id: 'symbol', field: 'symbol', label: 'Symbol', col: 'watchlist-col-symbol', align: 'left' },
    { id: 'price', field: 'price', label: 'Price', col: 'watchlist-col-price', align: 'right' },
    {
      id: 'change_abs',
      field: 'change_abs',
      label: chgAbs,
      col: 'watchlist-col-chg',
      align: 'right',
      optional: true,
      title: rolling ? 'Rolling 24h change ($)' : '24h change ($)',
    },
    {
      id: 'change_24h',
      field: 'change_24h',
      label: chgPct,
      col: 'watchlist-col-chgpct',
      align: 'right',
      optional: true,
      title: rolling ? 'Rolling 24h change (%)' : '24h change (%)',
    },
    {
      id: 'volume_24h',
      field: 'volume_24h',
      label: 'Vol',
      col: 'watchlist-col-vol',
      align: 'right',
      optional: true,
      title: 'Rolling 24h total volume',
    },
    {
      id: 'avg_volume',
      field: 'avg_volume',
      label: 'Avg 1m',
      col: 'watchlist-col-avgvol',
      align: 'right',
      optional: true,
      title: 'Average volume per 1-minute bar over the rolling 24h window (not daily ADV)',
    },
  ];
}

/** @param {Record<WatchlistOptionalColumn, boolean>} cols @param {ReturnType<typeof watchlistColumnDefs>} defs */
export function visibleWatchlistColumns(cols, defs) {
  return defs.filter((d) => isWatchlistColumnVisible(cols, d.id));
}

/** DOM attrs for container-query gating — user-enabled columns are never force-hidden. */
export function watchlistColumnPrefAttrs(cols) {
  const n = normalizeWatchlistColumns(cols);
  const visibleOptional = Object.keys(DEFAULT_WATCHLIST_COLUMNS).filter((k) => n[k] !== false).length;
  return {
    'data-pref-change-abs': n.change_abs !== false ? '1' : undefined,
    'data-pref-change-24h': n.change_24h !== false ? '1' : undefined,
    'data-pref-vol': n.volume_24h !== false ? '1' : undefined,
    'data-pref-avg': n.avg_volume !== false ? '1' : undefined,
    'data-col-count': String(2 + visibleOptional),
  };
}
