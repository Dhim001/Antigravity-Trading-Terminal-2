import { describe, expect, it } from 'vitest';
import {
  buildCorrelationHeatmapCells,
  buildPortfolioInvalidateKey,
  calendarHeatmapData,
  calendarPnlRange,
  correlationStrengthLabel,
  fmtUsd,
  nextSortState,
  sortBreakdownRows,
} from './helpers';

describe('analytics helpers', () => {
  it('fmtUsd keeps negative sign', () => {
    expect(fmtUsd(-1234.5)).toBe('-$1,234.50');
    expect(fmtUsd(1234.5)).toBe('+$1,234.50');
    expect(fmtUsd(0)).toBe('$0.00');
  });

  it('buildPortfolioInvalidateKey changes when trades or symbols update', () => {
    const base = buildPortfolioInvalidateKey({
      tradeHistory: [],
      symbolsList: ['AAPL'],
    });
    const afterTrade = buildPortfolioInvalidateKey({
      tradeHistory: [{ timestamp: 1, realized_pnl: 5 }],
      tradeStats: { total_pnl: 5, total_sells: 1 },
      symbolsList: ['AAPL'],
    });
    const afterSymbol = buildPortfolioInvalidateKey({
      tradeHistory: [],
      symbolsList: ['AAPL', 'TSLA'],
    });
    expect(afterTrade).not.toEqual(base);
    expect(afterSymbol).not.toEqual(base);
  });

  it('calendarHeatmapData maps days to ECharts cells', () => {
    expect(calendarHeatmapData([{ date: '2026-01-01', pnl: 10 }])).toEqual([
      ['2026-01-01', 10],
    ]);
  });

  it('calendarPnlRange returns symmetric max abs', () => {
    expect(calendarPnlRange([{ pnl: -5 }, { pnl: 12 }])).toBe(12);
    expect(calendarPnlRange([])).toBe(1);
  });

  it('sortBreakdownRows sorts by field', () => {
    const rows = [
      { key: 'a', total_pnl: 1 },
      { key: 'b', total_pnl: 10 },
    ];
    const sorted = sortBreakdownRows(rows, { field: 'total_pnl', dir: 'desc' });
    expect(sorted[0].key).toBe('b');
  });

  it('nextSortState cycles asc/desc/clear', () => {
    expect(nextSortState({ field: null, dir: null }, 'x')).toEqual({ field: 'x', dir: 'desc' });
    expect(nextSortState({ field: 'x', dir: 'desc' }, 'x')).toEqual({ field: 'x', dir: 'asc' });
    expect(nextSortState({ field: 'x', dir: 'asc' }, 'x')).toEqual({ field: null, dir: null });
  });

  it('buildCorrelationHeatmapCells keeps lower triangle only', () => {
    const matrix = [[1, 0.5], [0.5, 1]];
    expect(buildCorrelationHeatmapCells(matrix)).toEqual([
      [0, 0, 1],
      [0, 1, 0.5],
      [1, 1, 1],
    ]);
  });

  it('correlationStrengthLabel describes magnitude and sign', () => {
    expect(correlationStrengthLabel(0.85)).toBe('Strong positive');
    expect(correlationStrengthLabel(-0.55)).toBe('Moderate negative');
    expect(correlationStrengthLabel(0.05)).toBe('Negligible');
  });
});
