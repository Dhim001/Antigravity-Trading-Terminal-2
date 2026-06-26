import { describe, it, expect } from 'vitest';
import {
  DEFAULT_WATCHLIST_COLUMNS,
  normalizeWatchlistColumns,
  watchlistColumnsEqual,
  isWatchlistColumnVisible,
  visibleWatchlistColumns,
  watchlistColumnDefs,
  watchlistColumnPrefAttrs,
} from '../settings/watchlistColumns';

describe('watchlistColumns', () => {
  it('defaults all optional columns on', () => {
    expect(normalizeWatchlistColumns(undefined)).toEqual(DEFAULT_WATCHLIST_COLUMNS);
  });

  it('keeps at least one optional column visible', () => {
    const cols = normalizeWatchlistColumns({
      change_abs: false,
      change_24h: false,
      volume_24h: false,
      avg_volume: false,
    });
    expect(cols.change_24h).toBe(true);
  });

  it('filters column defs by visibility', () => {
    const defs = watchlistColumnDefs('SIMULATED');
    const visible = visibleWatchlistColumns(
      { change_abs: false, change_24h: true, volume_24h: false, avg_volume: true },
      defs,
    );
    expect(visible.map((c) => c.id)).toEqual(['symbol', 'price', 'change_24h', 'avg_volume']);
    expect(isWatchlistColumnVisible({}, 'symbol')).toBe(true);
  });

  it('compare normalized column settings', () => {
    expect(watchlistColumnsEqual(undefined, DEFAULT_WATCHLIST_COLUMNS)).toBe(true);
    expect(watchlistColumnsEqual({ change_abs: false }, { change_abs: false })).toBe(true);
    expect(watchlistColumnsEqual({ change_abs: true }, { change_abs: false })).toBe(false);
  });

  it('emits data-pref attrs for enabled optional columns', () => {
    const attrs = watchlistColumnPrefAttrs(DEFAULT_WATCHLIST_COLUMNS);
    expect(attrs['data-pref-vol']).toBe('1');
    expect(attrs['data-col-count']).toBe('6');
    const minimal = watchlistColumnPrefAttrs({ change_abs: false, change_24h: true, volume_24h: false, avg_volume: false });
    expect(minimal['data-pref-vol']).toBeUndefined();
    expect(minimal['data-col-count']).toBe('3');
  });
});
