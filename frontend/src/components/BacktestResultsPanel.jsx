/**
 * Backtest metrics, equity chart, trade log, and CSV export.
 */
import React, { useMemo, useCallback, useState, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { Download, Maximize2, AlertTriangle, LineChart, Loader2, FileText, Sparkles } from 'lucide-react';
import { openBacktestLabResults } from '../lib/backtestLab';
import { useStore } from '../store/useStore';
import { useResearchStore } from '../store/useResearchStore';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { StatCard } from '@/components/StatCard';
import BacktestMiniChart from './BacktestMiniChart';
import BacktestEquityChart from './BacktestEquityChart';
import BacktestRegimeSection from './BacktestRegimeSection';
import BacktestPriceChart from './BacktestPriceChart';
import BacktestComparePanel from './BacktestComparePanel';
import StrategySuggestPanel from './StrategySuggestPanel';
import BacktestParityPanel from './BacktestParityPanel';
import BacktestReasoningPanel from './BacktestReasoningPanel';
import BacktestMlInsightsSection from './BacktestMlInsightsSection';
import BacktestAgentInsightsSection from './BacktestAgentInsightsSection';
import { getStrategyCategory } from '@/config/strategies';
import BacktestMetaLabelWalkForwardPanel from './BacktestMetaLabelWalkForwardPanel';
import BacktestAssumptionsStrip from './BacktestAssumptionsStrip';
import BacktestAssumptionsPanel from './BacktestAssumptionsPanel';
import BacktestBlockedLog from './BacktestBlockedLog';
import BacktestTradeExplain from './BacktestTradeExplain';
import BacktestReportTabs from './BacktestReportTabs';
import BacktestPerformanceSection from './BacktestPerformanceSection';
import BacktestMonteCarloChart from './BacktestMonteCarloChart';
import BacktestExcursionBar from './BacktestExcursionBar';
import BacktestSparkline from './BacktestSparkline';
import PortfolioContributionChart from './PortfolioContributionChart';
import CorrelationMatrix from './analytics/CorrelationMatrix';
import BacktestWalkForwardPanel from './BacktestWalkForwardPanel';
import { cn } from '@/lib/utils';
import { fetchBacktestTrades, fetchBacktestRun } from '../api/endpoints';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { buildDeployPayload, evaluateDeployGate } from '@/lib/deployGate';
import { useVirtualRows, VirtualTablePadding } from './VirtualTableBody';
import {
  backtestFingerprint,
  fmtBacktestRange,
  formatBacktestDaysChip,
  formatBacktestRangeLabel,
  formatBacktestTitle,
  isBacktestStale,
  normalizeTradingSymbol,
  notifyBacktestOverlayChanged,
  ensureBacktestChartHistory,
  resolveBacktestRange,
  symbolsMatch,
} from '@/lib/backtestDisplay';
import { exportBacktestPdf } from '@/lib/backtestExport';
import { exportBacktestManifest } from '@/lib/backtestManifest';

const EMPTY_TRADES = [];

function BacktestTable({ caption, children, className, onScroll, footer }) {
  return (
    <div
      className={cn('algo-backtest-table-scroll', className)}
      onScroll={onScroll}
    >
      {caption != null && caption !== '' && (
        <p className="algo-backtest-table-scroll__caption">{caption}</p>
      )}
      {children}
      {footer}
    </div>
  );
}

function TradeLogFilters({ value, onChange }) {
  return (
    <div className="algo-backtest-trade-filters" role="group" aria-label="Filter trades">
      {['all', 'entries', 'exits', 'SL', 'TP', 'SIGNAL'].map((f) => (
        <button
          key={f}
          type="button"
          className={cn(
            'algo-backtest-trade-filters__btn',
            value === f && 'algo-backtest-trade-filters__btn--active',
          )}
          onClick={() => onChange(f)}
        >
          {f}
        </button>
      ))}
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
  const rangeInfo = resolveBacktestRange(meta);
  const allocation = results?.allocation ?? results?.starting_equity ?? results?.starting_capital;
  const isPortfolio = Boolean(results?.portfolio || meta?.portfolio);
  const portfolioSymbols = meta?.portfolio_symbols;
  const symbolChip = isPortfolio
    ? (meta?.portfolio_label
      || (Array.isArray(portfolioSymbols)
        ? `Portfolio · ${portfolioSymbols.length}`
        : 'Portfolio'))
    : symbol;

  return (
    <div className="algo-backtest-lab__meta">
      <div className="algo-backtest-lab__meta-chips">
        <span className="algo-backtest-lab__chip algo-backtest-lab__chip--symbol">{symbolChip}</span>
        <span className="algo-backtest-lab__chip">{strategy}</span>
        <span
          className={cn(
            'algo-backtest-lab__chip',
            rangeInfo.hasMismatch && 'algo-backtest-lab__chip--warn',
          )}
          title={rangeInfo.hasMismatch ? formatBacktestRangeLabel(meta, { fallbackDays: backtestDays }) : undefined}
        >
          {formatBacktestDaysChip(meta, backtestDays)}
        </span>
        <span className="algo-backtest-lab__chip">{meta.timeframe ?? backtestTimeframe}</span>
        {allocation != null && (
          <span className="algo-backtest-lab__chip num-mono" title={isPortfolio ? 'Basket capital' : 'Max notional cap'}>
            cap ${Number(allocation).toLocaleString()}
          </span>
        )}
        {results?.risk_base != null && (
          <span className="algo-backtest-lab__chip num-mono" title="Risk base (1% sizing)">
            risk ${Number(results.risk_base).toLocaleString()}
          </span>
        )}
      </div>
      {(range || meta.resolution_note || meta.range_note || meta.timeframe_note) && (
        <div className="algo-backtest-lab__meta-secondary">
          {range && <span className="algo-backtest-lab__meta-range">{range}</span>}
          {(meta.resolution_note || meta.range_note || meta.timeframe_note) && (
            <span className="algo-backtest-lab__meta-note">
              {[meta.resolution_note, meta.range_note, meta.timeframe_note].filter(Boolean).join(' · ')}
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

function MonteCarloSection({ monteCarlo, startingEquity, isFull }) {
  if (!monteCarlo?.simulations) return null;
  if (isFull && monteCarlo.fan_bands?.length) {
    return (
      <BacktestMonteCarloChart
        monteCarlo={monteCarlo}
        startingEquity={startingEquity}
        className="backtest-mc-chart--lab mb-3"
      />
    );
  }
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

function PortfolioDeployBasket({ results, strategy, timeframe, totalAllocation, runId, days }) {
  const { botConfig } = useStore();
  const [deploying, setDeploying] = useState(false);
  const [forceDeploy, setForceDeploy] = useState(false);

  const rows = (results?.symbol_results || []).filter((r) => !r.error);
  if (!results?.portfolio || rows.length < 2) return null;

  const gate = evaluateDeployGate({
    results,
    config: botConfig,
    strategy,
    timeframe,
    days,
  });
  const canDeploy = gate.passed || forceDeploy;

  const handleDeployBasket = async () => {
    if (!canDeploy || deploying) return;
    setDeploying(true);
    let okCount = 0;
    let failCount = 0;
    const totalWeight = rows.reduce((sum, r) => sum + Number(r.weight || 1), 0) || rows.length;

    for (const row of rows) {
      const weight = Number(row.weight || 1) / totalWeight;
      const alloc = Math.max(100, Math.round((totalAllocation || botConfig?.allocation || 1000) * weight));
      const rowGate = evaluateDeployGate({
        results: {
          ...results,
          total_pnl: row.total_pnl,
          trade_count: row.trade_count,
          summary: row.summary,
        },
        symbol: row.symbol,
        strategy,
        timeframe,
        days,
        config: botConfig,
      });
      if (rowGate.blocking && !forceDeploy) {
        failCount += 1;
        continue;
      }
      const payload = buildDeployPayload({
        strategy: strategy || 'CHART_AGENT',
        symbol: row.symbol,
        timeframe: timeframe || '1m',
        allocation: alloc,
        executionMode: 'BAR_CLOSE',
        config: {
          ...botConfig,
          portfolio_deploy: true,
          portfolio_weight: weight,
        },
        results: { run_id: runId },
        days,
        forceDeploy,
      });
      const { ok } = await sendAction(Action.BOT_CREATE, payload);
      if (ok) okCount += 1;
      else failCount += 1;
    }

    setDeploying(false);
    if (okCount > 0) {
      toast.success(`Deployed ${okCount} portfolio bot${okCount === 1 ? '' : 's'}`);
    }
    if (failCount > 0) {
      toast.error(`${failCount} symbol${failCount === 1 ? '' : 's'} skipped or failed`);
    }
  };

  return (
    <div className="portfolio-deploy-basket mt-2">
      {gate.blocking && (
        <label className="deploy-gate__force mb-1.5">
          <input
            type="checkbox"
            checked={forceDeploy}
            onChange={(e) => setForceDeploy(e.target.checked)}
          />
          <span>Deploy basket anyway (bypass gate)</span>
        </label>
      )}
      <Button
        type="button"
        variant="buy"
        size="xs"
        disabled={!canDeploy || deploying}
        onClick={handleDeployBasket}
      >
        {deploying ? 'Deploying basket…' : `Deploy ${rows.length}-symbol basket`}
      </Button>
    </div>
  );
}

function PortfolioResultsSection({ results, strategy, timeframe, backtestDays, totalAllocation }) {
  const rows = results?.symbol_results;
  if (!results?.portfolio || !rows?.length) return null;
  const summary = results.summary ?? {};
  const pnl = results.total_pnl ?? summary.total_pnl ?? 0;
  const pnlTone = pnl >= 0 ? 'up' : 'down';
  const corr = results.correlation_summary;
  const hasCorrMatrix = Array.isArray(corr?.symbols)
    && corr.symbols.length >= 2
    && Array.isArray(corr?.matrix)
    && corr.matrix.length >= 2;
  const skipped = results.skipped_symbols ?? rows.filter((r) => r.error).map((r) => ({
    symbol: r.symbol,
    reason: r.error,
  }));

  return (
    <section className="algo-backtest-lab__section mb-3">
      <p className="algo-backtest-table-scroll__caption mb-1.5">
        Portfolio backtest — {results.symbols_tested} symbol{results.symbols_tested === 1 ? '' : 's'}
        {results.symbols_failed > 0 ? ` (${results.symbols_failed} skipped/failed)` : ''}
      </p>

      {skipped.length > 0 && (
        <Alert variant="default" className="mb-2 py-1.5 px-2">
          <AlertTriangle className="size-3.5" />
          <AlertDescription className="text-[0.58rem] leading-snug">
            Skipped: {skipped.map((s) => `${s.symbol} (${s.reason})`).join(' · ')}
          </AlertDescription>
        </Alert>
      )}

      <div className="algo-backtest-stat-grid algo-backtest-stat-grid--compact mb-2">
        <StatCard label="Aggregate PnL" value={`$${Number(pnl).toFixed(2)}`} tone={pnlTone} />
        <StatCard label="Total trades" value={String(results.trade_count ?? summary.total_trades ?? 0)} />
        <StatCard label="Win rate" value={`${Number(results.win_rate ?? summary.win_rate ?? 0).toFixed(1)}%`} />
        <StatCard label="Symbols OK" value={String(results.symbols_tested ?? 0)} />
      </div>

      {corr?.warning && (
        <Alert variant="destructive" className="mb-2 py-1.5 px-2">
          <AlertTriangle className="size-3.5" />
          <AlertDescription className="text-[0.58rem] leading-snug">
            Correlation note: {corr.message}
          </AlertDescription>
        </Alert>
      )}

      <div className="portfolio-bt-viz mb-2">
        <PortfolioContributionChart rows={rows} />
        {hasCorrMatrix && (
          <div className="portfolio-bt-corr">
            <p className="algo-backtest-table-scroll__caption mb-1">
              Basket correlation
              {corr.period || corr.lookback_days
                ? ` · ${corr.period || corr.lookback_days}`
                : ''}
              {corr.source ? ` · ${corr.source}` : ''}
            </p>
            <CorrelationMatrix
              correlation={corr}
              className="portfolio-bt-corr__matrix"
              profitColor="#22c55e"
              lossColor="#ef4444"
            />
          </div>
        )}
      </div>

      <div className="algo-backtest-table-scroll">
        <table className="terminal-table algo-backtest-table m-0 text-[0.58rem]">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Curve</th>
              <th className="text-right">PnL</th>
              <th className="text-right">Share</th>
              <th className="text-right">Trades</th>
              <th className="text-right">Win%</th>
              <th className="text-right">Sharpe</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.symbol}>
                <td>{row.symbol}</td>
                <td>
                  <BacktestSparkline
                    values={row.sparkline}
                    tone={(row.total_pnl ?? 0) >= 0 ? 'up' : 'down'}
                  />
                </td>
                <td className={cn(
                  'num-mono text-right',
                  row.error ? 'text-muted-foreground' : (row.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
                )}>
                  {row.error ? row.error : `$${Number(row.total_pnl).toFixed(2)}`}
                </td>
                <td className="num-mono text-right text-muted-foreground">
                  {row.error || row.pnl_contribution_pct == null
                    ? '—'
                    : `${Number(row.pnl_contribution_pct).toFixed(0)}%`}
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
      <PortfolioDeployBasket
        results={results}
        strategy={strategy}
        timeframe={timeframe}
        totalAllocation={totalAllocation}
        runId={results?.run_id}
        days={backtestDays}
      />
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
  strategyCategory = null,
}) {
  const setBacktestResults = useResearchStore((s) => s.setBacktestResults);
  const setBacktestOverlay = useResearchStore((s) => s.setBacktestOverlay);
  const setActiveSymbol = useStore((s) => s.setActiveSymbol);
  const backtestOverlay = useResearchStore((s) => s.backtestOverlay);
  const botConfig = useStore((s) => s.botConfig);
  const activeBots = useStore((s) => s.activeBots);
  const activeSymbol = useStore((s) => s.activeSymbol);
  const botStrategy = useStore((s) => s.botStrategy);
  const botTimeframe = useStore((s) => s.botTimeframe);

  const [fullTrades, setFullTrades] = useState(null);
  const [loadingTrades, setLoadingTrades] = useState(false);
  const [loadingRun, setLoadingRun] = useState(false);
  const [tradeReasonFilter, setTradeReasonFilter] = useState('all');
  const [focusBarTime, setFocusBarTime] = useState(null);
  const [selectedTrade, setSelectedTrade] = useState(null);
  const loadRef = useRef(0);

  const isFull = variant === 'full';
  const summary = results?.summary;
  const isPortfolio = Boolean(results?.portfolio || results?.meta?.portfolio);
  const tradesTotal = results?.trades_total ?? results?.trade_count ?? results?.trades?.length ?? 0;
  const previewTrades = results?.trades ?? EMPTY_TRADES;
  const resolvedCategory = strategyCategory
    ?? getStrategyCategory(strategy ?? results?.meta?.strategy);
  const isMlCategory = resolvedCategory === 'ml';
  const isAgentCategory = resolvedCategory === 'agent';
  const showAdvisor = !isMlCategory;

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
  const tradesTruncated = tradesTotal > previewTrades.length;
  const chartTrades = loadingTrades && tradesTruncated ? [] : displayTrades;

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

  const tableTrades = isFull ? allTrades : allTrades.slice(0, 10);

  const closedTrades = useMemo(
    () => displayTrades.filter(t => t.is_exit),
    [displayTrades],
  );

  const entryCount = useMemo(
    () => displayTrades.filter((t) => !t.is_exit).length,
    [displayTrades],
  );

  const reasoningRequested = Boolean(results?.meta?.reasoning || reasoningPending);

  const { onScroll: onTradeScroll, window: tradeWindow } = useVirtualRows(tableTrades, {
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

  const onExport = useCallback(async () => {
    let exportTrades = fullTrades ?? previewTrades;
    if (results?.run_id && (results.trades_total ?? 0) > exportTrades.length) {
      try {
        exportTrades = await fetchBacktestTrades(results.run_id);
      } catch {
        /* use preview trades */
      }
    }
    exportTradesCsv(
      exportTrades,
      symbol ?? results?.meta?.symbol ?? 'sym',
      strategy ?? results?.meta?.strategy ?? 'strategy',
    );
  }, [fullTrades, previewTrades, results, symbol, strategy]);

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
    setFocusBarTime(trade.time);
    setSelectedTrade(trade);
    window.dispatchEvent(new CustomEvent('backtest-focus-bar', {
      detail: { time: trade.time, symbol: results?.meta?.symbol ?? symbol },
    }));
  }, [results, symbol]);

  const onBlockedBarFocus = useCallback((time) => {
    if (time == null) return;
    setFocusBarTime(time);
    setSelectedTrade(null);
    window.dispatchEvent(new CustomEvent('backtest-focus-bar', {
      detail: { time, symbol: results?.meta?.symbol ?? symbol },
    }));
  }, [results, symbol]);

  const onExportManifest = useCallback(() => {
    const outcome = exportBacktestManifest({
      results,
      symbol: symbol ?? results?.meta?.symbol,
      strategy: strategy ?? results?.meta?.strategy,
      days: backtestDays,
      timeframe: backtestTimeframe ?? results?.meta?.timeframe,
      config: botConfig,
    });
    if (!outcome?.ok) {
      toast.error(outcome?.error || 'Could not export manifest');
      return;
    }
    toast.success(outcome.filename ? `Downloaded ${outcome.filename}` : 'Manifest downloaded');
  }, [results, symbol, strategy, backtestDays, backtestTimeframe, botConfig]);

  useEffect(() => {
    const handler = (ev) => {
      const t = ev?.detail?.time;
      if (t != null) setFocusBarTime(t);
    };
    window.addEventListener('backtest-focus-bar', handler);
    return () => window.removeEventListener('backtest-focus-bar', handler);
  }, []);

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
    (results.live_parity || results.meta?.live_parity) && {
      key: 'parity',
      label: 'Live parity',
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

      {results?.strategy_readiness && (
        (results.strategy_readiness.ok === false
          || (Array.isArray(results.strategy_readiness.warnings)
            && results.strategy_readiness.warnings.length > 0)
          || (Array.isArray(results.strategy_readiness.notes)
            && results.strategy_readiness.notes.length > 0
            && !(results.trade_count ?? results.summary?.total_trades)))
      ) && (
        <Alert
          variant={results.strategy_readiness.ok === false ? 'destructive' : 'default'}
          className="algo-backtest-stale-banner py-2 mb-2"
        >
          <AlertTriangle data-icon="inline-start" className="size-3.5" />
          <AlertDescription className="text-xs space-y-1">
            <p className="font-medium m-0">
              {results.strategy_readiness.status === 'no_signals'
                && 'Strategy produced no BUY/SELL signals'}
              {results.strategy_readiness.status === 'signals_blocked'
                && 'Signals fired but no trades filled'}
              {results.strategy_readiness.status === 'broken'
                && 'Strategy evaluate errors detected'}
              {results.strategy_readiness.status === 'ok'
                && 'Strategy tradeability note'}
            </p>
            {(results.strategy_readiness.warnings || []).map((w) => (
              <p key={w} className="m-0 text-muted-foreground">{w}</p>
            ))}
            {(results.strategy_readiness.notes || []).map((n) => (
              <p key={n} className="m-0 text-muted-foreground">{n}</p>
            ))}
            {results.strategy_readiness.signals && (
              <p className="m-0 num-mono text-[0.65rem] text-muted-foreground">
                Signals: BUY {results.strategy_readiness.signals.BUY ?? 0}
                {' · '}
                SELL {results.strategy_readiness.signals.SELL ?? 0}
                {' · '}
                NONE {results.strategy_readiness.signals.NONE ?? 0}
                {results.strategy_readiness.direction_mode
                  ? ` · dir ${results.strategy_readiness.direction_mode}`
                  : ''}
                {results.strategy_readiness.blocked_entries
                  ? ` · blocked ${results.strategy_readiness.blocked_entries}`
                  : ''}
                {results.strategy_readiness.top_block_reasons?.length
                  ? ` · Top block: ${results.strategy_readiness.top_block_reasons[0].reason}`
                  : results.strategy_readiness.top_block_kinds?.length
                    ? ` · Top block: ${results.strategy_readiness.top_block_kinds[0].kind} (${results.strategy_readiness.top_block_kinds[0].count})`
                    : ''}
                {results.strategy_readiness.top_reject_reasons?.length
                  ? ` · Top reject: ${results.strategy_readiness.top_reject_reasons[0].reason}`
                  : ''}
              </p>
            )}
          </AlertDescription>
        </Alert>
      )}

      <header className="algo-backtest-lab__head">
        <div className="algo-backtest-lab__title-block">
          <h3 className="algo-backtest-lab__title">
            <span className="algo-backtest-lab__title-main">
              {formatBacktestTitle(results.meta, {
                fallbackDays: backtestDays,
                fallbackTimeframe: backtestTimeframe,
              })}
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
                {tag.icon && <tag.icon data-icon="inline-start" aria-hidden />}
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
          <Button
            type="button"
            variant="ghost"
            size="xs"
            className="h-6 text-[0.62rem]"
            onClick={onExportManifest}
            title="Download JSON reproducibility manifest"
          >
            <Download data-icon="inline-start" />
            Manifest
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
      {isFull ? (
        <BacktestReportTabs
          overview={(
            <>
              <BacktestSummaryCards summary={summary} results={results} isFull={false} />
              {isMlCategory && (
                <BacktestMlInsightsSection
                  results={results}
                  strategy={strategy ?? results?.meta?.strategy}
                />
              )}
              {isAgentCategory && (
                <>
                  <BacktestAgentInsightsSection results={results} />
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
                </>
              )}
              <section className="algo-backtest-lab__section algo-backtest-lab__section--chart">
                {!isPortfolio && (
                  <BacktestPriceChart
                    symbol={symbol ?? results?.meta?.symbol}
                    meta={results?.meta}
                    timeframe={backtestTimeframe ?? results?.meta?.timeframe ?? '1m'}
                    trades={chartTrades}
                    tradesLoading={loadingTrades}
                    tradesTotal={tradesTotal}
                    highlightBarTime={focusBarTime}
                    className="backtest-price-chart-wrap--lab"
                  />
                )}
                <BacktestEquityChart
                  equityCurve={results.equity_curve}
                  drawdownCurve={results.drawdown_curve}
                  totalPnl={results.total_pnl}
                  trades={chartTrades}
                  benchmarkOverlays={results?.benchmark_overlays ?? summary?.benchmark_overlays}
                  className="backtest-mini-chart--lab"
                  variant="lab"
                />
                {!isPortfolio && (
                  <BacktestTradeExplain
                    trade={selectedTrade}
                    strategy={strategy ?? results?.meta?.strategy}
                  />
                )}
              </section>
              <PortfolioResultsSection
                results={results}
                strategy={strategy ?? results?.meta?.strategy}
                timeframe={backtestTimeframe ?? results?.meta?.timeframe}
                backtestDays={backtestDays}
                totalAllocation={botConfig?.allocation}
              />
              {!isPortfolio && (
              <BacktestRegimeSection
                regime={results?.regime ?? summary?.regime}
                benchmarkOverlays={results?.benchmark_overlays ?? summary?.benchmark_overlays}
              />
              )}
              {!isPortfolio && (
              <BacktestWalkForwardPanel
                walkForward={results?.walk_forward}
                symbol={symbol ?? results?.meta?.symbol}
                strategy={strategy ?? results?.meta?.strategy}
                timeframe={backtestTimeframe ?? results?.meta?.timeframe}
                allocation={results?.allocation ?? results?.starting_equity ?? botConfig?.allocation}
                runId={results?.run_id}
                results={results}
                days={backtestDays}
              />
              )}
              <div className="algo-backtest-lab__tools-grid">
                <BacktestComparePanel
                  currentRun={{ run_id: results.run_id, results }}
                  recentRuns={recentRuns}
                />
              </div>
            </>
          )}
          performance={(
            <>
              {isPortfolio && summary?.trades_sampled && (
                <p className="algo-field-hint mb-2 text-muted-foreground">
                  Performance stats use a capped trade sample
                  {summary.trades_sample_size != null
                    ? ` (${summary.trades_sample_size} of ${tradesTotal} fills)`
                    : ''}
                  {' '}plus combined equity for Sharpe / drawdown.
                </p>
              )}
              <BacktestPerformanceSection summary={summary} results={results} />
              {!isPortfolio && (
                <>
              <MonteCarloSection
                monteCarlo={results?.monte_carlo}
                startingEquity={results?.starting_equity ?? results?.allocation}
                isFull
              />
              <FilterRejectsSection summary={summary} />
              <BacktestBlockedLog
                results={results}
                onFocusBar={onBlockedBarFocus}
              />
              <BacktestParityPanel results={results} symbol={symbol} strategy={strategy} />
              <BacktestMetaLabelWalkForwardPanel walkForward={results?.meta_label_walk_forward} />
                </>
              )}
            </>
          )}
          trades={(
            allTrades.length > 0 ? (
              <section className="algo-backtest-lab__section algo-backtest-lab__section--trades">
                <header className="algo-backtest-trade-log__header">
                  <div className="algo-backtest-trade-log__title-row">
                    <p className="algo-backtest-table-scroll__caption m-0">
                      {isPortfolio
                        ? `Trade sample (${allTrades.length}${tradesTotal > allTrades.length ? ` of ${tradesTotal}` : ''} fills)`
                        : `Trade log (${tradesTotal} fills)`}
                    </p>
                    {loadingTrades && (
                      <Loader2 className="size-3 animate-spin text-muted-foreground" aria-hidden />
                    )}
                  </div>
                  <TradeLogFilters value={tradeReasonFilter} onChange={setTradeReasonFilter} />
                </header>
                <BacktestTable
                  className="algo-backtest-table-scroll--trades algo-backtest-table-scroll--trades-full"
                  onScroll={onTradeScroll}
                >
                  <table className="terminal-table algo-backtest-table m-0">
                    <thead>
                      <tr>
                        <th>Time</th>
                        {isPortfolio && <th>Symbol</th>}
                        <th>Type</th>
                        <th>Side</th>
                        <th className="text-right">Qty</th>
                        <th className="text-right">Price</th>
                        <th className="text-right">PnL</th>
                        <th>Excursion</th>
                        <th>Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      <VirtualTablePadding height={tradeWindow.topPad} colSpan={isPortfolio ? 9 : 8} />
                      {tradeWindow.slice.map((t, i) => (
                        <tr
                          key={`${t.symbol || ''}-${t.time}-${t.side}-${tradeWindow.start + i}`}
                          className={cn(
                            'cursor-pointer hover:bg-muted/30',
                            focusBarTime === t.time && 'bg-primary/10',
                          )}
                          onClick={() => onTradeRowClick(t)}
                          title="Focus chart on this bar"
                        >
                          <td className="text-muted-foreground whitespace-nowrap">{fmtTime(t.time)}</td>
                          {isPortfolio && (
                            <td className="whitespace-nowrap">{t.symbol || '—'}</td>
                          )}
                          <td className="whitespace-nowrap">
                            <span className={cn(
                              'algo-backtest-trade-type',
                              t.is_exit ? 'algo-backtest-trade-type--exit' : 'algo-backtest-trade-type--entry',
                            )}
                            >
                              {t.is_exit ? 'Exit' : 'Entry'}
                            </span>
                          </td>
                          <td className="whitespace-nowrap">{t.side}</td>
                          <td className="num-mono text-right whitespace-nowrap">{Number(t.quantity).toFixed(4)}</td>
                          <td className="num-mono text-right whitespace-nowrap">{Number(t.price).toFixed(2)}</td>
                          <td className={cn(
                            'num-mono text-right whitespace-nowrap',
                            t.pnl != null && (t.pnl >= 0 ? 'text-trading-up' : 'text-trading-down'),
                          )}>
                            {t.pnl != null ? `$${Number(t.pnl).toFixed(2)}` : '—'}
                          </td>
                          <td className="w-[4.5rem]">
                            {t.is_exit ? (
                              <BacktestExcursionBar mfePct={t.mfe_pct} maePct={t.mae_pct} pnl={t.pnl} />
                            ) : '—'}
                          </td>
                          <td className="text-muted-foreground max-w-[10rem] truncate" title={t.reason ?? (t.is_exit ? 'EXIT' : 'ENTRY')}>
                            {t.reason ?? (t.is_exit ? 'EXIT' : 'ENTRY')}
                          </td>
                        </tr>
                      ))}
                      <VirtualTablePadding height={tradeWindow.bottomPad} colSpan={isPortfolio ? 9 : 8} />
                    </tbody>
                  </table>
                </BacktestTable>
                {!isPortfolio && (
                  <BacktestTradeExplain
                    trade={selectedTrade}
                    strategy={strategy ?? results?.meta?.strategy}
                  />
                )}
              </section>
            ) : (
              <p className="text-xs text-muted-foreground px-1">
                {isPortfolio
                  ? 'No trade sample in this portfolio run (symbols may have been skipped or had zero fills).'
                  : 'No trades in this run.'}
              </p>
            )
          )}
          properties={(
            <>
              <BacktestAssumptionsStrip results={results} />
              <BacktestAssumptionsPanel results={results} />
              <section className="algo-backtest-lab__section algo-backtest-lab__section--audit">
                <div className="algo-backtest-section__head">
                  <p className="algo-backtest-section__title m-0">Run audit</p>
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    className="h-6 text-[0.62rem]"
                    onClick={onExportManifest}
                  >
                    <Download data-icon="inline-start" />
                    JSON manifest
                  </Button>
                </div>
                <BacktestTable className="algo-backtest-table-scroll--properties">
                  <table className="terminal-table algo-backtest-table algo-backtest-audit-table m-0">
                    <thead>
                      <tr>
                        <th>Property</th>
                        <th>Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[
                        ['Run ID', <span className="num-mono" key="run">{results.run_id ?? '—'}</span>],
                        [
                          isPortfolio ? 'Basket' : 'Symbol',
                          isPortfolio
                            ? (results?.meta?.portfolio_label
                              || (Array.isArray(results?.meta?.portfolio_symbols)
                                ? results.meta.portfolio_symbols.join(', ')
                                : 'Portfolio'))
                            : (symbol ?? results?.meta?.symbol ?? '—'),
                        ],
                        ['Strategy', strategy ?? results?.meta?.strategy ?? '—'],
                        ['Days', (
                          <span className="num-mono" key="days" title={
                            resolveBacktestRange(results?.meta).hasMismatch
                              ? `Requested ${results?.meta?.days_requested ?? backtestDays}d`
                              : undefined
                          }>
                            {formatBacktestRangeLabel(results?.meta ?? {}, { fallbackDays: backtestDays })}
                          </span>
                        )],
                        ['Timeframe', results?.meta?.timeframe ?? backtestTimeframe],
                        ['Sim mode', results.sim_mode ?? results?.meta?.sim_mode ?? 'live_aligned'],
                        ['Fees', <span className="num-mono" key="fees">${Number(summary?.total_fees ?? 0).toFixed(2)}</span>],
                        ['Slippage', <span className="num-mono" key="slip">{summary?.slippage_bps ?? results?.costs?.slippage_bps ?? 0} bps</span>],
                      ].map(([label, value]) => (
                        <tr key={String(label)}>
                          <td className="text-muted-foreground">{label}</td>
                          <td>{value}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </BacktestTable>
              </section>
              {showAdvisor && (
              <section className="algo-backtest-lab__section algo-backtest-lab__section--advisor">
                <StrategySuggestPanel
                  botId={advisorBotId}
                  candidateBots={activeBots}
                  backtestDays={backtestDays}
                  recentResults={results}
                  agentLlmAvailable={agentLlmAvailable}
                  symbol={symbol ?? results?.meta?.symbol}
                  compact={false}
                />
              </section>
              )}
              {recentRuns.length > 0 && (
                <section className="algo-backtest-lab__section algo-backtest-lab__section--history">
                  <BacktestTable
                    caption="Saved runs (same symbol)"
                    className="algo-backtest-table-scroll--history algo-backtest-table-scroll--history-full"
                  >
                    <table className="terminal-table algo-backtest-table m-0">
                      <thead>
                        <tr>
                          <th>When</th>
                          <th>Strategy</th>
                          <th>Days</th>
                          <th className="text-right">PnL</th>
                          <th className="text-right">Win%</th>
                          <th>Regime</th>
                          <th className="text-right">PF</th>
                        </tr>
                      </thead>
                      <tbody>
                        {recentRuns.slice(0, 15).map((run) => (
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
                            <td className="capitalize text-muted-foreground whitespace-nowrap">
                              {run.summary?.regime?.dominant_regime ?? run.results?.regime?.dominant_regime ?? '—'}
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
              {(showReasoningSection || reasoningRequested || results?.reasoning) && !isAgentCategory && (
                <BacktestReasoningPanel
                  reasoning={results.reasoning}
                  reasoningRequested={reasoningRequested}
                  entryCount={entryCount}
                  tradeLog={fullTrades ?? previewTrades}
                  results={results}
                  className="mt-2"
                />
              )}
            </>
          )}
        />
      ) : (
        <>
      <BacktestAssumptionsStrip results={results} className="mb-2" />
      <BacktestSummaryCards summary={summary} results={results} isFull={false} />
      <PortfolioResultsSection
        results={results}
        strategy={strategy ?? results?.meta?.strategy}
        timeframe={backtestTimeframe ?? results?.meta?.timeframe}
        backtestDays={backtestDays}
        totalAllocation={botConfig?.allocation}
      />
      {isMlCategory && (
        <BacktestMlInsightsSection
          results={results}
          strategy={strategy ?? results?.meta?.strategy}
          compact
        />
      )}
      {isAgentCategory && (
        <BacktestAgentInsightsSection results={results} compact />
      )}

      {showAdvisor && (
      <section className="algo-backtest-lab__section algo-backtest-lab__section--advisor">
        <StrategySuggestPanel
          botId={advisorBotId}
          candidateBots={activeBots}
          backtestDays={backtestDays}
          recentResults={results}
          agentLlmAvailable={agentLlmAvailable}
          symbol={symbol ?? results?.meta?.symbol}
          compact
        />
      </section>
      )}

      {results?.sweep?.results?.length > 0 && (
        <button
          type="button"
          className="algo-backtest-sweep-teaser text-[0.62rem] text-primary hover:underline text-left px-1 py-1"
          onClick={() => useResearchStore.getState().openBacktestLab('optimizer')}
        >
          {results.sweep.configs_tested ?? results.sweep.results.length} configs tested → Open optimizer
        </button>
      )}

      <section className="algo-backtest-lab__section algo-backtest-lab__section--chart">
        <BacktestEquityChart
          equityCurve={results.equity_curve}
          drawdownCurve={results.drawdown_curve}
          totalPnl={results.total_pnl}
          trades={displayTrades}
          benchmarkOverlays={results?.benchmark_overlays ?? summary?.benchmark_overlays}
        />
      </section>

      {tableTrades.length > 0 && (
        <section className="algo-backtest-lab__section algo-backtest-lab__section--trades">
        <header className="algo-backtest-trade-log__header">
          <div className="algo-backtest-trade-log__title-row">
            <p className="algo-backtest-table-scroll__caption m-0">
              {`Trade preview (${Math.min(10, tradesTotal)} of ${tradesTotal})`}
            </p>
            {loadingTrades && <Loader2 className="size-3 animate-spin text-muted-foreground" aria-hidden />}
          </div>
        </header>
        <BacktestTable
          className="algo-backtest-table-scroll--trades"
          footer={tradesTotal > 10 ? (
            <p className="algo-backtest-table-scroll__footer">
              Open Lab for full trade log, excursion bars, and charts.
            </p>
          ) : null}
        >
          <table className="terminal-table algo-backtest-table m-0">
            <thead>
              <tr>
                <th>Time</th>
                {isPortfolio && <th>Symbol</th>}
                <th>Type</th>
                <th>Side</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Price</th>
                <th className="text-right">PnL</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {tableTrades.map((t, i) => (
                <tr
                  key={`${t.symbol || ''}-${t.time}-${t.side}-${i}`}
                  className="cursor-pointer hover:bg-muted/30"
                  onClick={() => onTradeRowClick(t)}
                  title="Focus chart on this bar"
                >
                  <td className="text-muted-foreground whitespace-nowrap">{fmtTime(t.time)}</td>
                  {isPortfolio && (
                    <td className="whitespace-nowrap">{t.symbol || '—'}</td>
                  )}
                  <td className="whitespace-nowrap">
                    <span className={cn(
                      'algo-backtest-trade-type',
                      t.is_exit ? 'algo-backtest-trade-type--exit' : 'algo-backtest-trade-type--entry',
                    )}
                    >
                      {t.is_exit ? 'Exit' : 'Entry'}
                    </span>
                  </td>
                  <td className="whitespace-nowrap">{t.side}</td>
                  <td className="num-mono text-right whitespace-nowrap">{Number(t.quantity).toFixed(4)}</td>
                  <td className="num-mono text-right whitespace-nowrap">{Number(t.price).toFixed(2)}</td>
                  <td className={cn(
                    'num-mono text-right whitespace-nowrap',
                    t.pnl != null && (t.pnl >= 0 ? 'text-trading-up' : 'text-trading-down'),
                  )}>
                    {t.pnl != null ? `$${Number(t.pnl).toFixed(2)}` : '—'}
                  </td>
                  <td className="text-muted-foreground max-w-[8rem] truncate">{t.reason ?? (t.is_exit ? 'EXIT' : 'ENTRY')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </BacktestTable>
        </section>
      )}
        </>
      )}

      </div>
    </div>
  );
}
