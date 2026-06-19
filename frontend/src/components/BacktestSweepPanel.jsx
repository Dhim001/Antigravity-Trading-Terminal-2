/**
 * Parameter sweep controls + results table (P3).
 */
import React from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { toast } from 'sonner';

const DEFAULT_SWEEP = {
  trailing_stop_percent: [1, 2, 3],
  take_profit_percent: [2, 3, 5],
};

export default function BacktestSweepPanel({
  symbol,
  strategy,
  days,
  timeframe,
  oosPct,
  results,
}) {
  const backtestRunning = useStore((s) => s.backtestRunning);
  const botConfig = useStore((s) => s.botConfig);
  const updateBotConfig = useStore((s) => s.updateBotConfig);
  const sweep = results?.sweep;

  const handleSweep = async () => {
    if (backtestRunning) return;
    useStore.getState().setBacktestRunning(true);
    useStore.getState().setBacktestProgress({ pct: 0, phase: 'sweep', message: 'Starting sweep…' });
    const { ok, error } = await sendAction(Action.RUN_BACKTEST_SWEEP, {
      symbol,
      strategy,
      config: botConfig,
      days: parseInt(days, 10) || 7,
      timeframe,
      oos_pct: oosPct || undefined,
      sweep: DEFAULT_SWEEP,
    });
    if (!ok && error) toast.error(error);
    if (!ok) {
      useStore.getState().setBacktestRunning(false);
      useStore.getState().setBacktestProgress(null);
    }
  };

  const applyConfig = (cfg) => {
    if (!cfg) return;
    updateBotConfig(cfg);
    toast.success('Applied sweep winner to deploy settings');
  };

  return (
    <div className="algo-backtest-sweep">
      <div className="algo-backtest-sweep__header">
        <span className="algo-backtest-table-scroll__caption m-0">Parameter sweep</span>
        <Button
          type="button"
          variant="outline"
          size="xs"
          className="h-6 text-[0.62rem]"
          disabled={backtestRunning}
          onClick={handleSweep}
        >
          Sweep SL × TP
        </Button>
      </div>
      <p className="algo-backtest-sweep__hint text-[0.58rem] text-muted-foreground">
        Tests {DEFAULT_SWEEP.trailing_stop_percent.length * DEFAULT_SWEEP.take_profit_percent.length} combos
        (SL 1–3%, TP 2–5%). Saves the best run.
      </p>

      {sweep?.results?.length > 0 && (
        <table className="terminal-table algo-backtest-table m-0 text-[0.58rem]">
          <thead>
            <tr>
              <th>Config</th>
              <th className="text-right">PnL</th>
              <th className="text-right">Trades</th>
              <th className="text-right">Win%</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {sweep.results.map((row, i) => {
              const isBest = i === 0;
              const summary = row.summary ?? {};
              return (
                <tr key={row.label ?? i} className={cn(isBest && 'bg-primary/5')}>
                  <td className="max-w-[9rem] truncate" title={row.label}>{row.label}</td>
                  <td className={cn(
                    'num-mono text-right whitespace-nowrap',
                    (row.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
                  )}>
                    {row.error ? '—' : `$${Number(row.total_pnl ?? 0).toFixed(2)}`}
                  </td>
                  <td className="num-mono text-right">{row.trade_count ?? summary.total_trades ?? '—'}</td>
                  <td className="num-mono text-right">
                    {summary.win_rate != null ? `${Number(summary.win_rate).toFixed(1)}%` : '—'}
                  </td>
                  <td className="text-right">
                    {isBest && row.config && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="xs"
                        className="h-5 text-[0.55rem] px-1"
                        onClick={() => applyConfig(row.config)}
                      >
                        Apply
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
