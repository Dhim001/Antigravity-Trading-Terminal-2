/**
 * Backtest metrics, equity chart, trade log, and CSV export.
 */
import React, { useMemo, useCallback, useState, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { Download, Maximize2, AlertTriangle, LineChart, Loader2, FileText, Sparkles } from 'lucide-react';
import { openBacktestLabResults } from '../lib/backtestLab';
import { useStore } from '../store/useStore';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { StatCard } from '@/components/StatCard';
import BacktestMiniChart from './BacktestMiniChart';
import BacktestPriceChart from './BacktestPriceChart';
import BacktestComparePanel from './BacktestComparePanel';
import StrategySuggestPanel from './StrategySuggestPanel';
import BacktestParityPanel from './BacktestParityPanel';
import BacktestReasoningPanel from './BacktestReasoningPanel';
import BacktestMetaLabelWalkForwardPanel from './BacktestMetaLabelWalkForwardPanel';
import { cn } from '@/lib/utils';
import { fetchBacktestTrades, fetchBacktestRun } from '../api/endpoints';
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
import { exportBacktestPdf } from '@/lib/backtestExport';

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
      <div className="algo-backtest-lab__meta-chips">
        <span className="algo-backtest-lab__chip algo-backtest-lab__chip--symbol">{symbol}</span>
        <span className="algo-backtest-lab__chip">{strategy}</span>
        <span className="algo-backtest-lab__chip">{meta.days ?? backtestDays}d</span>
        <span className="algo-backtest-lab__chip">{meta.timeframe ?? backtestTimeframe}</span>
        {allocation != null && (
          <span className="algo-backtest-lab__chip num-mono" title="Max notional cap">
            cap ${Number(allocation).toLocaleString()}
          </span>
        )}
        {results?.risk_base != null && (
          <span className="algo-backtest-lab__chip num-mono" title="Risk base (1% sizing)">
            risk ${Number(results.risk_base).toLocaleString()}
          </span>
        )}
      </div>
      {(range || meta.resolution_note || meta.timeframe_note) && (
        <div className="algo-backtest-lab__meta-secondary">
          {range && <span className="algo-backtest-lab__meta-range">{range}</span>}
          {(meta.resolution_note || meta.timeframe_note) && (
            <span className="algo-backtest-lab__meta-note">
              {[meta.resolution_note, meta.timeframe_note].filter(Boolean).join(' · ')}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

import FilterRejectsDashboard from './FilterRejectsDashboard';

function FilterRejectsSection({ summary }) {
  const rejects = summary?.filter_rejects;
  const total = summary?.filter_rejects_total;
  if (!rejects && !total) return null;
  return (
    <FilterRejectsDashboard
      className="algo-backtest-lab__section algo-backtest-lab__section--filters"
      rejects={rejects}
      total={total}
      hint="Entry signals blocked by analyst filters during replay (min_score, trend alignment, elevated vol, HTF confirm)."
    />
  );
}

function MonteCarloSection({ monteCarlo }) {
  if (!monteCarlo?.simulations) return null;
  return (
    <section className="algo-backtest-lab__section mb-3 rounded border border-border/50 bg-muted/10 p-2">
      <p className="algo-backtest-table-scroll__caption mb-1.5">Monte Carlo confidence ({monteCarlo.simulations} sims)</p>
      <div className="grid grid-cols-3 gap-2 text-[0.62rem]">
        <div>
          <span className="text-muted-foreground block">PnL 5th</span>
          <span className="num-mono font-semibold">${Number(monteCarlo.pnl_p5).toFixed(2)}</span>
        </div>
        <div>
          <span className="text-muted-foreground block">Median</span>
          <span className="num-mono font-semibold">${Number(monteCarlo.pnl_p50).toFixed(2)}</span>
        </div>
        <div>
          <span className="text-muted-foreground block">PnL 95th</span>
          <span className="num-mono font-semibold">${Number(monteCarlo.pnl_p95).toFixed(2)}</span>
        </div>
      </div>
      <p className="text-[0.55rem] text-muted-foreground m-0 mt-1">
        Bootstrap resample of {monteCarlo.trade_count} closed trades — not a forward projection.
      </p>
    </section>
  );
}

function PortfolioResultsSection({ results }) {
  const rows = results?.symbol_results;
  if (!results?.portfolio || !rows?.length) return null;
  return (
    <section className="algo-backtest-lab__section mb-3">
      <p className="algo-backtest-table-scroll__caption mb-1.5">
        Portfolio backtest — {results.symbols_tested} symbol{results.symbols_tested === 1 ? '' : 's'}
        {results.symbols_failed > 0 ? ` (${results.symbols_failed} failed)` : ''}
      </p>
      <div className="algo-backtest-table-scroll">
        <table className="terminal-table algo-backtest-table m-0 text-[0.58rem]">
          <thead>
            <tr>
              <th>Symbol</th>
              <th className="text-right">PnL</th>
              <th className="text-right">Trades</th>
              <th className="text-right">Win%</th>
              <th className="text-right">Sharpe</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.symbol}>
                <td>{row.symbol}</td>
                <td className={cn(
                  'num-mono text-right',
                  row.error ? 'text-muted-foreground' : (row.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
                )}>
                  {row.error ? row.error : `$${Number(row.total_pnl).toFixed(2)}`}
                </td>
                <td className="num-mono text-right">{row.trade_count ?? '—'}</td>
                <td className="num-mono text-right">
                  {row.win_rate != null ? `${Number(row.win_rate).toFixed(1)}%` : '—'}
                </td>
                <td className="num-mono text-right">
                  {row.sharpe_ratio != null ? Number(row.sharpe_ratio).toFixed(2) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function BacktestSummaryCards({ summary, results, isFull }) {
  const s = summary ?? {};
  const pnl = s.total_pnl ?? results?.total_pnl ?? 0;
  const pnlTone = pnl >= 0 ? 'up' : 'down';

  const compactCards = (
    <>
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
    </>
  );

  if (!isFull) {
    return (
      <div className="algo-backtest-stat-grid algo-backtest-stat-grid--compact">
        {compactCards}
      </div>
    );
  }

  return (
    <div className="algo-backtest-stat-grid">
      {compactCards}
      <StatCard label="Avg win" value={`$${Number(s.avg_win ?? 0).toFixed(2)}`} tone="up" />
      <StatCard label="Avg loss" value={`$${Number(s.avg_loss ?? 0).toFixed(2)}`} tone="down" />
      <StatCard label="Expectancy" value={`$${Number(s.expectancy ?? 0).toFixed(2)}`} tone={pnlTone} />
      <StatCard
        label="Avg hold"
        value={s.avg_hold_hours ? `${Number(s.avg_hold_hours).toFixed(1)}h` : '—'}
      />
      <StatCard
        label="Sharpe"
        value={s.sharpe_ratio != null ? Number(s.sharpe_ratio).toFixed(2) : '—'}
      />
      <StatCard
        label="Sortino"
        value={s.sortino_ratio != null ? Number(s.sortino_ratio).toFixed(2) : '—'}
      />
      <StatCard
        label="Alpha vs B&H"
        value={s.alpha_pnl != null ? `$${Number(s.alpha_pnl).toFixed(2)}` : '—'}
        tone={(s.alpha_pnl ?? 0) >= 0 ? 'up' : 'down'}
        sub={s.benchmark?.return_pct != null ? `B&H ${Number(s.benchmark.return_pct).toFixed(1)}%` : undefined}
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
  reasoningPending = false,
  showReasoningSection = false,
  advisorBotId = null,
  agentLlmAvailable = false,
}) {
  const setBacktestResults = useStore((s) => s.setBacktestResults);
  const setBacktestOverlay = useStore((s) => s.setBacktestOverlay);
  const setActiveSymbol = useStore((s) => s.setActiveSymbol);
  const backtestOverlay = useStore((s) => s.backtestOverlay);
  const botConfig = useStore((s) => s.botConfig);
  const activeBots = useStore((s) => s.activeBots);
  const activeSymbol = useStore((s) => s.activeSymbol);
  const botStrategy = useStore((s) => s.botStrategy);
  const botTimeframe = useStore((s) => s.botTimeframe);

  const [fullTrades, setFullTrades] = useState(null);
  const [loadingTrades, setLoadingTrades] = useState(false);
  const [loadingRun, setLoadingRun] = useState(false);
  const [tradeReasonFilter, setTradeReasonFilter] = useState('all');
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

  const allTrades = useMemo(() => {
    let rows = [...displayTrades].reverse();
    if (tradeReasonFilter === 'entries') {
      rows = rows.filter((t) => !t.is_exit);
    } else if (tradeReasonFilter === 'exits') {
      rows = rows.filter((t) => t.is_exit);
    } else if (tradeReasonFilter !== 'all') {
      rows = rows.filter((t) => (t.reason || (t.is_exit ? 'EXIT' : 'ENTRY')) === tradeReasonFilter);
    }
    return rows;
  }, [displayTrades, tradeReasonFilter]);

  const closedTrades = useMemo(
    () => displayTrades.filter(t => t.is_exit),
    [displayTrades],
  );

  const entryCount = useMemo(
    () => displayTrades.filter((t) => !t.is_exit).length,
    [displayTrades],
  );

  const reasoningRequested = Boolean(results?.meta?.reasoning || reasoningPending);

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

  const onExportPdf = useCallback(async () => {
    let exportTrades = fullTrades ?? previewTrades;
    if (results?.run_id && (results.trades_total ?? 0) > exportTrades.length) {
      try {
        exportTrades = await fetchBacktestTrades(results.run_id);
      } catch {
        /* use preview trades */
      }
    }

    const toastId = toast.loading('Preparing PDF with chart…');
    const outcome = await exportBacktestPdf({
      results,
      symbol: symbol ?? results?.meta?.symbol,
      strategy: strategy ?? results?.meta?.strategy,
      days: backtestDays,
      timeframe: backtestTimeframe ?? results?.meta?.timeframe,
      trades: exportTrades,
    });
    toast.dismiss(toastId);

    if (!outcome?.ok) {
      toast.error(outcome?.error || 'Could not download PDF');
      return;
    }
    toast.success(outcome.filename ? `Downloaded ${outcome.filename}` : 'PDF downloaded');
  }, [fullTrades, previewTrades, results, symbol, strategy, backtestDays, backtestTimeframe]);

  const loadSavedRun = useCallback(async (runId) => {
    if (!runId || loadingRun) return;
    setLoadingRun(true);
    try {
      const { results: loaded } = await fetchBacktestRun(runId, { setBacktestResults });
      setBacktestResults(loaded);
      toast.success('Loaded saved backtest run');
    } catch (err) {
      toast.error(err?.message || 'Failed to load run');
    } finally {
      setLoadingRun(false);
    }
  }, [loadingRun, setBacktestResults]);

  const onTradeRowClick = useCallback((trade) => {
    if (!trade?.time) return;
    window.dispatchEvent(new CustomEvent('backtest-focus-bar', {
      detail: { time: trade.time, symbol: results?.meta?.symbol ?? symbol },
    }));
  }, [results, symbol]);

  if (!results) return null;

  const simMode = results.sim_mode;
  const pnl = summary?.total_pnl ?? results?.total_pnl ?? 0;
  const pnlTone = pnl >= 0 ? 'up' : 'down';

  const runTags = [
    simMode && {
      key: 'sim',
      label: simMode === 'research' ? 'Research' : simMode === 'live_aligned' ? 'Live-aligned' : simMode,
    },
    results.meta?.walk_forward && {
      key: 'wf',
      label: `WF ${results.meta?.train_pct ?? results.walk_forward?.train_pct ?? 70}%`,
    },
    results.meta?.oos_pct && !results.meta?.walk_forward && {
      key: 'oos',
      label: `OOS ${results.meta.oos_pct}%`,
    },
    results.sweep && { key: 'sweep', label: 'Sweep best' },
    results.reasoning?.trades?.length > 0 && { key: 'llm', label: 'LLM explained', icon: Sparkles },
  ].filter(Boolean);

  return (
    <div className={cn(
      'algo-backtest-lab',
      isFull && 'algo-backtest-lab--full backtest-lab__report',
      !isFull && 'algo-backtest-lab--compact',
      pnl < 0 && 'algo-backtest-lab--down',
    )}>
      {stale && (
        <Alert variant="default" className="algo-backtest-stale-banner py-2">
          <AlertTriangle data-icon="inline-start" className="size-3.5" />
          <AlertDescription className="text-xs">
            Deploy settings changed since this run. Re-run backtest to refresh.
          </AlertDescription>
        </Alert>
      )}

      <header className="algo-backtest-lab__head">
        <div className="algo-backtest-lab__title-block">
          <h3 className="algo-backtest-lab__title">
            <span className="algo-backtest-lab__title-main">
              {results.meta?.days ?? backtestDays}-Day · {results.meta?.timeframe ?? backtestTimeframe} Backtest
            </span>
            {results.meta?.count != null && (
              <span className="algo-backtest-lab__title-sub">
                {results.meta.count.toLocaleString()} bars
              </span>
            )}
            <span className={cn(
              'algo-backtest-lab__pnl-pill num-mono',
              pnlTone === 'up' ? 'algo-backtest-lab__pnl-pill--up' : 'algo-backtest-lab__pnl-pill--down',
            )}>
              {pnl >= 0 ? '+' : ''}${Number(pnl).toFixed(2)}
            </span>
          </h3>
          <BacktestMetaLine
            results={results}
            backtestDays={backtestDays}
            backtestTimeframe={backtestTimeframe}
            symbol={symbol}
            strategy={strategy}
          />
        </div>

        {runTags.length > 0 && (
          <div className="algo-backtest-lab__tags">
            {runTags.map((tag) => (
              <Badge key={tag.key} variant="outline" className="algo-backtest-lab__tag h-5 px-1.5 text-[0.55rem]">
                {tag.icon && <tag.icon className="size-3" aria-hidden />}
                {tag.label}
              </Badge>
            ))}
          </div>
        )}

        <div className="algo-backtest-lab__toolbar">
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
              type="button"
              variant="ghost"
              size="xs"
              className="h-6 text-[0.62rem]"
              onClick={() => openBacktestLabResults()}
              title="Open Backtest Lab → Results"
            >
              <Maximize2 data-icon="inline-start" />
              Lab
            </Button>
          )}
          <Button
            type="button"
            variant="ghost"
            size="xs"
            className="h-6 text-[0.62rem]"
            onClick={onExportPdf}
            title="Download PDF report with price chart and trades"
          >
            <FileText data-icon="inline-start" />
            PDF
          </Button>
          {displayTrades.length > 0 && (
            <Button
              type="button"
              variant="ghost"
              size="xs"
              className="h-6 text-[0.62rem]"
              onClick={onExport}
            >
              <Download data-icon="inline-start" />
              CSV
            </Button>
          )}
        </div>
      </header>

      <div className="algo-backtest-lab__body">
      <BacktestSummaryCards summary={summary} results={results} isFull={isFull} />

      <section className="algo-backtest-lab__section algo-backtest-lab__section--advisor">
        <StrategySuggestPanel
          botId={advisorBotId}
          candidateBots={activeBots}
          backtestDays={backtestDays}
          recentResults={results}
          agentLlmAvailable={agentLlmAvailable}
          symbol={symbol ?? results?.meta?.symbol}
          compact={!isFull}
        />
      </section>

      {isFull && <PortfolioResultsSection results={results} />}
      {isFull && <MonteCarloSection monteCarlo={results?.monte_carlo} />}
      {isFull && (
        <BacktestMetaLabelWalkForwardPanel walkForward={results?.meta_label_walk_forward} />
      )}
      {isFull && <FilterRejectsSection summary={summary} />}

      {isFull && (
        <BacktestParityPanel results={results} symbol={symbol} strategy={strategy} />
      )}

      {!isFull && results?.sweep?.results?.length > 0 && (
        <button
          type="button"
          className="algo-backtest-sweep-teaser text-[0.62rem] text-primary hover:underline text-left px-1 py-1"
          onClick={() => useStore.getState().openBacktestLab('optimizer')}
        >
          {results.sweep.configs_tested ?? results.sweep.results.length} configs tested → Open optimizer
        </button>
      )}

      {isFull && (
        <div className="algo-backtest-lab__tools-grid">
          <BacktestComparePanel
            currentRun={{ run_id: results.run_id, results }}
            recentRuns={recentRuns}
          />
        </div>
      )}

      <section className="algo-backtest-lab__section algo-backtest-lab__section--chart">
        <BacktestPriceChart
          symbol={symbol ?? results?.meta?.symbol}
          meta={results?.meta}
          timeframe={backtestTimeframe ?? results?.meta?.timeframe ?? '1m'}
          trades={displayTrades}
          className={isFull ? 'backtest-price-chart-wrap--lab' : undefined}
        />
        <BacktestMiniChart
          equityCurve={results.equity_curve}
          drawdownCurve={results.drawdown_curve}
          totalPnl={results.total_pnl}
          trades={displayTrades}
          className={isFull ? 'backtest-mini-chart--lab' : undefined}
        />
      </section>

      <div className={cn(
        'algo-backtest-lab__tables',
        isFull && 'algo-backtest-lab__tables--full',
      )}>
      {recentRuns.length > 0 && isFull && (
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
                <tr
                  key={run.id}
                  className={cn(
                    'cursor-pointer hover:bg-muted/40',
                    run.id === results.run_id && 'bg-primary/5',
                  )}
                  onClick={() => loadSavedRun(run.id)}
                  title="Load saved run"
                >
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
            <span className="inline-flex flex-wrap items-center gap-2">
              <span>{`Trade log (${tradesTotal} fills)`}</span>
              {loadingTrades && <Loader2 className="size-3 animate-spin" aria-hidden />}
              {isFull && (
                <span className="algo-backtest-trade-filters">
                  {['all', 'entries', 'exits', 'SL', 'TP', 'SIGNAL'].map((f) => (
                    <button
                      key={f}
                      type="button"
                      className={cn(
                        'algo-backtest-trade-filters__btn',
                        tradeReasonFilter === f && 'algo-backtest-trade-filters__btn--active',
                      )}
                      onClick={() => setTradeReasonFilter(f)}
                    >
                      {f}
                    </button>
                  ))}
                </span>
              )}
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
                <tr
                  key={`${t.time}-${t.side}-${tradeWindow.start + i}`}
                  className="cursor-pointer hover:bg-muted/30"
                  onClick={() => onTradeRowClick(t)}
                  title="Focus chart on this bar"
                >
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

      {(showReasoningSection || reasoningRequested || results?.reasoning) && (
        <BacktestReasoningPanel
          reasoning={results.reasoning}
          reasoningRequested={reasoningRequested}
          entryCount={entryCount}
          tradeLog={fullTrades ?? previewTrades}
          results={results}
          className="mt-2"
        />
      )}
      </div>
    </div>
  );
}
