/**
 * Backtest metrics, equity chart, trade log, and CSV export.
 */
import React, { useMemo, useCallback, useState, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { Download, Maximize2, AlertTriangle, LineChart, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { StatCard } from '@/components/StatCard';
import BacktestMiniChart from './BacktestMiniChart';
import BacktestComparePanel from './BacktestComparePanel';
import BacktestSweepPanel from './BacktestSweepPanel';
import { useStore } from '../store/useStore';
import { fetchBacktestTrades } from '../api/endpoints';
import { useVirtualRows, VirtualTablePadding } from './VirtualTableBody';
import {
  backtestFingerprint,
  fmtBacktestRange,
  isBacktestStale,
  normalizeTradingSymbol,
  notifyBacktestOverlayChanged,
  ensureBacktestChartHistory,
  symbolsMatch,
} from '@/lib/backtestDisplay';

const EMPTY_TRADES = [];

function BacktestTable({ caption, children, className, onScroll }) {
  return (
    <div
      className={cn('algo-backtest-table-scroll', className)}
      onScroll={onScroll}
    >
      {caption != null && caption !== '' && (
        <p className="algo-backtest-table-scroll__caption">{caption}</p>
      )}
      {children}
    </div>
  );
}

function fmtTime(sec) {
  if (!sec) return '—';
  return new Date(sec * 1000).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function exportTradesCsv(trades, symbol, strategy) {
  const header = 'time,side,quantity,price,pnl,is_exit,reason,hold_seconds\n';
  const rows = (trades ?? []).map(t => [
    t.time ?? '',
    t.side ?? '',
    t.quantity ?? '',
    t.price ?? '',
    t.pnl ?? '',
    t.is_exit ? '1' : '0',
    t.reason ?? '',
    t.hold_seconds ?? '',
  ].join(','));
  const blob = new Blob([header + rows.join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `backtest_${symbol}_${strategy}_${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function BacktestMetaLine({ results, backtestDays, backtestTimeframe, symbol, strategy }) {
  const meta = results?.meta ?? {};
  const range = fmtBacktestRange(meta);
  const allocation = results?.allocation ?? results?.starting_equity;

  return (
    <div className="algo-backtest-lab__meta">
      <div className="algo-backtest-lab__meta-row">
        <span className="font-medium text-foreground">{symbol}</span>
        <span className="text-muted-foreground">·</span>
        <span>{strategy}</span>
        <span className="text-muted-foreground">·</span>
        <span>{meta.days ?? backtestDays}d</span>
        <span className="text-muted-foreground">·</span>
        <span>{meta.timeframe ?? backtestTimeframe}</span>
        {allocation != null && (
          <>
            <span className="text-muted-foreground">·</span>
            <span className="num-mono">${Number(allocation).toLocaleString()} alloc</span>
          </>
        )}
      </div>
      {range && (
        <p className="algo-backtest-lab__meta-range">{range}</p>
      )}
      {(meta.resolution_note || meta.timeframe_note) && (
        <p className="algo-backtest-lab__meta-note">
          {[meta.resolution_note, meta.timeframe_note].filter(Boolean).join(' · ')}
        </p>
      )}
    </div>
  );
}

function BacktestSummaryCards({ summary, results, isFull }) {
  const s = summary ?? {};
  const pnl = s.total_pnl ?? results?.total_pnl ?? 0;
  const pnlTone = pnl >= 0 ? 'up' : 'down';

  return (
    <div className="algo-backtest-stat-grid">
      <StatCard label="Est PnL" value={`$${Number(pnl).toFixed(2)}`} tone={pnlTone} />
      <StatCard
        label="Return"
        value={`${Number(s.return_pct ?? 0).toFixed(2)}%`}
        tone={pnlTone}
      />
      <StatCard label="Win rate" value={`${Number(s.win_rate ?? results?.win_rate ?? 0).toFixed(1)}%`} />
      <StatCard label="Max DD" value={`${Number(s.max_drawdown ?? results?.max_drawdown ?? 0).toFixed(2)}%`} tone="down" />
      <StatCard label="Trades" value={String(s.total_trades ?? results?.trade_count ?? 0)} />
      <StatCard
        label="Profit factor"
        value={s.profit_factor != null ? Number(s.profit_factor).toFixed(2) : '—'}
        sub={s.profit_factor == null && (s.gross_profit > 0) ? 'No losses' : undefined}
      />
      <StatCard label="Avg win" value={`$${Number(s.avg_win ?? 0).toFixed(2)}`} tone="up" />
      <StatCard label="Avg loss" value={`$${Number(s.avg_loss ?? 0).toFixed(2)}`} tone="down" />
      <StatCard label="Expectancy" value={`$${Number(s.expectancy ?? 0).toFixed(2)}`} tone={pnlTone} />
      <StatCard
        label="Avg hold"
        value={s.avg_hold_hours ? `${Number(s.avg_hold_hours).toFixed(1)}h` : '—'}
      />
      {isFull && (
        <>
          <StatCard
            label="Sharpe"
            value={s.sharpe_ratio != null ? Number(s.sharpe_ratio).toFixed(2) : '—'}
          />
          <StatCard
            label="Time in mkt"
            value={s.time_in_market_pct != null ? `${Number(s.time_in_market_pct).toFixed(1)}%` : '—'}
          />
          <StatCard
            label="Blocked"
            value={String(s.blocked_entries ?? 0)}
            sub="Risk gate rejects"
          />
          <StatCard
            label="Max loss streak"
            value={String(s.max_consecutive_losses ?? 0)}
            tone={s.max_consecutive_losses > 2 ? 'down' : 'neutral'}
          />
          <StatCard
            label="Fees"
            value={`$${Number(s.total_fees ?? results?.costs?.total_fees ?? 0).toFixed(2)}`}
            sub={
              (s.slippage_bps || results?.costs?.slippage_bps)
                ? `${s.slippage_bps ?? results?.costs?.slippage_bps}bps slip`
                : undefined
            }
          />
        </>
      )}
    </div>
  );
}

export default function BacktestResultsPanel({
  results,
  backtestDays,
  backtestTimeframe = '1m',
  symbol,
  strategy,
  recentRuns = [],
  variant = 'compact',
  snapshot = null,
  oosPct = null,
}) {
  const setBacktestLabOpen = useStore((s) => s.setBacktestLabOpen);
  const setBacktestOverlay = useStore((s) => s.setBacktestOverlay);
  const setActiveSymbol = useStore((s) => s.setActiveSymbol);
  const backtestOverlay = useStore((s) => s.backtestOverlay);
  const botConfig = useStore((s) => s.botConfig);
  const activeSymbol = useStore((s) => s.activeSymbol);
  const botStrategy = useStore((s) => s.botStrategy);
  const botTimeframe = useStore((s) => s.botTimeframe);

  const [fullTrades, setFullTrades] = useState(null);
  const [loadingTrades, setLoadingTrades] = useState(false);
  const loadRef = useRef(0);

  const isFull = variant === 'full';
  const summary = results?.summary;
  const tradesTotal = results?.trades_total ?? results?.trades?.length ?? 0;
  const previewTrades = results?.trades ?? EMPTY_TRADES;

  useEffect(() => {
    const runId = results?.run_id;
    if (!runId) {
      setFullTrades(null);
      return;
    }
    if (tradesTotal <= previewTrades.length) {
      setFullTrades(previewTrades);
      return;
    }

    const token = ++loadRef.current;
    setLoadingTrades(true);
    fetchBacktestTrades(runId)
      .then((trades) => {
        if (loadRef.current === token) setFullTrades(trades);
      })
      .catch(() => {
        if (loadRef.current === token) setFullTrades(previewTrades);
      })
      .finally(() => {
        if (loadRef.current === token) setLoadingTrades(false);
      });
  }, [results?.run_id, tradesTotal, results?.trades]);

  const displayTrades = fullTrades ?? previewTrades;

  const chartOn = backtestOverlay?.visible
    && backtestOverlay?.runId === results?.run_id
    && symbolsMatch(backtestOverlay?.symbol, activeSymbol);

  useEffect(() => {
    if (!chartOn || !fullTrades?.length || !results?.run_id || !backtestOverlay) return;
    if (backtestOverlay.trades?.length === fullTrades.length) return;
    setBacktestOverlay({
      ...backtestOverlay,
      trades: fullTrades,
      tradesTotal,
    });
  }, [chartOn, fullTrades, results?.run_id, backtestOverlay, setBacktestOverlay, tradesTotal]);

  const stale = useMemo(() => {
    if (!snapshot || !results) return false;
    const current = backtestFingerprint({
      symbol: activeSymbol,
      strategy: botStrategy,
      days: backtestDays,
      timeframe: botTimeframe,
      config: botConfig,
    });
    return isBacktestStale(snapshot, current);
  }, [snapshot, results, activeSymbol, botStrategy, backtestDays, botTimeframe, botConfig]);

  const allTrades = useMemo(
    () => [...displayTrades].reverse(),
    [displayTrades],
  );

  const closedTrades = useMemo(
    () => displayTrades.filter(t => t.is_exit),
    [displayTrades],
  );

  const { onScroll: onTradeScroll, window: tradeWindow } = useVirtualRows(allTrades, {
    rowHeight: 28,
    overscan: 10,
  });

  const toggleChartOverlay = useCallback(async () => {
    if (!displayTrades.length) return;

    if (chartOn) {
      setBacktestOverlay({ ...backtestOverlay, visible: false });
      notifyBacktestOverlayChanged();
      toast.success('Backtest markers hidden');
      return;
    }

    const targetSymbol = normalizeTradingSymbol(results?.meta?.symbol ?? symbol);
    let trades = displayTrades;
    if (results?.run_id && tradesTotal > trades.length) {
      try {
        trades = await fetchBacktestTrades(results.run_id);
      } catch {
        trades = displayTrades;
      }
    }

    setBacktestOverlay({
      runId: results?.run_id ?? null,
      symbol: targetSymbol,
      meta: results?.meta,
      trades,
      tradesTotal,
      equityCurve: results?.equity_curve ?? [],
      visible: true,
    });

    if (results?.meta?.oldest) {
      try {
        await ensureBacktestChartHistory(targetSymbol, results.meta);
      } catch {
        /* chart markers may be partial if archive unavailable */
      }
    }

    notifyBacktestOverlayChanged();

    if (targetSymbol && !symbolsMatch(activeSymbol, targetSymbol)) {
      setActiveSymbol(targetSymbol);
      toast.success(`Switched to ${targetSymbol} — backtest markers on chart`);
    } else {
      toast.success('Backtest markers on chart');
    }
  }, [
    activeSymbol,
    backtestOverlay,
    chartOn,
    displayTrades,
    results,
    setActiveSymbol,
    setBacktestOverlay,
    symbol,
    tradesTotal,
  ]);

  const onExport = useCallback(() => {
    exportTradesCsv(
      displayTrades,
      symbol ?? results?.meta?.symbol ?? 'sym',
      strategy ?? results?.meta?.strategy ?? 'strategy',
    );
  }, [displayTrades, results, symbol, strategy]);

  if (!results) return null;

  const simMode = results.sim_mode;

  return (
    <div className={cn(
      'algo-backtest-lab',
      isFull && 'algo-backtest-lab--full',
      (results.total_pnl ?? 0) < 0 && 'algo-backtest-lab--down',
    )}>
      {stale && (
        <Alert variant="default" className="algo-backtest-stale-banner py-2">
          <AlertTriangle data-icon="inline-start" className="size-3.5" />
          <AlertDescription className="text-xs">
            Deploy settings changed since this run. Re-run backtest to refresh.
          </AlertDescription>
        </Alert>
      )}

      <div className="algo-backtest-lab__header">
        <div className="min-w-0 flex-1">
          <div className="algo-backtest-lab__title">
            {results.meta?.days ?? backtestDays}-Day · {results.meta?.timeframe ?? backtestTimeframe} Backtest
            {results.meta?.count != null && (
              <span className="text-muted-foreground font-normal ml-1">
                ({results.meta.count.toLocaleString()} bars)
              </span>
            )}
          </div>
          <BacktestMetaLine
            results={results}
            backtestDays={backtestDays}
            backtestTimeframe={backtestTimeframe}
            symbol={symbol}
            strategy={strategy}
          />
        </div>
        <div className="algo-backtest-lab__actions shrink-0">
          {simMode && !results.meta?.oos_pct && (
            <Badge variant="outline" className="text-[0.58rem] h-5">
              {simMode === 'live_aligned' ? 'Live-aligned' : simMode}
            </Badge>
          )}
          {results.sweep && (
            <Badge variant="outline" className="text-[0.58rem] h-5">
              Sweep best
            </Badge>
          )}
          {results.meta?.oos_pct && (
            <Badge variant="outline" className="text-[0.58rem] h-5">
              OOS {results.meta.oos_pct}%
            </Badge>
          )}
          {displayTrades.length > 0 && (
            <Button
              type="button"
              variant={chartOn ? 'secondary' : 'ghost'}
              size="xs"
              className="h-6 text-[0.62rem]"
              onClick={toggleChartOverlay}
              title="Show backtest entry/exit markers on the main chart"
            >
              <LineChart data-icon="inline-start" />
              {chartOn ? 'On chart' : 'Chart'}
            </Button>
          )}
          {!isFull && (
            <Button
              variant="ghost"
              size="xs"
              className="h-6 text-[0.62rem]"
              onClick={() => setBacktestLabOpen(true)}
              title="Open full backtest report"
            >
              <Maximize2 data-icon="inline-start" />
              Report
            </Button>
          )}
          {displayTrades.length > 0 && (
            <Button variant="ghost" size="xs" className="h-6 text-[0.62rem]" onClick={onExport}>
              <Download data-icon="inline-start" />
              CSV
            </Button>
          )}
        </div>
      </div>

      <BacktestSummaryCards summary={summary} results={results} isFull={isFull} />

      {isFull && (
        <div className="algo-backtest-lab__tools-grid">
          <BacktestComparePanel
            currentRun={{ run_id: results.run_id, summary }}
            recentRuns={recentRuns}
          />
          <BacktestSweepPanel
            symbol={symbol ?? results?.meta?.symbol}
            strategy={strategy ?? results?.meta?.strategy}
            days={results?.meta?.days ?? backtestDays}
            timeframe={results?.meta?.timeframe ?? backtestTimeframe}
            oosPct={oosPct ?? results?.meta?.oos_pct}
            results={results}
          />
        </div>
      )}

      <section className="algo-backtest-lab__section algo-backtest-lab__section--chart">
        <BacktestMiniChart
          equityCurve={results.equity_curve}
          totalPnl={results.total_pnl}
          trades={displayTrades}
          className={isFull ? 'backtest-mini-chart--lab' : undefined}
        />
      </section>

      <div className={cn(
        'algo-backtest-lab__tables',
        isFull && 'algo-backtest-lab__tables--full',
      )}>
      {recentRuns.length > 0 && (
        <section className="algo-backtest-lab__section algo-backtest-lab__section--history">
        <BacktestTable
          caption={recentRuns.length > 1 ? 'Recent runs (same symbol)' : 'Saved runs (same symbol)'}
          className={cn(
            'algo-backtest-table-scroll--history',
            isFull && 'algo-backtest-table-scroll--history-full',
          )}
        >
          <table className="terminal-table algo-backtest-table m-0 text-[0.58rem]">
            <thead>
              <tr>
                <th>When</th>
                <th>Strategy</th>
                <th>Days</th>
                <th className="text-right">PnL</th>
                <th className="text-right">Win%</th>
                <th className="text-right">PF</th>
              </tr>
            </thead>
            <tbody>
              {recentRuns.slice(0, isFull ? 15 : 5).map(run => (
                <tr key={run.id} className={run.id === results.run_id ? 'bg-primary/5' : ''}>
                  <td className="text-muted-foreground whitespace-nowrap">{run.created_at?.slice(0, 16) ?? '—'}</td>
                  <td className="whitespace-nowrap">{run.strategy}</td>
                  <td className="num-mono">{run.days}</td>
                  <td className={cn(
                    'num-mono text-right whitespace-nowrap',
                    (run.summary?.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
                  )}>
                    {run.summary?.total_pnl != null ? `$${Number(run.summary.total_pnl).toFixed(2)}` : '—'}
                  </td>
                  <td className="num-mono text-right whitespace-nowrap">
                    {run.summary?.win_rate != null ? `${Number(run.summary.win_rate).toFixed(1)}%` : '—'}
                  </td>
                  <td className="num-mono text-right whitespace-nowrap">
                    {run.summary?.profit_factor != null ? Number(run.summary.profit_factor).toFixed(2) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </BacktestTable>
        </section>
      )}

      {allTrades.length > 0 && (
        <section className="algo-backtest-lab__section algo-backtest-lab__section--trades">
        <BacktestTable
          caption={(
            <span className="inline-flex items-center gap-2">
              {`Trade log (${tradesTotal} fills)`}
              {loadingTrades && <Loader2 className="size-3 animate-spin" aria-hidden />}
            </span>
          )}
          className={cn(
            'algo-backtest-table-scroll--trades',
            isFull && 'algo-backtest-table-scroll--trades-full',
          )}
          onScroll={onTradeScroll}
        >
          <table className="terminal-table algo-backtest-table m-0 text-[0.58rem]">
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
              <VirtualTablePadding height={tradeWindow.topPad} colSpan={6} />
              {tradeWindow.slice.map((t, i) => (
                <tr key={`${t.time}-${t.side}-${tradeWindow.start + i}`}>
                  <td className="text-muted-foreground whitespace-nowrap">{fmtTime(t.time)}</td>
                  <td className="whitespace-nowrap">{t.side}{t.is_exit ? ' ↗' : ''}</td>
                  <td className="num-mono text-right whitespace-nowrap">{Number(t.quantity).toFixed(4)}</td>
                  <td className="num-mono text-right whitespace-nowrap">{Number(t.price).toFixed(2)}</td>
                  <td className={cn(
                    'num-mono text-right whitespace-nowrap',
                    t.pnl != null && (t.pnl >= 0 ? 'text-trading-up' : 'text-trading-down'),
                  )}>
                    {t.pnl != null ? `$${Number(t.pnl).toFixed(2)}` : '—'}
                  </td>
                  <td className="text-muted-foreground max-w-[10rem] truncate" title={t.reason ?? (t.is_exit ? 'EXIT' : 'ENTRY')}>
                    {t.reason ?? (t.is_exit ? 'EXIT' : 'ENTRY')}
                  </td>
                </tr>
              ))}
              <VirtualTablePadding height={tradeWindow.bottomPad} colSpan={6} />
            </tbody>
          </table>
          {closedTrades.length > 0 && (
            <p className="algo-backtest-table-scroll__footer">
              {closedTrades.length} closed trade{closedTrades.length !== 1 ? 's' : ''}
              {tradesTotal > previewTrades.length ? ' · full log loaded from server' : ''}
            </p>
          )}
        </BacktestTable>
        </section>
      )}
      </div>
    </div>
  );
}
