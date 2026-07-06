import { describe, it, expect } from 'vitest';
import {
  evaluateDeployGate,
  extractBacktestSimMode,
  extractBacktestDirectionMode,
  normalizeDirectionMode,
} from './deployGate';

const baseResults = {
  run_id: 'run-1',
  sim_mode: 'live_aligned',
  total_pnl: 100,
  trade_count: 5,
  summary: { total_trades: 5, total_pnl: 100 },
  meta: {
    config: {
      direction_mode: 'LONG_ONLY',
      sim_mode: 'live_aligned',
    },
  },
};

describe('normalizeDirectionMode', () => {
  it('defaults to LONG_ONLY', () => {
    expect(normalizeDirectionMode()).toBe('LONG_ONLY');
    expect(normalizeDirectionMode('')).toBe('LONG_ONLY');
  });

  it('normalizes BOTH and SHORT_ONLY', () => {
    expect(normalizeDirectionMode('both')).toBe('BOTH');
    expect(normalizeDirectionMode('SHORT_ONLY')).toBe('SHORT_ONLY');
  });
});

describe('extractBacktestSimMode', () => {
  it('reads top-level sim_mode', () => {
    expect(extractBacktestSimMode({ sim_mode: 'research' })).toBe('research');
  });

  it('falls back to meta.config', () => {
    expect(extractBacktestSimMode({ meta: { config: { sim_mode: 'research' } } })).toBe('research');
  });
});

describe('extractBacktestDirectionMode', () => {
  it('prefers backtestConfig override', () => {
    expect(extractBacktestDirectionMode(baseResults, { direction_mode: 'BOTH' })).toBe('BOTH');
  });

  it('reads meta.config', () => {
    expect(extractBacktestDirectionMode({
      meta: { config: { direction_mode: 'SHORT_ONLY' } },
    })).toBe('SHORT_ONLY');
  });
});

describe('evaluateDeployGate', () => {
  it('warns on research sim_mode', () => {
    const gate = evaluateDeployGate({
      results: { ...baseResults, sim_mode: 'research' },
      config: { direction_mode: 'LONG_ONLY' },
      backtestConfig: { direction_mode: 'LONG_ONLY', sim_mode: 'research' },
    });
    expect(gate.checks.some((c) => c.id === 'research_sim_mode' && !c.ok)).toBe(true);
  });

  it('warns when deploy direction differs from backtest', () => {
    const gate = evaluateDeployGate({
      results: {
        ...baseResults,
        meta: { config: { direction_mode: 'BOTH', sim_mode: 'live_aligned' } },
      },
      config: { direction_mode: 'LONG_ONLY' },
      backtestConfig: { direction_mode: 'BOTH', sim_mode: 'live_aligned' },
    });
    expect(gate.checks.some((c) => c.id === 'direction_mode_mismatch' && !c.ok)).toBe(true);
  });

  it('passes when direction matches in live-aligned mode', () => {
    const gate = evaluateDeployGate({
      results: baseResults,
      config: { direction_mode: 'LONG_ONLY' },
      backtestConfig: { direction_mode: 'LONG_ONLY', sim_mode: 'live_aligned' },
    });
    expect(gate.checks.some((c) => c.id === 'direction_mode_mismatch')).toBe(false);
    expect(gate.checks.some((c) => c.id === 'research_sim_mode')).toBe(false);
  });
});
