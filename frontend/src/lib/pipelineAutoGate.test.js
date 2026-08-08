import { describe, it, expect, vi } from 'vitest';
import { evaluateAndMaybeDeploy, canPaperAutoDeploy } from './pipelineAutoGate';

/** Minimal passing gate results (non-blocking trade_count / pnl path). */
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

const baseConfig = { direction_mode: 'LONG_ONLY' };

function makeCallbacks() {
  return {
    onGatePassed: vi.fn(),
    onGateFailed: vi.fn(),
    onApprovalNeeded: vi.fn(),
    onAutoDeploy: vi.fn(),
  };
}

describe('evaluateAndMaybeDeploy', () => {
  it('blocks when gate fails and calls onGateFailed', () => {
    const cbs = makeCallbacks();
    const out = evaluateAndMaybeDeploy({
      backtestResults: {
        ...baseResults,
        total_pnl: -10,
        trade_count: 0,
        summary: { total_trades: 0, total_pnl: -10 },
        walk_forward: {
          out_of_sample: { total_pnl: -10, trade_count: 0 },
          aggregate: { fold_count: 1 },
        },
      },
      config: baseConfig,
      autoDeployMode: 'full_auto',
      symbol: 'AAPL',
      strategy: 'ML_SIGNAL_BOOST',
      ...cbs,
    });
    expect(out.deployed).toBe(false);
    expect(out.gateResult.blocking).toBe(true);
    expect(cbs.onGateFailed).toHaveBeenCalledOnce();
    expect(cbs.onAutoDeploy).not.toHaveBeenCalled();
    expect(cbs.onGatePassed).not.toHaveBeenCalled();
  });

  it('blocks when results belong to a different symbol/strategy', () => {
    const cbs = makeCallbacks();
    const out = evaluateAndMaybeDeploy({
      backtestResults: baseResults,
      config: baseConfig,
      autoDeployMode: 'full_auto',
      symbol: 'AMZN',
      strategy: 'ML_SIGNAL_BOOST',
      terminalMode: 'SIMULATED',
      executionMode: 'paper',
      ...cbs,
    });
    expect(out.deployed).toBe(false);
    expect(out.gateResult.blocking).toBe(true);
    expect(out.gateResult.block_reason).toMatch(/do not match/i);
    expect(cbs.onGateFailed).toHaveBeenCalledOnce();
    expect(cbs.onAutoDeploy).not.toHaveBeenCalled();
  });

  it('paper mode auto-deploys only in paper execution', () => {
    const cbs = makeCallbacks();
    const paper = evaluateAndMaybeDeploy({
      backtestResults: baseResults,
      config: baseConfig,
      autoDeployMode: 'paper',
      terminalMode: 'SIMULATED',
      executionMode: 'paper',
      symbol: 'AAPL',
      strategy: 'ML_SIGNAL_BOOST',
      ...cbs,
    });
    expect(paper.deployed).toBe(true);
    expect(paper.reason).toMatch(/paper/i);
    expect(cbs.onGatePassed).toHaveBeenCalledOnce();
    expect(cbs.onAutoDeploy).toHaveBeenCalledOnce();

    const cbs2 = makeCallbacks();
    const live = evaluateAndMaybeDeploy({
      backtestResults: baseResults,
      config: baseConfig,
      autoDeployMode: 'paper',
      terminalMode: 'LIVE_ALPACA',
      executionMode: 'broker',
      symbol: 'AAPL',
      strategy: 'ML_SIGNAL_BOOST',
      ...cbs2,
    });
    expect(live.deployed).toBe(false);
    expect(live.reason).toMatch(/live mode/i);
    expect(cbs2.onAutoDeploy).not.toHaveBeenCalled();
    expect(cbs2.onGatePassed).toHaveBeenCalledOnce();
  });

  it('approval mode calls onApprovalNeeded and does not deploy', () => {
    const cbs = makeCallbacks();
    const out = evaluateAndMaybeDeploy({
      backtestResults: baseResults,
      config: baseConfig,
      autoDeployMode: 'approval',
      terminalMode: 'SIMULATED',
      executionMode: 'paper',
      symbol: 'AAPL',
      strategy: 'ML_SIGNAL_BOOST',
      ...cbs,
    });
    expect(out.deployed).toBe(false);
    expect(out.reason).toMatch(/approval/i);
    expect(cbs.onApprovalNeeded).toHaveBeenCalledOnce();
    expect(cbs.onAutoDeploy).not.toHaveBeenCalled();
  });

  it('full_auto deploys regardless of execution mode', () => {
    const cbs = makeCallbacks();
    const out = evaluateAndMaybeDeploy({
      backtestResults: baseResults,
      config: baseConfig,
      autoDeployMode: 'full_auto',
      terminalMode: 'LIVE_ALPACA',
      executionMode: 'broker',
      symbol: 'AAPL',
      strategy: 'ML_SIGNAL_BOOST',
      ...cbs,
    });
    expect(out.deployed).toBe(true);
    expect(out.reason).toMatch(/full auto/i);
    expect(cbs.onAutoDeploy).toHaveBeenCalledOnce();
  });

  it('returns unknown mode without deploying', () => {
    const cbs = makeCallbacks();
    const out = evaluateAndMaybeDeploy({
      backtestResults: baseResults,
      config: baseConfig,
      autoDeployMode: 'mystery',
      symbol: 'AAPL',
      strategy: 'ML_SIGNAL_BOOST',
      ...cbs,
    });
    expect(out.deployed).toBe(false);
    expect(out.reason).toMatch(/unknown/i);
    expect(cbs.onGatePassed).toHaveBeenCalledOnce();
    expect(cbs.onAutoDeploy).not.toHaveBeenCalled();
  });
});

describe('canPaperAutoDeploy', () => {
  it('delegates to isPaperExecutionMode', () => {
    expect(canPaperAutoDeploy('SIMULATED', 'paper')).toBe(true);
    expect(canPaperAutoDeploy('LIVE_ALPACA', 'broker')).toBe(false);
  });
});
