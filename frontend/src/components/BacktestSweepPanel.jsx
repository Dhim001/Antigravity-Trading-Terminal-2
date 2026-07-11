/**
 * Parameter sweep + walk-forward controls with strategy-aware param grid.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
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
import { useResearchStore } from '../store/useResearchStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { withLlmModel } from '../api/endpoints';
import { getSweepEligibleFields } from '../lib/botConfigDisplay';
import { DEFAULT_SWEEP_OBJECTIVE, defaultSweepEnabled, isExploratorySweep } from '../lib/optimizerDefaults';
import { defaultPortfolioSymbols } from '../lib/portfolioBacktest';
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
  { value: 'calmar_ratio', label: 'Calmar ratio (default)' },
  { value: 'max_drawdown_penalty', label: 'PnL − DD penalty' },
  { value: 'total_pnl', label: 'Total PnL' },
  { value: 'sharpe_ratio', label: 'Sharpe ratio' },
  { value: 'robust_score', label: 'Robust score (Sharpe × √trades)' },
  { value: 'profit_factor', label: 'Profit factor' },
  { value: 'sortino_ratio', label: 'Sortino ratio' },
  { value: 'expectancy', label: 'Expectancy / trade' },
  { value: 'win_rate', label: 'Win rate' },
  { value: 'stress_pnl', label: 'Stress PnL (2× slippage)' },
  { value: 'max_consecutive_losses', label: 'Fewest loss streak' },
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
  const cap = mode === 'grid' ? Math.min(sweep.max_combos ?? 24, 24) : Math.min(sweep.max_combos ?? 24, 200);
  if (mode === 'bayesian') return cap;
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

function defaultEnabledKeys(strategy, paramDefs) {
  return defaultSweepEnabled(strategy, paramDefs);
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
  const backtestRunning = useResearchStore((s) => s.backtestRunning);
  const botConfig = useStore((s) => s.botConfig);
  const optimizerPreset = useResearchStore((s) => s.optimizerPreset);
  const clearOptimizerPreset = useResearchStore((s) => s.clearOptimizerPreset);
  const updateBotConfig = useStore((s) => s.updateBotConfig);
  const setBotTimeframe = useStore((s) => s.setBotTimeframe);
  const setBotStrategy = useStore((s) => s.setBotStrategy);
  const setPendingDeploy = useResearchStore((s) => s.setPendingDeploy);
  const agentLlmAvailable = useStore((s) => s.agentLlmAvailable);

  const paramDefs = useMemo(
    () => getSweepEligibleFields(strategy, botConfig),
    [strategy, botConfig],
  );

  const [enabled, setEnabled] = useState(() => defaultEnabledKeys(strategy, paramDefs));
  const [valuesByKey, setValuesByKey] = useState(() =>
    defaultValuesForFields(getSweepEligibleFields(strategy, botConfig), botConfig),
  );
  const [maxCombos, setMaxCombos] = useState(24);
  const [sweepMode, setSweepMode] = useState('grid');
  const [objective, setObjective] = useState(DEFAULT_SWEEP_OBJECTIVE);
  const [minTrades, setMinTrades] = useState(1);
  const [reasoning, setReasoning] = useState(false);
  const [rollingWf, setRollingWf] = useState(true);
  const [rollingFolds, setRollingFolds] = useState(3);
  const [wfMode, setWfMode] = useState('rolling');
  const [purgedSplits, setPurgedSplits] = useState(true);
  const [finalHoldoutPct, setFinalHoldoutPct] = useState('');
  const [pboAudit, setPboAudit] = useState(false);
  const [optimizeRegime, setOptimizeRegime] = useState('all');
  const [portfolioSweep, setPortfolioSweep] = useState(false);
  const [autoDeploy, setAutoDeploy] = useState(false);
  const [autoDeployMinOosPnl, setAutoDeployMinOosPnl] = useState('0');
  const prevStrategyRef = useRef(strategy);

  // Only fully reset sweep axes when the strategy changes. Re-running on every
  // botConfig tick was wiping user checkboxes / empty Values and leaving Run disabled.
  useEffect(() => {
    if (prevStrategyRef.current === strategy) return;
    prevStrategyRef.current = strategy;
    setEnabled(defaultSweepEnabled(strategy, paramDefs));
    setValuesByKey(defaultValuesForFields(paramDefs, botConfig));
  }, [strategy, paramDefs, botConfig]);

  // Seed newly appeared param keys without clobbering user edits.
  useEffect(() => {
    setValuesByKey((prev) => {
      const defaults = defaultValuesForFields(paramDefs, botConfig);
      let changed = false;
      const next = { ...prev };
      for (const [key, val] of Object.entries(defaults)) {
        if (next[key] === undefined) {
          next[key] = val;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
    setEnabled((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const def of paramDefs) {
        if (next[def.key] === undefined) {
          next[def.key] = false;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [paramDefs, botConfig]);

  useEffect(() => {
    if (!optimizerPreset) return;
    if (optimizerPreset.objective) setObjective(optimizerPreset.objective);
    if (optimizerPreset.rollingWf != null) setRollingWf(Boolean(optimizerPreset.rollingWf));
    if (optimizerPreset.rollingFolds) setRollingFolds(optimizerPreset.rollingFolds);
    if (optimizerPreset.purgedSplits != null) setPurgedSplits(Boolean(optimizerPreset.purgedSplits));
    if (optimizerPreset.wfMode) setWfMode(optimizerPreset.wfMode);
    if (optimizerPreset.portfolioSweep != null) setPortfolioSweep(Boolean(optimizerPreset.portfolioSweep));
    if (optimizerPreset.optimizeRegime) setOptimizeRegime(optimizerPreset.optimizeRegime);
    if (optimizerPreset.enabled) {
      setEnabled((prev) => ({ ...prev, ...optimizerPreset.enabled }));
    }
    if (optimizerPreset.values) {
      setValuesByKey((prev) => ({ ...prev, ...optimizerPreset.values }));
    }
    clearOptimizerPreset();
  }, [optimizerPreset, clearOptimizerPreset]);

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
  const sweepDisabledReason = backtestRunning
    ? 'A backtest or sweep is already running'
    : !sweepGrid
      ? 'Enable at least one parameter and enter Values (e.g. 1, 2, 3)'
      : null;

  const toggleParamEnabled = (key, on, def) => {
    setEnabled((prev) => ({ ...prev, [key]: on }));
    if (!on) return;
    setValuesByKey((prev) => {
      if (String(prev[key] ?? '').trim()) return prev;
      const seeded = defaultValuesForFields([def], botConfig)[key]
        || def.placeholder
        || '';
      return { ...prev, [key]: seeded };
    });
  };

  const runSweep = async (walkForward = false) => {
    if (!sweepGrid) {
      toast.error('Enable at least one parameter with values');
      return;
    }
    if (walkForward && walkForwardTooFewBars) {
      toast.error(`Walk-forward needs ~${WALK_FORWARD_MIN_BARS}+ bars — increase days or lower the timeframe`);
      return;
    }
    if (walkForward && wfMode === 'anchored' && !rollingWf) {
      toast.error('Anchored walk-forward requires multi-fold mode — enable rolling WF');
      return;
    }
    if (backtestRunning) return;
    useResearchStore.getState().setBacktestRunning(true);
    useResearchStore.getState().setBacktestProgress({
      pct: 0,
      phase: 'sweep',
      message: walkForward ? 'Starting walk-forward…' : 'Starting sweep…',
    });
    const parsedDays = parseInt(days, 10) || 7;
    const wfFolds = walkForward ? (rollingWf ? rollingFolds : 1) : 1;
    const timeoutMs = getBacktestClientTimeoutMs({
      reasoning,
      days: parsedDays,
      walkForward,
      rollingFolds: wfFolds,
      comboCount,
      strategy,
    });
    scheduleBacktestClientTimeout({
      reasoning,
      days: parsedDays,
      walkForward,
      rollingFolds: wfFolds,
      comboCount,
      timeoutMs,
      onTimeout: (elapsedMs) => {
        if (!useResearchStore.getState().backtestRunning) return;
        useResearchStore.getState().setBacktestRunning(false);
        useResearchStore.getState().setBacktestProgress(null);
        toast.error(
          walkForward
            ? `Walk-forward timed out after ${formatBacktestTimeoutLabel(elapsedMs)} — try fewer folds/combos or increase VITE_BACKTEST_WALK_FORWARD_TIMEOUT_MS`
            : `Sweep timed out after ${formatBacktestTimeoutLabel(elapsedMs)}`,
        );
      },
    });
    const portfolioSymbols = portfolioSweep
      ? defaultPortfolioSymbols(symbol, useStore.getState().symbolsList)
      : undefined;
    const wfExtras = walkForward
      ? {
          wf_mode: wfMode,
          purged_splits: purgedSplits,
          final_holdout_pct: finalHoldoutPct ? parseFloat(finalHoldoutPct) : undefined,
          pbo_audit: pboAudit || undefined,
          optimize_regime: optimizeRegime !== 'all' ? optimizeRegime : undefined,
        }
      : {
          optimize_regime: optimizeRegime !== 'all' ? optimizeRegime : undefined,
          portfolio_sweep: portfolioSweep || undefined,
        };
    const { ok, error } = await sendAction(Action.RUN_BACKTEST_SWEEP, withLlmModel({
      symbol,
      strategy,
      config: botConfig,
      days: parseInt(days, 10) || 7,
      timeframe,
      oos_pct: oosPct || undefined,
      portfolio_symbols: portfolioSymbols,
      walk_forward: walkForward || undefined,
      rolling_folds: walkForward ? (rollingWf ? rollingFolds : 1) : undefined,
      train_pct: walkForward ? 70 : undefined,
      wf_mode: walkForward ? wfMode : undefined,
      purged_splits: walkForward ? purgedSplits : undefined,
      final_holdout_pct: walkForward && finalHoldoutPct ? parseFloat(finalHoldoutPct) : undefined,
      pbo_audit: walkForward && pboAudit ? true : undefined,
      optimize_regime: optimizeRegime !== 'all' ? optimizeRegime : undefined,
      sweep: sweepGrid ? { ...sweepGrid, ...wfExtras } : null,
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
      useResearchStore.getState().setBacktestRunning(false);
      useResearchStore.getState().setBacktestProgress(null);
    }
  };

  const applyConfig = (cfg) => {
    if (!cfg) return;
    updateBotConfig(cfg);
    toast.success('Applied sweep winner to deploy settings');
  };

  const deployOptimized = () => {
    const cfg = results?.walk_forward?.best_config
      ?? results?.sweep?.stable_config
      ?? results?.sweep?.best_config;
    if (!cfg) {
      toast.error('No optimized config available — run a sweep first');
      return;
    }
    updateBotConfig(cfg);
    // Optimizer TF/strategy must follow the run — deploy dialog used to keep
    // AlgoPanel's current TF (often 1m) while params were fit on 1h/etc.
    if (timeframe) setBotTimeframe(timeframe);
    if (strategy) setBotStrategy(strategy);
    setPendingDeploy(true);
    toast.success(
      `Optimized params applied (${strategy || 'strategy'} · ${timeframe || 'tf'}) — confirm deploy`,
    );
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

  const bestConfig = results?.walk_forward?.best_config
    ?? results?.sweep?.stable_config
    ?? results?.sweep?.best_config;
  const sweepStability = results?.sweep?.stability;
  const bayesianMeta = results?.sweep?.bayesian;
  const isChartAgent = (strategy || '').toUpperCase() === 'CHART_AGENT';

  return (
    <div className="algo-backtest-sweep algo-backtest-sweep--lab">
      <header className="algo-backtest-sweep__hero">
        <div className="algo-backtest-sweep__hero-copy">
          <h4 className="algo-backtest-sweep__title">Parameter optimizer</h4>
          <p className="algo-backtest-sweep__subtitle">
            <span className="algo-backtest-sweep__chip">{symbol}</span>
            <span className="algo-backtest-sweep__chip">{strategy}</span>
            <span className="algo-backtest-sweep__chip num-mono">{days}d · {timeframe}</span>
          </p>
        </div>
        <div className="algo-backtest-sweep__actions">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="algo-backtest-sweep__btn algo-backtest-sweep__btn--sweep"
            disabled={backtestRunning || !sweepGrid}
            title={sweepDisabledReason || undefined}
            onClick={() => runSweep(false)}
          >
            Run sweep
            <span className="algo-backtest-sweep__btn-badge num-mono">{comboCount}</span>
          </Button>
          <Button
            type="button"
            size="sm"
            className="algo-backtest-sweep__btn algo-backtest-sweep__btn--wf"
            disabled={backtestRunning || !sweepGrid || walkForwardTooFewBars}
            onClick={() => runSweep(true)}
            title={
              sweepDisabledReason
                || (walkForwardTooFewBars
                  ? `Need ~${WALK_FORWARD_MIN_BARS}+ bars for a 70/30 split — increase days or use a lower timeframe`
                  : wfMode === 'anchored' && rollingWf
                    ? `Anchored walk-forward (${rollingFolds} folds) — expanding IS from series start`
                    : rollingWf
                      ? `Rolling walk-forward (${rollingFolds} folds) — optimize IS, validate OOS per slice`
                      : 'Optimize on first 70% of bars, validate on last 30%')
            }
          >
            {wfMode === 'anchored' && rollingWf
              ? `Anchored WF (${rollingFolds})`
              : rollingWf
                ? `Rolling WF (${rollingFolds})`
                : 'Walk-forward'}
          </Button>
          {sweep?.results?.length > 0 && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="algo-backtest-sweep__btn algo-backtest-sweep__btn--export"
              onClick={handleExportCsv}
            >
              Export CSV
            </Button>
          )}
        </div>
      </header>

      <section className="algo-backtest-sweep__card algo-backtest-sweep__card--search" aria-label="Search settings">
        <h5 className="algo-backtest-sweep__card-title">Search</h5>
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
              if (v !== 'grid' && maxCombos > 200) setMaxCombos(200);
            }}
          >
            <ToggleGroupItem value="grid" className="text-xs" title="Full grid, capped at 24 runs">Grid</ToggleGroupItem>
            <ToggleGroupItem value="random" className="text-xs" title="Random search, up to 200 trials">Random</ToggleGroupItem>
            <ToggleGroupItem value="lhs" className="text-xs" title="Latin hypercube sampling, up to 200 trials">LHS</ToggleGroupItem>
            <ToggleGroupItem value="bayesian" className="text-xs" title="Optuna TPE — adaptive search, up to 200 trials">Bayesian</ToggleGroupItem>
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
          {comboCount > 0 && (
            <span className="text-[0.55rem] text-muted-foreground block mt-0.5">
              WF floor: max({minTrades}, 5 × params with 2+ values)
            </span>
          )}
        </div>
        <div className="algo-backtest-sweep__control">
          <Label className="algo-backtest-sweep__control-label">Max combos</Label>
          <Input
            type="number"
            min={1}
            max={sweepMode === 'grid' ? 24 : 200}
            className="h-7 w-16 text-xs"
            value={maxCombos}
            onChange={(e) => {
              const cap = sweepMode === 'grid' ? 24 : 200;
              setMaxCombos(Math.max(1, Math.min(cap, parseInt(e.target.value, 10) || 1)));
            }}
          />
        </div>
        </div>
        <div className="algo-backtest-sweep__budget">
          <span className="algo-backtest-sweep__budget-item num-mono">
            {comboCount} run{comboCount === 1 ? '' : 's'}
          </span>
          {fullGridSize > 1 && (
            <span className="algo-backtest-sweep__budget-item num-mono">
              grid {fullGridSize}
            </span>
          )}
          {comboTruncated && (
            <span className="algo-backtest-sweep__budget-item algo-backtest-sweep__budget-item--warn">
              capped {comboCount}
            </span>
          )}
          <span className="algo-backtest-sweep__budget-item">5 min · 200 trial budget</span>
        </div>
      </section>

      {(isChartAgent || isExploratorySweep(results) || results?.sim_mode === 'research'
        || (results?.meta?.config?.direction_mode
          && results.meta.config.direction_mode !== (botConfig?.direction_mode ?? 'LONG_ONLY'))) && (
        <div className="algo-backtest-sweep__alerts">
          {isChartAgent && (
            <Alert className="algo-backtest-sweep__alert py-1.5">
              <AlertDescription className="text-xs leading-snug">
                Chart Agent sweeps replay rules-only signals — LLM analysis is not varied during optimization.
              </AlertDescription>
            </Alert>
          )}

          {isExploratorySweep(results) && (
            <Alert variant="destructive" className="algo-backtest-sweep__alert py-1.5">
              <AlertDescription className="text-xs leading-snug">
                Exploratory in-sample sweep — run walk-forward before deploy. Deploy gate will block until OOS-validated.
              </AlertDescription>
            </Alert>
          )}

          {results?.sim_mode === 'research' && (
            <Alert variant="destructive" className="algo-backtest-sweep__alert py-1.5">
              <AlertDescription className="text-xs leading-snug">
                Parent backtest used research mode — sweep inherits sim_mode. Re-run live-aligned before deploy.
              </AlertDescription>
            </Alert>
          )}

          {results?.meta?.config?.direction_mode
            && results.meta.config.direction_mode !== (botConfig?.direction_mode ?? 'LONG_ONLY') && (
            <Alert className="algo-backtest-sweep__alert py-1.5">
              <AlertDescription className="text-xs leading-snug">
                Backtest direction ({results.meta.config.direction_mode}) differs from current deploy config — align trade direction before optimizing.
              </AlertDescription>
            </Alert>
          )}
        </div>
      )}

      <section className="algo-backtest-sweep__card algo-backtest-sweep__card--params" aria-label="Sweep parameters">
        <h5 className="algo-backtest-sweep__card-title">Parameters</h5>
        {!sweepGrid && (
          <p className="algo-backtest-sweep__hint">
            Check at least one parameter and enter comma-separated Values to enable Run sweep.
          </p>
        )}
        <div className="algo-backtest-sweep__param-head" aria-hidden>
          <span />
          <span>Field</span>
          <span>Values</span>
        </div>
        <div className="algo-backtest-sweep__grid">
        {paramDefs.map((def) => (
          <div
            key={def.key}
            className={cn(
              'algo-backtest-sweep__row',
              enabled[def.key] && 'algo-backtest-sweep__row--active',
            )}
          >
            <Checkbox
              id={`sweep-${def.key}`}
              checked={Boolean(enabled[def.key])}
              onCheckedChange={(c) => toggleParamEnabled(def.key, c === true, def)}
            />
            <Label
              htmlFor={`sweep-${def.key}`}
              className="algo-backtest-sweep__row-label"
            >
              {def.label}
            </Label>
            <Input
              className="algo-backtest-sweep__row-input h-8 text-xs num-mono"
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
          <div className="algo-backtest-sweep__row algo-backtest-sweep__row--toggle">
            <Checkbox
              id="sweep-reasoning"
              checked={reasoning}
              onCheckedChange={(c) => setReasoning(c === true)}
            />
            <Label htmlFor="sweep-reasoning" className="algo-backtest-sweep__row-label algo-backtest-sweep__row-label--wide">
              Generate trade explanations after backtest (local LLM, post-hoc only)
            </Label>
          </div>
        )}
        </div>
      </section>

      <section className="algo-backtest-sweep__card algo-backtest-sweep__card--wf" aria-label="Walk-forward validation">
        <h5 className="algo-backtest-sweep__card-title">Walk-forward &amp; validation</h5>
        <div className="algo-backtest-sweep__wf-grid">
          <div className="algo-backtest-sweep__wf-item">
            <Checkbox
              id="sweep-rolling"
              checked={rollingWf}
              onCheckedChange={(c) => setRollingWf(c === true)}
            />
            <Label htmlFor="sweep-rolling" className="algo-backtest-sweep__wf-label">
              Multi-fold walk-forward
            </Label>
          </div>
          {rollingWf && (
            <>
              <div className="algo-backtest-sweep__wf-field">
                <Label className="algo-backtest-sweep__wf-field-label">Folds</Label>
                <Select
                  value={String(rollingFolds)}
                  onValueChange={(v) => setRollingFolds(parseInt(v, 10) || 3)}
                >
                  <SelectTrigger size="sm" className="algo-backtest-sweep__wf-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent position="popper">
                    {[2, 3, 4, 5].map((n) => (
                      <SelectItem key={n} value={String(n)} className="text-xs">{n}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="algo-backtest-sweep__wf-field">
                <Label className="algo-backtest-sweep__wf-field-label">WF mode</Label>
                <ToggleGroup
                  type="single"
                  variant="outline"
                  size="sm"
                  value={wfMode}
                  onValueChange={(v) => {
                    if (!v) return;
                    setWfMode(v);
                    if (v === 'anchored') setRollingWf(true);
                  }}
                  className="algo-backtest-sweep__wf-toggle"
                >
                  <ToggleGroupItem value="rolling" className="text-xs" title="Fixed-width IS/OOS slices">
                    Rolling
                  </ToggleGroupItem>
                  <ToggleGroupItem value="anchored" className="text-xs" title="Expanding IS anchored at series start">
                    Anchored
                  </ToggleGroupItem>
                </ToggleGroup>
              </div>
            </>
          )}
          <div className="algo-backtest-sweep__wf-item">
            <Checkbox
              id="sweep-purged"
              checked={purgedSplits}
              onCheckedChange={(c) => setPurgedSplits(c === true)}
            />
            <Label htmlFor="sweep-purged" className="algo-backtest-sweep__wf-label">
              Purged splits
            </Label>
          </div>
          <div className="algo-backtest-sweep__wf-field">
            <Label className="algo-backtest-sweep__wf-field-label">Holdout %</Label>
            <Input
              type="number"
              min={0}
              max={30}
              className="algo-backtest-sweep__wf-input h-7 text-xs num-mono"
              placeholder="off"
              value={finalHoldoutPct}
              onChange={(e) => setFinalHoldoutPct(e.target.value)}
              title="Reserve trailing bars never used in optimization (5–30%). Empty = disabled."
            />
          </div>
          <div className="algo-backtest-sweep__wf-item">
            <Checkbox
              id="sweep-pbo"
              checked={pboAudit}
              onCheckedChange={(c) => setPboAudit(c === true)}
            />
            <Label htmlFor="sweep-pbo" className="algo-backtest-sweep__wf-label">
              PBO / CSCV audit
            </Label>
          </div>
          <div className="algo-backtest-sweep__wf-field">
            <Label className="algo-backtest-sweep__wf-field-label">Regime</Label>
            <Select value={optimizeRegime} onValueChange={setOptimizeRegime}>
              <SelectTrigger size="sm" className="algo-backtest-sweep__wf-select algo-backtest-sweep__wf-select--wide">
                <SelectValue />
              </SelectTrigger>
              <SelectContent position="popper">
                {[
                  { value: 'all', label: 'All bars' },
                  { value: 'trend', label: 'Trend (norm+elev)' },
                  { value: 'elevated', label: 'Elevated vol' },
                  { value: 'normal', label: 'Normal vol' },
                  { value: 'compressed', label: 'Compressed' },
                ].map((opt) => (
                  <SelectItem key={opt.value} value={opt.value} className="text-xs">{opt.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="algo-backtest-sweep__wf-item">
            <Checkbox
              id="sweep-portfolio"
              checked={portfolioSweep}
              onCheckedChange={(c) => setPortfolioSweep(c === true)}
            />
            <Label htmlFor="sweep-portfolio" className="algo-backtest-sweep__wf-label">
              Portfolio sweep
            </Label>
          </div>
          <div className="algo-backtest-sweep__wf-item algo-backtest-sweep__wf-item--wide">
            <Checkbox
              id="sweep-auto-deploy"
              checked={autoDeploy}
              onCheckedChange={(c) => setAutoDeploy(c === true)}
            />
            <Label htmlFor="sweep-auto-deploy" className="algo-backtest-sweep__wf-label">
              Auto-deploy on OOS pass
            </Label>
            {autoDeploy && (
              <div className="algo-backtest-sweep__wf-inline">
                <Label className="algo-backtest-sweep__wf-field-label">Min OOS PnL</Label>
                <Input
                  className="algo-backtest-sweep__wf-input h-7 w-16 text-xs num-mono"
                  value={autoDeployMinOosPnl}
                  onChange={(e) => setAutoDeployMinOosPnl(e.target.value)}
                />
              </div>
            )}
          </div>
        </div>
        <p className="algo-backtest-sweep__wf-hint">
          {wfMode === 'anchored' && rollingWf
            ? 'Expanding in-sample from series start; OOS slides forward with purge/embargo'
            : rollingWf
              ? 'Sequential IS/OOS slices; best config by mean OOS performance'
              : 'Single 70/30 split when multi-fold is off'}
          {purgedSplits ? ' · purged' : ''}
          {finalHoldoutPct ? ` · ${finalHoldoutPct}% final holdout` : ''}
        </p>
      </section>

      {bestConfig && (
        <div className="algo-backtest-sweep__deploy-bar">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => applyConfig(bestConfig)}
          >
            Apply best config
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={deployOptimized}
          >
            Deploy optimized
          </Button>
        </div>
      )}

      <div className="algo-backtest-sweep__results">
        <BacktestWalkForwardPanel
          walkForward={results?.walk_forward}
          symbol={symbol}
          strategy={strategy}
          timeframe={timeframe}
          runId={results?.run_id}
        />

        <div className="algo-backtest-sweep__result-alerts">
          {results?.auto_deploy && (
            <Alert className={cn(
              'algo-backtest-sweep__alert',
              results.auto_deploy.deployed ? 'algo-backtest-sweep__alert--ok' : 'algo-backtest-sweep__alert--muted',
            )}>
              <AlertDescription className="text-xs">
                {results.auto_deploy.deployed
                  ? `Auto-deployed bot ${results.auto_deploy.bot_id?.slice(0, 8)}… (OOS PnL $${Number(results.auto_deploy.metrics?.oos_pnl ?? 0).toFixed(2)})`
                  : `Auto-deploy skipped: ${results.auto_deploy.reason}`}
              </AlertDescription>
            </Alert>
          )}

          {bayesianMeta?.sampler && (
            <Alert className="algo-backtest-sweep__alert algo-backtest-sweep__alert--muted">
              <AlertDescription className="text-xs">
                Bayesian search ({bayesianMeta.sampler}): {bayesianMeta.trials_completed}/{bayesianMeta.trials_budget} trials
                {bayesianMeta.early_stopped ? ' — stopped early (plateau)' : ''}
              </AlertDescription>
            </Alert>
          )}

          {sweepStability?.recommendation === 'centroid' && (
            <Alert className="algo-backtest-sweep__alert algo-backtest-sweep__alert--muted">
              <AlertDescription className="text-xs">
                Stable pick: top-quartile centroid (spread {Number(sweepStability.objective_spread ?? 0).toFixed(2)})
                — prefer over single peak when params vary across winners.
              </AlertDescription>
            </Alert>
          )}

          {results?.pbo_audit?.pbo != null && (
            <Alert className={cn(
              'algo-backtest-sweep__alert',
              Number(results.pbo_audit.pbo) >= 0.5
                ? 'algo-backtest-sweep__alert--danger'
                : Number(results.pbo_audit.pbo) >= 0.35
                  ? 'algo-backtest-sweep__alert--warn'
                  : 'algo-backtest-sweep__alert--muted',
            )}>
              <AlertDescription className="text-xs">
                PBO / CSCV: {(Number(results.pbo_audit.pbo) * 100).toFixed(0)}% overfit probability
                ({results.pbo_audit.risk_label || 'low'} risk, {results.pbo_audit.configs_audited} configs audited)
              </AlertDescription>
            </Alert>
          )}

          {results?.final_holdout && !results.final_holdout.skipped && (
            <Alert className={cn(
              'algo-backtest-sweep__alert',
              results.final_holdout.passed === false
                ? 'algo-backtest-sweep__alert--danger'
                : 'algo-backtest-sweep__alert--ok',
            )}>
              <AlertDescription className="text-xs">
                Final holdout: ${Number(results.final_holdout.total_pnl ?? 0).toFixed(2)}
                , {results.final_holdout.trade_count ?? 0} trades
                {results.final_holdout.passed === false ? ' — failed deploy gate' : ' — passed'}
              </AlertDescription>
            </Alert>
          )}
        </div>

        {aggregatedFilterRejects && (
          <FilterRejectsDashboard
            rejects={aggregatedFilterRejects.rejects}
            total={aggregatedFilterRejects.total}
            title="Sweep filter rejects"
            hint={`Summed across ${aggregatedFilterRejects.runs} optimizer runs — signals blocked before entry during replay.`}
          />
        )}

        {sweep?.results?.length > 0 && (
          <section className="algo-backtest-sweep__card algo-backtest-sweep__card--results">
            <h3 className="algo-backtest-sweep__card-title">Trial leaderboard</h3>
            <div className="algo-backtest-sweep__table-wrap">
              <table className="terminal-table algo-backtest-table algo-backtest-sweep__table">
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
                      <tr key={row.label ?? i} className={cn(isBest && 'algo-backtest-sweep__row--best')}>
                        <td className="algo-backtest-sweep__cell-config" title={row.label}>{row.label}</td>
                        <td className={cn(
                          'num-mono text-right',
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
          </section>
        )}

        <OptimizationHistory />
      </div>
    </div>
  );
}
