import { describe, it, expect } from 'vitest';
import { buildBacktestAssumptionDetails } from './backtestAssumptions';
import { buildBacktestManifest } from './backtestManifest';
import { resolveBlockedEvents } from './backtestBlockedEvents';

describe('buildBacktestAssumptionDetails', () => {
  it('returns structured sections', () => {
    const { sections } = buildBacktestAssumptionDetails({
      sim_mode: 'live_aligned',
      run_id: 'abc-123',
      meta: { days: 7, timeframe: '1m', count: 500, git_revision: 'deadbeef' },
      summary: { blocked_entries: 3, parity_gate_blocks: 1, filter_rejects_total: 5 },
    });
    expect(sections.length).toBeGreaterThan(2);
    expect(sections.some((s) => s.id === 'audit')).toBe(true);
  });
});

describe('buildBacktestManifest', () => {
  it('includes fingerprint and data slice', () => {
    const manifest = buildBacktestManifest({
      results: {
        run_id: 'run-1',
        sim_mode: 'live_aligned',
        meta: {
          symbol: 'AAPL',
          strategy: 'CHART_AGENT',
          days: 7,
          timeframe: '1m',
          oldest: 1700000000,
          newest: 1700600000,
          count: 1000,
          git_revision: 'abc1234',
        },
        summary: { total_pnl: 120, blocked_events_total: 4 },
      },
      symbol: 'AAPL',
      strategy: 'CHART_AGENT',
      days: 7,
      timeframe: '1m',
      config: { allocation: 1000 },
    });
    expect(manifest.schema).toBe('backtest-manifest/v1');
    expect(manifest.run_id).toBe('run-1');
    expect(manifest.git_revision).toBe('abc1234');
    expect(manifest.data_slice.bar_count).toBe(1000);
    expect(manifest.config_fingerprint).toContain('AAPL');
  });
});

describe('resolveBlockedEvents', () => {
  it('reads capped blocked log from summary', () => {
    const { events, total, truncated } = resolveBlockedEvents({
      summary: {
        blocked_events: [{ time: 1, kind: 'filter', reason: 'trend' }],
        blocked_events_total: 10,
        blocked_events_truncated: true,
      },
    });
    expect(events).toHaveLength(1);
    expect(total).toBe(10);
    expect(truncated).toBe(true);
  });
});
