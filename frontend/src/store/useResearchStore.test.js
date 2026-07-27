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
