import { describe, expect, it } from 'vitest';
import {
  closedFillPnl,
  computeTradeStats,
  historyRangeCutoff,
  normalizeTradeTimestamp,
} from './tradeHistoryStats';

describe('closedFillPnl', () => {
  it('ignores opens, non-fills, and non-numeric values', () => {
    expect(closedFillPnl({ status: 'FILLED' })).toBeNull();
    expect(closedFillPnl({ status: 'PENDING', realized_pnl: 5 })).toBeNull();
    expect(closedFillPnl({ status: 'FILLED', realized_pnl: 'nope' })).toBeNull();
    expect(closedFillPnl({ status: 'FILLED', realized_pnl: 0 })).toBe(0);
    expect(closedFillPnl({ status: 'FILLED', realized_pnl: '-2.5' })).toBe(-2.5);
  });
});

describe('computeTradeStats', () => {
  it('sums every closed fill, not just the newest slice', () => {
    const trades = [
      { status: 'FILLED', realized_pnl: -40, trade_value: 100 },
      { status: 'FILLED', realized_pnl: 10, trade_value: 50 },
      { status: 'FILLED', realized_pnl: null, trade_value: 80 },
      { status: 'CANCELED', realized_pnl: 99, trade_value: 1 },
    ];
    const stats = computeTradeStats(trades);
    expect(stats.total_pnl).toBe(-30);
    expect(stats.total_exits).toBe(2);
    expect(stats.total_fills).toBe(3);
    expect(stats.wins).toBe(1);
    expect(stats.losses).toBe(1);
    expect(stats.gross_volume).toBe(150);
  });
});

describe('historyRangeCutoff', () => {
  it('uses local midnight for Today, not a rolling 24h window', () => {
    const now = new Date('2026-08-16T19:44:00+01:00').getTime();
    const cutoff = historyRangeCutoff('Today', now);
    const local = new Date(now);
    local.setHours(0, 0, 0, 0);
    expect(cutoff).toBe(local.getTime());
    expect(now - cutoff).toBeLessThan(24 * 3600 * 1000);
  });

  it('returns 0 for All', () => {
    expect(historyRangeCutoff('All')).toBe(0);
  });
});

describe('normalizeTradeTimestamp', () => {
  it('treats naive SQLite timestamps as UTC', () => {
    const ms = normalizeTradeTimestamp('2026-08-16 18:35:02');
    expect(ms).toBe(Date.parse('2026-08-16T18:35:02Z'));
  });
});
