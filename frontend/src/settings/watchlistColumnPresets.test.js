import { describe, it, expect } from 'vitest';
import {
  BUILTIN_WATCHLIST_COLUMN_PRESETS,
  normalizeWatchlistColumnPresets,
  resolveWatchlistColumnPresetId,
  getWatchlistColumnPreset,
  buildCustomWatchlistPreset,
} from '../settings/watchlistColumnPresets';

describe('watchlistColumnPresets', () => {
  it('includes builtin trading preset', () => {
    const trading = BUILTIN_WATCHLIST_COLUMN_PRESETS.find((p) => p.id === 'trading');
    expect(trading?.columns.volume_24h).toBe(false);
    expect(trading?.columns.change_24h).toBe(true);
  });

  it('resolves preset id from columns', () => {
    expect(resolveWatchlistColumnPresetId(BUILTIN_WATCHLIST_COLUMN_PRESETS[0].columns)).toBe('full');
    expect(resolveWatchlistColumnPresetId({ change_abs: true, change_24h: true, volume_24h: false, avg_volume: false })).toBe('trading');
    expect(resolveWatchlistColumnPresetId({ change_abs: false, change_24h: false, volume_24h: true, avg_volume: true })).toBe('volume');
  });

  it('returns custom when no preset matches', () => {
    expect(resolveWatchlistColumnPresetId({ change_abs: true, change_24h: false, volume_24h: true, avg_volume: false })).toBe('custom');
  });

  it('normalizes saved custom presets', () => {
    const raw = [{ id: 'x', name: 'Mine', columns: { change_abs: false } }];
    expect(normalizeWatchlistColumnPresets(raw)).toHaveLength(1);
    expect(normalizeWatchlistColumnPresets(raw)[0].columns.change_24h).toBe(true);
  });

  it('loads preset by id including custom', () => {
    const custom = buildCustomWatchlistPreset('Test', { change_abs: false, change_24h: true, volume_24h: false, avg_volume: false });
    expect(getWatchlistColumnPreset('minimal')).toBeTruthy();
    expect(getWatchlistColumnPreset(custom.id, [custom])?.name).toBe('Test');
  });
});
