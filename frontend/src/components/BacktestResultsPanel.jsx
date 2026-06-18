/**
 * Expanded backtest metrics, equity chart, trade log, and CSV export.
 */
import React, { useMemo, useCallback } from 'react';
import { Download } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import BacktestMiniChart from './BacktestMiniChart';

function fmtTime(sec) {
  if (!sec) return '—';
  return new Date(sec * 1000).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function exportTradesCsv(trades, symbol, strategy) {
  const header = 'time,side,quantity,price,pnl,is_exit,reason\n';
  const rows = (trades ?? []).map(t => [
    t.time ?? '',
    t.side ?? '',
    t.quantity ?? '',
    t.price ?? '',
    t.pnl ?? '',
    t.is_exit ? '1' : '0',
    t.reason ?? '',
  ].join(','));
  const blob = new Blob([header + rows.join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `backtest_${symbol}_${strategy}_${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function BacktestResultsPanel({ results, backtestDays, backtestTimeframe = '1m', symbol, strategy, recentRuns = [] }) {
  const startingEquity = results?.starting_equity ?? 10000;
  const returnPct = useMemo(() => {
    if (!results?.total_pnl || !startingEquity) return 0;
    return (results.total_pnl / startingEquity) * 100;
  }, [results?.total_pnl, startingEquity]);

  const closedTrades = useMemo(
    () => (results?.trades ?? []).filter(t => t.is_exit),
    [results?.trades],
  );

  const allTrades = useMemo(
    () => [...(results?.trades ?? [])].reverse(),
    [results?.trades],
  );

  const onExport = useCallback(() => {
    exportTradesCsv(
      results?.trades,
      symbol ?? results?.meta?.symbol ?? 'sym',
      strategy ?? results?.meta?.strategy ?? 'strategy',
    );
  }, [results, symbol, strategy]);

  if (!results) return null;

  return (
    <div className={cn(
      'algo-backtest-lab',
      (results.total_pnl ?? 0) < 0 && 'algo-backtest-lab--down',
    )}>
      <div className="algo-backtest-lab__header">
        <div className="algo-backtest-lab__title">
          {results.meta?.days ?? backtestDays}-Day · {results.meta?.timeframe ?? backtestTimeframe} Backtest
          {results.meta?.count != null && (
            <span className="text-muted-foreground font-normal ml-1">
              ({results.meta.count.toLocaleString()} bars)
            </span>
          )}
        </div>
        {results.trades?.length > 0 && (
          <Button variant="ghost" size="xs" className="h-6 text-[0.62rem]" onClick={onExport}>
            <Download data-icon="inline-start" />
            CSV
          </Button>
        )}
      </div>

      <div className="algo-backtest-metrics algo-backtest-metrics--expanded">
        <div>Win Rate: <span className="text-foreground">{results.win_rate}%</span></div>
        <div>
          Est PnL:{' '}
          <span className={results.total_pnl >= 0 ? 'text-trading-up' : 'text-trading-down'}>
            ${results.total_pnl}
          </span>
        </div>
        <div>Max DD: <span className="text-trading-down">{results.max_drawdown}%</span></div>
        <div>Trades: <span className="text-foreground">{results.trade_count}</span></div>
        <div>Return: <span className={returnPct >= 0 ? 'text-trading-up' : 'text-trading-down'}>{returnPct.toFixed(2)}%</span></div>
        <div>Start: <span className="text-foreground">${startingEquity.toLocaleString()}</span></div>
      </div>

      <BacktestMiniChart equityCurve={results.equity_curve} totalPnl={results.total_pnl} />

      {recentRuns.length > 1 && (
        <div className="algo-backtest-compare mt-2 rounded border border-border/60 p-2">
          <p className="mb-1 text-[0.62rem] font-semibold text-muted-foreground">Recent runs (same symbol)</p>
          <table className="terminal-table m-0 w-full text-[0.58rem]">
            <thead>
              <tr>
                <th>When</th>
                <th>Strategy</th>
                <th>Days</th>
                <th className="text-right">PnL</th>
                <th className="text-right">Win%</th>
              </tr>
            </thead>
            <tbody>
              {recentRuns.slice(0, 5).map(run => (
                <tr key={run.id} className={run.id === results.run_id ? 'bg-primary/5' : ''}>
                  <td className="text-muted-foreground">{run.created_at?.slice(0, 16) ?? '—'}</td>
                  <td>{run.strategy}</td>
                  <td>{run.days}</td>
                  <td className={cn(
                    'num-mono text-right',
                    (run.summary?.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
                  )}>
                    {run.summary?.total_pnl != null ? `$${Number(run.summary.total_pnl).toFixed(2)}` : '—'}
                  </td>
                  <td className="num-mono text-right">
                    {run.summary?.win_rate != null ? `${Number(run.summary.win_rate).toFixed(1)}%` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {allTrades.length > 0 && (
        <div className="algo-backtest-trades scroll-panel-y scroll-panel-y-0 max-h-48">
          <table className="terminal-table m-0 text-[0.58rem]">
            <thead>
              <tr>
                <th>Time</th>
                <th>Side</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Price</th>
                <th className="text-right">PnL</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {allTrades.map((t, i) => (
                <tr key={`${t.time}-${t.side}-${i}`}>
                  <td className="text-muted-foreground">{fmtTime(t.time)}</td>
                  <td>{t.side}{t.is_exit ? ' ↗' : ''}</td>
                  <td className="num-mono text-right">{Number(t.quantity).toFixed(4)}</td>
                  <td className="num-mono text-right">{Number(t.price).toFixed(2)}</td>
                  <td className={cn(
                    'num-mono text-right',
                    t.pnl != null && (t.pnl >= 0 ? 'text-trading-up' : 'text-trading-down'),
                  )}>
                    {t.pnl != null ? `$${Number(t.pnl).toFixed(2)}` : '—'}
                  </td>
                  <td className="text-muted-foreground">{t.reason ?? (t.is_exit ? 'EXIT' : 'ENTRY')}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {closedTrades.length > 0 && (
            <p className="mt-1 px-1 text-[0.58rem] text-muted-foreground">
              {closedTrades.length} closed trade{closedTrades.length !== 1 ? 's' : ''} shown (entries + exits)
            </p>
          )}
        </div>
      )}
    </div>
  );
}
