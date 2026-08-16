import { describe, it, expect } from 'vitest';
import {
  evaluateDeployGate,
  extractBacktestSimMode,
  extractBacktestDirectionMode,
  normalizeDirectionMode,
  backtestResultsMatchTarget,
  buildDeployPayload,
} from './deployGate';

const baseResults = {
  run_id: 'run-1',
  sim_mode: 'live_aligned',
  total_pnl: 100,
  trade_count: 5,
  summary: { total_trades: 5, total_pnl: 100 },
  meta: {
    symbol: 'AAPL',
    strategy: 'ML_SIGNAL_BOOST',
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

  it('blocks when results symbol/strategy do not match deploy target', () => {
    const gate = evaluateDeployGate({
      results: baseResults,
      symbol: 'AMZN',
      strategy: 'ML_SIGNAL_BOOST',
      config: { direction_mode: 'LONG_ONLY' },
    });
    expect(gate.blocking).toBe(true);
    expect(gate.checks.some((c) => c.id === 'result_identity' && !c.ok)).toBe(true);
  });
});

describe('backtestResultsMatchTarget', () => {
  it('rejects stale symbol/strategy pairs', () => {
    expect(backtestResultsMatchTarget(baseResults, {
      symbol: 'AMZN',
      strategy: 'ML_SIGNAL_BOOST',
    })).toBe(false);
    expect(backtestResultsMatchTarget(baseResults, {
      symbol: 'AAPL',
      strategy: 'LSTM_DIRECTION',
    })).toBe(false);
  });

  it('accepts matching identity', () => {
    expect(backtestResultsMatchTarget(baseResults, {
      symbol: 'AAPL',
      strategy: 'ML_SIGNAL_BOOST',
    })).toBe(true);
  });
});

describe('RL payoff deploy gate', () => {
  it('blocks inverted expectancy without costs', () => {
    const gate = evaluateDeployGate({
      results: {
        run_id: 'rl-1',
        meta: { symbol: 'ADAUSDT', strategy: 'RL_PPO_AGENT', config: { fee_bps: 0, slippage_bps: 0 } },
        summary: { avg_win: 6, avg_loss: -27, profit_factor: 0.11 },
        walk_forward: {
          aggregate: {
            mean_oos_avg_win: 6,
            mean_oos_avg_loss: -27,
            mean_oos_profit_factor: 0.11,
          },
          final_holdout: { total_pnl: 10, trade_count: 4, passed: true },
        },
      },
      symbol: 'ADAUSDT',
      strategy: 'RL_PPO_AGENT',
      config: { direction_mode: 'BOTH', fee_bps: 0, slippage_bps: 0 },
    });
    expect(gate.blocking).toBe(true);
    expect(gate.checks.some((c) => c.id === 'rl_payoff' && !c.ok)).toBe(true);
    expect(gate.checks.some((c) => c.id === 'rl_costs' && !c.ok)).toBe(true);
  });

  it('passes costed payoff and stamps paper-first payload', () => {
    const gate = evaluateDeployGate({
      results: {
        run_id: 'rl-2',
        meta: {
          symbol: 'ADAUSDT',
          strategy: 'RL_PPO_AGENT',
          config: { fee_bps: 10, slippage_bps: 5, model_version: 'v1' },
        },
        summary: { avg_win: 12, avg_loss: -8, profit_factor: 1.5, fee_bps: 10 },
        walk_forward: {
          aggregate: {
            mean_oos_avg_win: 12,
            mean_oos_avg_loss: -8,
            mean_oos_profit_factor: 1.5,
          },
          final_holdout: { total_pnl: 20, trade_count: 5, passed: true, avg_win: 12, avg_loss: -8, profit_factor: 1.5 },
        },
      },
      symbol: 'ADAUSDT',
      strategy: 'RL_PPO_AGENT',
      config: {
        direction_mode: 'BOTH',
        fee_bps: 10,
        slippage_bps: 5,
        model_version: 'v1',
      },
    });
    expect(gate.checks.some((c) => c.id === 'rl_payoff' && c.ok)).toBe(true);
    expect(gate.checks.some((c) => c.id === 'rl_paper_first')).toBe(true);
  });
});

describe('buildDeployPayload', () => {
  it('does not inherit stale config.backtest_run_id', () => {
    const payload = buildDeployPayload({
      strategy: 'ML_SIGNAL_BOOST',
      symbol: 'AMZN',
      timeframe: '1m',
      allocation: 2000,
      executionMode: 'BAR_CLOSE',
      config: { backtest_run_id: 'stale-btc-run', direction_mode: 'BOTH' },
      results: { ...baseResults, run_id: undefined, meta: { ...baseResults.meta, symbol: 'AMZN' } },
      days: 'holdout',
    });
    expect(payload.config.backtest_run_id).toBeNull();
  });

  it('stamps results.run_id when present', () => {
    const payload = buildDeployPayload({
      strategy: 'ML_SIGNAL_BOOST',
      symbol: 'AAPL',
      timeframe: '1m',
      allocation: 2000,
      executionMode: 'BAR_CLOSE',
      config: { backtest_run_id: 'stale-run' },
      results: baseResults,
      days: '14',
    });
    expect(payload.config.backtest_run_id).toBe('run-1');
  });

  it('stamps RL ATR risk instead of 2% trail', () => {
    const payload = buildDeployPayload({
      strategy: 'RL_PPO_AGENT',
      symbol: 'ADAUSDT',
      timeframe: '5m',
      allocation: 3000,
      executionMode: 'BAR_CLOSE',
      config: { min_confidence: 0.28, model_version: 'v1' },
      results: {
        run_id: 'rl-3',
        meta: { symbol: 'ADAUSDT', strategy: 'RL_PPO_AGENT' },
      },
      days: '14',
    });
    expect(payload.config.trailing_stop_percent).toBeUndefined();
    expect(payload.config.risk_per_trade_usd).toBe(20);
    expect(payload.config.atr_stop_mult).toBe(1.5);
    expect(payload.config.paper_first).toBe(true);
    expect(payload.config.tp_mode).toBe('strategy');
  });
});
