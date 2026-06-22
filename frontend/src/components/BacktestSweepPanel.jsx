/**
 * Parameter sweep + walk-forward controls with strategy-aware param grid.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Separator } from '@/components/ui/separator';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { withLlmModel } from '../api/endpoints';
import { getSweepEligibleFields } from '../lib/botConfigDisplay';
import { exportSweepCsv } from '../lib/backtestExport';
import OptimizerHeatmap from './OptimizerHeatmap';
import OptimizationHistory from './OptimizationHistory';
import BacktestWalkForwardPanel from './BacktestWalkForwardPanel';
import FilterRejectsDashboard from './FilterRejectsDashboard';
import {
  scheduleBacktestClientTimeout,
  clearBacktestClientTimeout,
  formatBacktestTimeoutLabel,
  getBacktestClientTimeoutMs,
} from '../lib/backtestTimeouts';
import { toast } from 'sonner';

const OBJECTIVE_OPTIONS = [
  { value: 'total_pnl', label: 'Total PnL' },
  { value: 'sharpe_ratio', label: 'Sharpe ratio' },
  { value: 'profit_factor', label: 'Profit factor' },
  { value: 'sortino_ratio', label: 'Sortino ratio' },
  { value: 'calmar_ratio', label: 'Calmar ratio' },
  { value: 'max_drawdown_penalty', label: 'PnL − DD penalty' },
];

function parseSweepValues(text, kind) {
  if (!text || !String(text).trim()) return [];
  return String(text)
    .split(/[,;\s]+/)
    .map((v) => v.trim())
    .filter(Boolean)
    .map((v) => {
      if (kind === 'boolean') {
        if (v.toLowerCase() === 'true' || v === '1') return true;
        if (v.toLowerCase() === 'false' || v === '0') return false;
        return v;
      }
      const n = Number(v);
      return Number.isNaN(n) ? v : n;
    });
}

function buildSweepGrid(paramDefs, enabled, valuesByKey, maxCombos, objective, minTrades, sweepMode) {
  const sweep = {
    max_combos: maxCombos,
    sweep_objective: objective,
    min_trades: minTrades,
    sweep_mode: sweepMode,
  };
  for (const def of paramDefs) {
    if (!enabled[def.key]) continue;
    const vals = parseSweepValues(valuesByKey[def.key], def.kind);
    if (vals.length) sweep[def.key] = vals;
  }
  const paramKeys = Object.keys(sweep).filter(
    (k) => !['max_combos', 'sweep_objective', 'min_trades', 'sweep_mode'].includes(k),
  );
  return paramKeys.length > 0 ? sweep : null;
}

const BARS_PER_DAY_24H = { '1m': 1440, '5m': 288, '15m': 96, '1h': 24, '4h': 6, '1d': 1 };
const BARS_PER_DAY_EQUITY = { '1m': 390, '5m': 78, '15m': 26, '1h': 7, '4h': 2, '1d': 1 };
const WALK_FORWARD_MIN_BARS = 100;

function isCryptoSymbol(symbol) {
  const s = String(symbol || '').toUpperCase();
  return s.includes('USDT') || s.endsWith('USD');
}

function estimateMaxBars(days, timeframe, symbol) {
  const d = parseInt(days, 10) || 0;
  const tf = String(timeframe || '1m').toLowerCase();
  if (isCryptoSymbol(symbol)) {
    return d * (BARS_PER_DAY_24H[tf] ?? 1440);
  }
  const tradingDays = d * (5 / 7);
  return tradingDays * (BARS_PER_DAY_EQUITY[tf] ?? 390);
}

function countCombos(sweep, paramDefs) {
  if (!sweep) return 0;
  let fullGrid = 1;
  for (const def of paramDefs) {
    const vals = sweep[def.key];
    if (Array.isArray(vals) && vals.length) fullGrid *= vals.length;
  }
  const mode = sweep.sweep_mode || 'grid';
  const cap = mode === 'grid' ? Math.min(sweep.max_combos ?? 24, 24) : Math.min(sweep.max_combos ?? 24, 100);
  if (mode === 'grid') return Math.min(fullGrid, cap);
  return cap;
}

function estimateFullGrid(sweep, paramDefs) {
  if (!sweep) return 0;
  let n = 1;
  for (const def of paramDefs) {
    const vals = sweep[def.key];
    if (Array.isArray(vals) && vals.length) n *= vals.length;
  }
  return n;
}

function defaultEnabledKeys(strategy) {
  const strat = (strategy || '').toUpperCase();
  const base = {
    trailing_stop_percent: true,
    take_profit_percent: true,
    stop_loss_percent: false,
    allocation: false,
    slippage_bps: false,
    fee_bps: false,
  };
  if (strat === 'CHART_AGENT') {
    return { ...base, min_confidence: false, min_score: false };
  }
  return base;
}

function defaultValuesForFields(fields, botConfig) {
  const values = {};
  for (const def of fields) {
    const current = botConfig?.[def.key];
    if (current != null && current !== '') {
      values[def.key] = String(current);
    } else {
      values[def.key] = def.placeholder ?? '';
    }
  }
  return values;
}

function metricHeader(objective) {
  if (objective === 'sharpe_ratio') return 'Sharpe';
  if (objective === 'profit_factor') return 'PF';
  return 'PnL';
}

function formatMetric(row, objective) {
  const summary = row.summary ?? {};
  if (objective === 'sharpe_ratio') {
    const v = summary.sharpe_ratio;
    return v != null ? Number(v).toFixed(2) : '—';
  }
  if (objective === 'profit_factor') {
    const v = summary.profit_factor;
    return v != null ? Number(v).toFixed(2) : '—';
  }
  if (row.error) return '—';
  return `$${Number(row.total_pnl ?? 0).toFixed(2)}`;
}

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
  const setPendingDeploy = useStore((s) => s.setPendingDeploy);
  const agentLlmAvailable = useStore((s) => s.agentLlmAvailable);

  const paramDefs = useMemo(
    () => getSweepEligibleFields(strategy, botConfig),
    [strategy, botConfig],
  );

  const [enabled, setEnabled] = useState(() => defaultEnabledKeys(strategy));
  const [valuesByKey, setValuesByKey] = useState(() =>
    defaultValuesForFields(getSweepEligibleFields(strategy, botConfig), botConfig),
  );
  const [maxCombos, setMaxCombos] = useState(24);
  const [sweepMode, setSweepMode] = useState('grid');
  const [objective, setObjective] = useState('total_pnl');
  const [minTrades, setMinTrades] = useState(1);
  const [reasoning, setReasoning] = useState(false);
  const [rollingWf, setRollingWf] = useState(false);
  const [rollingFolds, setRollingFolds] = useState(3);
  const [autoDeploy, setAutoDeploy] = useState(false);
  const [autoDeployMinOosPnl, setAutoDeployMinOosPnl] = useState('0');

  useEffect(() => {
    setEnabled((prev) => ({ ...defaultEnabledKeys(strategy), ...prev }));
    setValuesByKey((prev) => ({
      ...defaultValuesForFields(paramDefs, botConfig),
      ...prev,
    }));
  }, [strategy]);

  const sweep = results?.sweep;
  const activeObjective = sweep?.objective ?? results?.meta?.sweep_objective ?? objective;

  const aggregatedFilterRejects = useMemo(() => {
    const rows = sweep?.results ?? [];
    if (!rows.length) return null;
    const byBucket = {};
    let total = 0;
    for (const row of rows) {
      const fr = row.filter_rejects ?? row.summary?.filter_rejects;
      if (!fr) continue;
      for (const [key, count] of Object.entries(fr)) {
        const n = Number(count) || 0;
        if (n <= 0) continue;
        byBucket[key] = (byBucket[key] || 0) + n;
        total += n;
      }
    }
    return total > 0 ? { rejects: byBucket, total, runs: rows.length } : null;
  }, [sweep?.results]);

  const sweepGrid = useMemo(
    () => buildSweepGrid(paramDefs, enabled, valuesByKey, maxCombos, objective, minTrades, sweepMode),
    [paramDefs, enabled, valuesByKey, maxCombos, objective, minTrades, sweepMode],
  );
  const comboCount = useMemo(() => countCombos(sweepGrid, paramDefs), [sweepGrid, paramDefs]);
  const fullGridSize = useMemo(() => estimateFullGrid(sweepGrid, paramDefs), [sweepGrid, paramDefs]);
  const comboTruncated = sweepMode === 'grid'
    ? fullGridSize > comboCount
    : fullGridSize > comboCount && sweepMode !== 'random';
  const walkForwardTooFewBars = useMemo(
    () => estimateMaxBars(days, timeframe, symbol) < WALK_FORWARD_MIN_BARS,
    [days, timeframe, symbol],
  );

  const runSweep = async (walkForward = false) => {
    if (!sweepGrid) {
      toast.error('Enable at least one parameter with values');
      return;
    }
    if (walkForward && walkForwardTooFewBars) {
      toast.error(`Walk-forward needs ~${WALK_FORWARD_MIN_BARS}+ bars — increase days or lower the timeframe`);
      return;
    }
    if (backtestRunning) return;
    useStore.getState().setBacktestRunning(true);
    useStore.getState().setBacktestProgress({
      pct: 0,
      phase: 'sweep',
      message: walkForward ? 'Starting walk-forward…' : 'Starting sweep…',
    });
    const parsedDays = parseInt(days, 10) || 7;
    const timeoutMs = getBacktestClientTimeoutMs({ reasoning, days: parsedDays })
      * Math.max(1, Math.min(comboCount, 12));
    scheduleBacktestClientTimeout({
      reasoning,
      days: parsedDays,
      timeoutMs,
      onTimeout: (elapsedMs) => {
        if (!useStore.getState().backtestRunning) return;
        useStore.getState().setBacktestRunning(false);
        useStore.getState().setBacktestProgress(null);
        toast.error(`Sweep timed out after ${formatBacktestTimeoutLabel(elapsedMs)}`);
      },
    });
    const { ok, error } = await sendAction(Action.RUN_BACKTEST_SWEEP, withLlmModel({
      symbol,
      strategy,
      config: botConfig,
      days: parseInt(days, 10) || 7,
      timeframe,
      oos_pct: oosPct || undefined,
      walk_forward: walkForward || undefined,
      rolling_folds: walkForward ? (rollingWf ? rollingFolds : 1) : undefined,
      train_pct: walkForward ? 70 : undefined,
      sweep: sweepGrid,
      sweep_objective: objective,
      min_trades: minTrades,
      reasoning: reasoning || undefined,
      auto_deploy: walkForward && autoDeploy ? true : undefined,
      auto_deploy_allocation: botConfig?.allocation ?? 1000,
      auto_deploy_min_oos_pnl: parseFloat(autoDeployMinOosPnl) || 0,
      auto_deploy_min_oos_trades: minTrades,
      auto_deploy_skip_existing: true,
    }));
    if (!ok && error) toast.error(error);
    if (!ok) {
      clearBacktestClientTimeout();
      useStore.getState().setBacktestRunning(false);
      useStore.getState().setBacktestProgress(null);
    }
  };

  const applyConfig = (cfg) => {
    if (!cfg) return;
    updateBotConfig(cfg);
    toast.success('Applied sweep winner to deploy settings');
  };

  const deployOptimized = () => {
    const cfg = results?.walk_forward?.best_config ?? results?.sweep?.best_config;
    if (!cfg) {
      toast.error('No optimized config available — run a sweep first');
      return;
    }
    updateBotConfig(cfg);
    setPendingDeploy(true);
    toast.success('Optimized params applied — confirm deploy');
  };

  const handleExportCsv = () => {
    const res = exportSweepCsv({
      results,
      symbol,
      strategy,
      objective: activeObjective,
    });
    if (!res.ok) toast.error(res.error);
  };

  const bestConfig = results?.walk_forward?.best_config ?? results?.sweep?.best_config;
  const isChartAgent = (strategy || '').toUpperCase() === 'CHART_AGENT';

  return (
    <div className="algo-backtest-sweep">
      <div className="algo-backtest-sweep__header">
        <span className="algo-backtest-table-scroll__caption m-0">Parameter sweep</span>
        <div className="flex flex-wrap gap-1.5">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={backtestRunning || !sweepGrid}
            onClick={() => runSweep(false)}
          >
            Run sweep ({comboCount})
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={backtestRunning || !sweepGrid || walkForwardTooFewBars}
            onClick={() => runSweep(true)}
            title={
              walkForwardTooFewBars
                ? `Need ~${WALK_FORWARD_MIN_BARS}+ bars for a 70/30 split — increase days or use a lower timeframe`
                : rollingWf
                  ? `Rolling walk-forward (${rollingFolds} folds) — optimize IS, validate OOS per slice`
                  : 'Optimize on first 70% of bars, validate on last 30%'
            }
          >
            {rollingWf ? `Rolling WF (${rollingFolds})` : 'Walk-forward'}
          </Button>
          {sweep?.results?.length > 0 && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleExportCsv}
            >
              Export CSV
            </Button>
          )}
        </div>
      </div>

      <div className="algo-backtest-sweep__controls">
        <div className="algo-backtest-sweep__control">
          <Label className="algo-backtest-sweep__control-label">Objective</Label>
          <Select value={objective} onValueChange={setObjective}>
            <SelectTrigger size="sm" className="w-[9rem]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent position="popper">
              {OBJECTIVE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value} className="text-xs">
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="algo-backtest-sweep__control">
          <Label className="algo-backtest-sweep__control-label">Mode</Label>
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            value={sweepMode}
            onValueChange={(v) => {
              if (!v) return;
              setSweepMode(v);
              if (v !== 'grid' && maxCombos > 100) setMaxCombos(100);
            }}
          >
            <ToggleGroupItem value="grid" className="text-xs" title="Full grid, capped at 24 runs">Grid</ToggleGroupItem>
            <ToggleGroupItem value="random" className="text-xs" title="Random search, up to 100 runs">Random</ToggleGroupItem>
            <ToggleGroupItem value="lhs" className="text-xs" title="Latin hypercube sampling, up to 100 runs">LHS</ToggleGroupItem>
          </ToggleGroup>
        </div>
        <div className="algo-backtest-sweep__control">
          <Label className="algo-backtest-sweep__control-label">Min trades</Label>
          <Input
            type="number"
            min={0}
            max={999}
            className="h-7 w-16 text-xs"
            value={minTrades}
            onChange={(e) => setMinTrades(Math.max(0, parseInt(e.target.value, 10) || 0))}
          />
        </div>
        <div className="algo-backtest-sweep__control">
          <Label className="algo-backtest-sweep__control-label">Max combos</Label>
          <Input
            type="number"
            min={1}
            max={sweepMode === 'grid' ? 24 : 100}
            className="h-7 w-16 text-xs"
            value={maxCombos}
            onChange={(e) => {
              const cap = sweepMode === 'grid' ? 24 : 100;
              setMaxCombos(Math.max(1, Math.min(cap, parseInt(e.target.value, 10) || 1)));
            }}
          />
        </div>
        <span className="algo-backtest-sweep__summary">
          {comboCount} run{comboCount === 1 ? '' : 's'}
          {fullGridSize > 1 && ` · full grid ${fullGridSize}`}
          {comboTruncated && (
            <span className="text-trading-warn"> · truncated to {comboCount}</span>
          )}
        </span>
      </div>

      {isChartAgent && (
        <Alert className="py-1.5">
          <AlertDescription className="text-xs leading-snug">
            Chart Agent sweeps replay rules-only signals — LLM analysis is not varied during optimization.
          </AlertDescription>
        </Alert>
      )}

      <Separator className="my-1" />

      <div className="algo-backtest-sweep__grid flex flex-col gap-2">
        {paramDefs.map((def) => (
          <div key={def.key} className="algo-backtest-sweep__row flex items-center gap-2">
            <Checkbox
              id={`sweep-${def.key}`}
              checked={Boolean(enabled[def.key])}
              onCheckedChange={(c) => setEnabled((prev) => ({ ...prev, [def.key]: c === true }))}
            />
            <Label
              htmlFor={`sweep-${def.key}`}
              className="w-28 shrink-0 cursor-pointer text-xs font-normal text-muted-foreground"
            >
              {def.label}
            </Label>
            <Input
              className="h-8 min-w-[8rem] flex-1 text-xs"
              placeholder={def.placeholder}
              value={valuesByKey[def.key] ?? ''}
              disabled={!enabled[def.key]}
              onChange={(e) => setValuesByKey((prev) => ({ ...prev, [def.key]: e.target.value }))}
            />
          </div>
        ))}
        {enabled.trailing_stop_percent && enabled.stop_loss_percent && (
          <Alert className="border-trading-warn/40 bg-trading-warn/10 py-1.5">
            <AlertDescription className="text-xs leading-snug text-trading-warn">
              Stop loss is only used as a fallback when trailing stop is 0 — sweeping both may produce
              duplicate-behaving configs.
            </AlertDescription>
          </Alert>
        )}
        {agentLlmAvailable && (
          <div className="flex items-center gap-2">
            <Checkbox
              id="sweep-reasoning"
              checked={reasoning}
              onCheckedChange={(c) => setReasoning(c === true)}
            />
            <Label htmlFor="sweep-reasoning" className="cursor-pointer text-xs font-normal text-muted-foreground">
              Generate trade explanations after backtest (local LLM, post-hoc only)
            </Label>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex items-center gap-2">
            <Checkbox
              id="sweep-rolling"
              checked={rollingWf}
              onCheckedChange={(c) => setRollingWf(c === true)}
            />
            <Label htmlFor="sweep-rolling" className="cursor-pointer text-xs font-normal text-muted-foreground">
              Rolling walk-forward
            </Label>
          </div>
          {rollingWf && (
            <div className="flex items-center gap-2">
              <Label className="shrink-0 text-xs text-muted-foreground">Folds</Label>
              <Select
                value={String(rollingFolds)}
                onValueChange={(v) => setRollingFolds(parseInt(v, 10) || 3)}
              >
                <SelectTrigger size="sm" className="w-[4.5rem]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent position="popper">
                  {[2, 3, 4, 5].map((n) => (
                    <SelectItem key={n} value={String(n)} className="text-xs">{n}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Checkbox
              id="sweep-auto-deploy"
              checked={autoDeploy}
              onCheckedChange={(c) => setAutoDeploy(c === true)}
            />
            <Label htmlFor="sweep-auto-deploy" className="cursor-pointer text-xs font-normal text-muted-foreground">
              Auto-deploy bot when walk-forward OOS passes
            </Label>
            {autoDeploy && (
              <div className="flex items-center gap-1.5">
                <Label className="text-xs text-muted-foreground">Min OOS PnL</Label>
                <Input
                  className="h-7 w-16 text-xs num-mono"
                  value={autoDeployMinOosPnl}
                  onChange={(e) => setAutoDeployMinOosPnl(e.target.value)}
                />
              </div>
            )}
          </div>
          <span className="text-xs text-muted-foreground">
            {rollingWf
              ? 'Sequential IS/OOS slices across the range; best config by mean OOS performance'
              : 'Walk-forward uses a single 70/30 split when rolling is off'}
          </span>
        </div>
      </div>

      {bestConfig && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => applyConfig(bestConfig)}
          >
            Apply best config to deploy
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={deployOptimized}
          >
            Deploy with optimized params
          </Button>
        </div>
      )}

      <BacktestWalkForwardPanel walkForward={results?.walk_forward} />

      {results?.auto_deploy && (
        <Alert className={cn(
          'border-border/60 py-2',
          results.auto_deploy.deployed ? 'bg-trading-up/10' : 'bg-muted/20',
        )}>
          <AlertDescription className="text-xs">
            {results.auto_deploy.deployed
              ? `Auto-deployed bot ${results.auto_deploy.bot_id?.slice(0, 8)}… (OOS PnL $${Number(results.auto_deploy.metrics?.oos_pnl ?? 0).toFixed(2)})`
              : `Auto-deploy skipped: ${results.auto_deploy.reason}`}
          </AlertDescription>
        </Alert>
      )}

      {aggregatedFilterRejects && (
        <FilterRejectsDashboard
          rejects={aggregatedFilterRejects.rejects}
          total={aggregatedFilterRejects.total}
          title="Sweep filter rejects"
          hint={`Summed across ${aggregatedFilterRejects.runs} optimizer runs — signals blocked before entry during replay.`}
        />
      )}

      {sweep?.results?.length > 0 && (
        <>
          <Separator className="my-1" />
          <div className="algo-backtest-table-scroll">
            <table className="terminal-table algo-backtest-table m-0 text-xs">
              <thead>
                <tr>
                  <th>Config</th>
                  <th className="text-right">{metricHeader(activeObjective)}</th>
                  <th className="text-right">Trades</th>
                  <th className="text-right">Filters</th>
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
                        activeObjective === 'total_pnl' && (row.total_pnl ?? 0) >= 0 && 'text-trading-up',
                        activeObjective === 'total_pnl' && (row.total_pnl ?? 0) < 0 && 'text-trading-down',
                      )}>
                        {formatMetric(row, activeObjective)}
                      </td>
                      <td className="num-mono text-right">{row.trade_count ?? summary.total_trades ?? '—'}</td>
                      <td className="num-mono text-right text-muted-foreground" title={
                        row.filter_rejects
                          ? Object.entries(row.filter_rejects).filter(([, n]) => n > 0).map(([k, n]) => `${k}:${n}`).join(', ')
                          : undefined
                      }>
                        {row.filter_rejects_total ?? summary.filter_rejects_total ?? '—'}
                      </td>
                      <td className="num-mono text-right">
                        {summary.win_rate != null ? `${Number(summary.win_rate).toFixed(1)}%` : '—'}
                      </td>
                      <td className="text-right">
                        {isBest && row.config && (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
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
          </div>
          <OptimizerHeatmap sweep={sweep} paramDefs={paramDefs} objective={activeObjective} />
        </>
      )}

      <OptimizationHistory />
    </div>
  );
}
