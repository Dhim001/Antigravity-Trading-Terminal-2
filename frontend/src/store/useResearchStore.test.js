import { describe, it, expect, beforeEach, vi } from 'vitest';

const resolveBacktestForLabAsync = vi.fn();

vi.mock('../services/backtestStorage', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    resolveBacktestForLab: (...args) => actual.resolveBacktestForLab(...args),
    resolveBacktestForLabAsync: (...args) => resolveBacktestForLabAsync(...args),
    offloadBacktestFromMemory: (...args) => actual.offloadBacktestFromMemory(...args),
  };
});

import { useResearchStore } from './useResearchStore';

describe('useResearchStore openBacktestLab', () => {
  beforeEach(() => {
    resolveBacktestForLabAsync.mockReset();
    useResearchStore.setState({
      backtestLabOpen: false,
      backtestResults: null,
    });
  });

  it('schedules IDB restore when session miss returns slim stub', async () => {
    const full = {
      run_id: 'run-1',
      total_pnl: 42,
      trades: [{ id: 1 }, { id: 2 }],
      meta: { symbol: 'BTCUSDT' },
    };
    resolveBacktestForLabAsync.mockResolvedValue(full);

    useResearchStore.setState({
      backtestResults: {
        run_id: 'run-1',
        _offloaded: true,
        trades: [],
        total_pnl: 42,
      },
    });

    useResearchStore.getState().openBacktestLab('results');

    expect(useResearchStore.getState().backtestLabOpen).toBe(true);
    expect(resolveBacktestForLabAsync).toHaveBeenCalledOnce();

    await vi.waitFor(() => {
      expect(useResearchStore.getState().backtestResults.trades).toHaveLength(2);
    });
  });

  it('does not apply async restore after Lab is closed', async () => {
    let resolveRestore;
    resolveBacktestForLabAsync.mockImplementation(
      () => new Promise((resolve) => { resolveRestore = resolve; }),
    );

    useResearchStore.setState({
      backtestResults: {
        run_id: 'run-2',
        _offloaded: true,
        trades: [],
        total_pnl: 1,
      },
    });

    useResearchStore.getState().openBacktestLab('results');
    expect(resolveBacktestForLabAsync).toHaveBeenCalledOnce();

    useResearchStore.getState().setBacktestLabOpen(false);
    resolveRestore({
      run_id: 'run-2',
      total_pnl: 1,
      trades: [{ id: 1 }],
    });

    await Promise.resolve();
    await Promise.resolve();
    expect(useResearchStore.getState().backtestResults._offloaded).toBe(true);
    expect(useResearchStore.getState().backtestResults.trades).toEqual([]);
  });
});
describe('backtest job slots', () => {
  beforeEach(() => {
    useResearchStore.setState({
      backtestJobId: null,
      backtestJobsById: {},
      backtestProgress: null,
      backtestRunning: false,
    });
  });

  it('keeps foreign job progress out of the watched slot', () => {
    const s = useResearchStore.getState();
    s.setBacktestJobId('job-a');
    s.setBacktestProgress({ pct: 30, job_id: 'job-a' });
    s.upsertBacktestJobSlot('job-b', { progress: { pct: 90 }, status: 'running' });

    const next = useResearchStore.getState();
    expect(next.backtestProgress.pct).toBe(30);
    expect(next.backtestJobsById['job-b'].progress.pct).toBe(90);
  });

  it('releases the watched job so the next run is adopted', () => {
    const s = useResearchStore.getState();
    s.setBacktestJobId('job-a');
    expect(useResearchStore.getState().backtestJobId).toBe('job-a');

    s.beginBacktestRun();
    expect(useResearchStore.getState().backtestJobId).toBeNull();

    // The new run's first progress message claims the slot.
    s.setBacktestJobId('job-b');
    s.setBacktestProgress({ pct: 5, job_id: 'job-b' });
    const next = useResearchStore.getState();
    expect(next.backtestJobId).toBe('job-b');
    expect(next.backtestProgress.pct).toBe(5);
  });
});

describe('invalidateMlBacktests', () => {
  beforeEach(() => {
    useResearchStore.setState({
      backtestResults: {
        run_id: 'ml-1',
        total_pnl: 10,
        meta: { symbol: 'ETHUSDT', strategy: 'LSTM_DIRECTION' },
      },
      backtestSnapshot: '{"x":1}',
      backtestOverlay: { trades: [] },
      backtestRuns: [{ run_id: 'ml-1' }],
    });
  });

  it('clears matching ML results but keeps run history', () => {
    const cleared = useResearchStore.getState().invalidateMlBacktests({
      strategy: 'LSTM_DIRECTION',
      symbol: 'ETHUSDT',
    });
    expect(cleared).toBe(true);
    const s = useResearchStore.getState();
    expect(s.backtestResults).toBeNull();
    expect(s.backtestSnapshot).toBeNull();
    expect(s.backtestOverlay).toBeNull();
    expect(s.backtestRuns).toHaveLength(1);
  });

  it('does not clear unrelated symbol/strategy', () => {
    const cleared = useResearchStore.getState().invalidateMlBacktests({
      strategy: 'ML_SIGNAL_BOOST',
      symbol: 'BTCUSDT',
    });
    expect(cleared).toBe(false);
    expect(useResearchStore.getState().backtestResults?.run_id).toBe('ml-1');
  });
});

/** MEMORY_CENTRIC_REVIEW #39 — wholesale history replace must respect caps. */
describe('setAgentInsightHistory caps (#39)', () => {
  beforeEach(() => {
    useResearchStore.setState({ agentInsightHistory: {} });
  });

  it('caps a symbol history at 20 entries on wholesale replace', () => {
    const insights = Array.from({ length: 30 }, (_, i) => ({ insight_id: `i${i}` }));
    useResearchStore.getState().setAgentInsightHistory('BTCUSDT', insights);
    const hist = useResearchStore.getState().agentInsightHistory.BTCUSDT;
    expect(hist).toHaveLength(20);
    expect(hist[0].insight_id).toBe('i0'); // newest-first order preserved
  });

  it('caps the symbol map at 8 entries', () => {
    for (let i = 0; i < 10; i++) {
      useResearchStore.getState().setAgentInsightHistory(`SYM${i}`, [{ insight_id: `x${i}` }]);
    }
    const keys = Object.keys(useResearchStore.getState().agentInsightHistory);
    expect(keys).toHaveLength(8);
    expect(keys).not.toContain('SYM0');
    expect(keys).not.toContain('SYM1');
    expect(keys).toContain('SYM9');
  });

  it('uppercases the symbol key so replace and append share a bucket', () => {
    useResearchStore.getState().setAgentInsightHistory('btcusdt', [{ insight_id: 'a' }]);
    expect(useResearchStore.getState().agentInsightHistory.BTCUSDT).toHaveLength(1);
  });
});

/** MEMORY_CENTRIC_REVIEW #42 — analytics TTL + snapshot lifecycle. */
describe('analyticsReport TTL and backtestSnapshot lifecycle (#42)', () => {
  beforeEach(() => {
    useResearchStore.setState({
      analyticsReport: null,
      backtestSnapshot: null,
      backtestResults: null,
    });
  });

  it('rebuilds from the partial when the previous report is stale', () => {
    useResearchStore.setState({
      analyticsReport: {
        report: 'dashboard',
        equity: { points: [1, 2] },
        _updatedAt: Date.now() - 31 * 60 * 1000,
      },
    });
    useResearchStore.getState().setAnalyticsReport({ report: 'risk', risk: { var95: -3 } });
    const rep = useResearchStore.getState().analyticsReport;
    expect(rep.risk).toEqual({ var95: -3 });
    expect(rep.equity).toBeUndefined(); // stale base dropped, not merged
    expect(rep.report).toBe('risk');
  });

  it('merges partials into a fresh dashboard report and keeps identity', () => {
    useResearchStore.getState().setAnalyticsReport({ report: 'dashboard', equity: { points: [1] } });
    useResearchStore.getState().setAnalyticsReport({ report: 'risk', risk: { var95: -3 } });
    const rep = useResearchStore.getState().analyticsReport;
    expect(rep.report).toBe('dashboard');
    expect(rep.equity).toEqual({ points: [1] });
    expect(rep.risk).toEqual({ var95: -3 });
  });

  it('clears backtestSnapshot when results are cleared', () => {
    useResearchStore.setState({
      backtestSnapshot: '{"x":1}',
      backtestResults: { run_id: 'r1' },
    });
    useResearchStore.getState().setBacktestResults(null);
    expect(useResearchStore.getState().backtestSnapshot).toBeNull();
  });

  it('keeps backtestSnapshot when results arrive', () => {
    useResearchStore.setState({ backtestSnapshot: '{"x":1}' });
    useResearchStore.getState().setBacktestResults({ run_id: 'r2' });
    expect(useResearchStore.getState().backtestSnapshot).toBe('{"x":1}');
  });
});
