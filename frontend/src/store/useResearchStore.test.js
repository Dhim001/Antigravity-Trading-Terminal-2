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
