import {
  DEFAULT_WATCHLIST_COLUMNS,
  normalizeWatchlistColumns,
  watchlistColumnsEqual,
} from './watchlistColumns';

/** @typedef {{ id: string, name: string, description?: string, columns: Record<string, boolean> }} WatchlistColumnPreset */

/** @type {WatchlistColumnPreset[]} */
export const BUILTIN_WATCHLIST_COLUMN_PRESETS = [
  {
    id: 'full',
    name: 'Full',
    description: 'Price, change ($/%), volume, avg 1m',
    columns: { ...DEFAULT_WATCHLIST_COLUMNS },
  },
  {
    id: 'trading',
    name: 'Trading',
    description: 'Price and change columns only',
    columns: {
      change_abs: true,
      change_24h: true,
      volume_24h: false,
      avg_volume: false,
    },
  },
  {
    id: 'volume',
    name: 'Volume',
    description: 'Price and volume columns',
    columns: {
      change_abs: false,
      change_24h: false,
      volume_24h: true,
      avg_volume: true,
    },
  },
  {
    id: 'minimal',
    name: 'Minimal',
    description: 'Price and % change only',
    columns: {
      change_abs: false,
      change_24h: true,
      volume_24h: false,
      avg_volume: false,
    },
  },
];

/** @param {unknown} raw */
export function normalizeWatchlistColumnPresets(raw) {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((p) => p && typeof p === 'object' && typeof p.id === 'string' && typeof p.name === 'string')
    .map((p) => ({
      id: p.id,
      name: p.name,
      description: typeof p.description === 'string' ? p.description : undefined,
      columns: normalizeWatchlistColumns(p.columns),
    }))
    .slice(0, 8);
}

/**
 * @param {Record<string, boolean>} columns
 * @param {WatchlistColumnPreset[]} customPresets
 */
export function resolveWatchlistColumnPresetId(columns, customPresets = []) {
  const normalized = normalizeWatchlistColumns(columns);
  const all = [...BUILTIN_WATCHLIST_COLUMN_PRESETS, ...customPresets];
  const match = all.find((p) => watchlistColumnsEqual(p.columns, normalized));
  return match?.id ?? 'custom';
}

/**
 * @param {string} presetId
 * @param {WatchlistColumnPreset[]} customPresets
 */
export function getWatchlistColumnPreset(presetId, customPresets = []) {
  if (!presetId || presetId === 'custom') return null;
  return (
    BUILTIN_WATCHLIST_COLUMN_PRESETS.find((p) => p.id === presetId)
    ?? customPresets.find((p) => p.id === presetId)
    ?? null
  );
}

/** @param {string} name @param {Record<string, boolean>} columns */
export function buildCustomWatchlistPreset(name, columns) {
  return {
    id: `wl-preset-${Date.now()}`,
    name: name.trim() || 'Custom',
    columns: normalizeWatchlistColumns(columns),
  };
}
