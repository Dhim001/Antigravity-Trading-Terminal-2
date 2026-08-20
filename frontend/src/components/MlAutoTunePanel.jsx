/**
 * ML training hyperparameter auto-tune (Optuna) — Model Training Lab.
 */
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { Loader2, RefreshCw, Sparkles, Wand2 } from 'lucide-react';
import { toast } from 'sonner';
import { apiRequest, isAbortError } from '@/api/client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { cn } from '@/lib/utils';
import { cancelMlJob, fetchLatestMlHyperparamSweep } from '@/lib/mlLabApi';
import { mlHyperparamSweepPollDeadlineMs } from '@/lib/mlJobTimeouts';
import { floorTunedBudgetKnobs } from '@/lib/mlTunedApply';
import {
  isMlHyperparamSweepPolling,
  startMlHyperparamSweepPolling,
} from '@/lib/mlHyperparamSweepPolling';
import {
  appendMlPollLog,
  beginMlJob,
  finishMlJob,
  getMlTrainingSession,
  hydrateMlTuneSession,
  setMlJobId,
  setMlServerProgress,
  subscribeMlTrainingSession,
} from '@/lib/mlTrainingSession';

const DEFAULT_HP = {
  ML_SIGNAL_BOOST: {
    gbm_max_depth: 5,
    gbm_learning_rate: 0.08,
    gbm_max_iter: 150,
    gbm_l2_reg: 0,
    val_fraction: 0.2,
    triple_barrier_atr_mult: 2,
  },
  LSTM_DIRECTION: {
    learning_rate: 0.001,
    hidden_dim: 128,
    epochs: 60,
    batch_size: 64,
    lookback: 60,
    num_layers: 2,
  },
  TRANSFORMER_SIGNAL: {
    learning_rate: 0.001,
    hidden_dim: 128,
    d_model: 128,
    epochs: 60,
    batch_size: 64,
    lookback: 60,
    num_layers: 2,
  },
  TCN_MULTI_HORIZON: {
    learning_rate: 0.001,
    hidden_dim: 128,
    epochs: 60,
    batch_size: 64,
    lookback: 60,
    num_layers: 3,
  },
  RL_PPO_AGENT: {
    learning_rate: 0.0003,
    clip_epsilon: 0.2,
    ent_coef: 0.01,
    n_steps: 2048,
    hidden_dim: 128,
    total_timesteps: 16384,
  },
  VAE_REGIME_DETECTOR: {
    latent_dim: 16,
    anomaly_threshold: 2.5,
    hidden_dim: 128,
    learning_rate: 0.001,
    epochs: 60,
  },
  GNN_CROSS_ASSET: {
    learning_rate: 0.001,
    hidden_dim: 128,
    epochs: 60,
    batch_size: 64,
    num_layers: 2,
    n_heads: 4,
  },
};

const SWEEP_PHASES = [
  { until: 8, label: 'Queuing Optuna study…' },
  { until: 25, label: 'Loading candles & feature space…' },
  { until: 75, label: 'Running hyperparam trials…' },
  { until: 92, label: 'Promoting top trials (full fidelity)…' },
  { until: 100, label: 'Ranking importance & finalizing…' },
];

function formatElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  return m > 0 ? `${m}m ${String(r).padStart(2, '0')}s` : `${r}s`;
}

function formatBudgetLabel(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  if (s >= 3600) return `${Math.round(s / 3600)}h`;
  if (s >= 60) return `${Math.round(s / 60)}m`;
  return `${s}s`;
}

function hasSweepOutcome(result) {
  if (!result || typeof result !== 'object') return false;
  if (result.ok === false) return false;
  if (result.ok === true) return true;
  if (result.best_hyperparams && typeof result.best_hyperparams === 'object'
    && Object.keys(result.best_hyperparams).length) {
    return true;
  }
  return result.best_score != null || Number(result.trials_completed) > 0;
}

function ConvergenceSparkline({ points }) {
  const vals = (points || []).map((p) => Number(p.score)).filter((n) => Number.isFinite(n));
  if (vals.length < 2) {
    return (
      <p className="text-[10px] text-muted-foreground">
        Convergence chart appears after ≥2 scored trials.
      </p>
    );
  }
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const w = 220;
  const h = 48;
  const coords = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * (w - 4) + 2;
    const y = h - 4 - ((v - min) / span) * (h - 8);
    return `${x},${y}`;
  });
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full max-w-[240px] h-12" aria-label="Trial convergence">
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        className="text-emerald-400/90"
        points={coords.join(' ')}
      />
    </svg>
  );
}

function ImportanceBars({ ranking }) {
  const entries = Object.entries(ranking || {})
    .map(([k, v]) => [k, Number(v)])
    .filter(([, v]) => Number.isFinite(v))
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);
  if (!entries.length) {
    return <p className="text-[10px] text-muted-foreground">Importance ranking after ≥3 trials.</p>;
  }
  const max = Math.max(...entries.map(([, v]) => v), 1e-9);
  return (
    <ul className="space-y-1">
      {entries.map(([key, val]) => (
        <li key={key} className="flex items-center gap-2 text-[10px]">
          <span className="w-28 truncate num-mono text-muted-foreground" title={key}>{key}</span>
          <div className="flex-1 h-1.5 rounded bg-muted overflow-hidden">
            <div
              className="h-full rounded bg-sky-500/80"
              style={{ width: `${Math.max(4, (val / max) * 100)}%` }}
            />
          </div>
          <span className="num-mono w-10 text-right">{val.toFixed(2)}</span>
        </li>
      ))}
    </ul>
  );
}

function DiffTable({ defaults, best }) {
  const keys = useMemo(() => {
    const set = new Set([
      ...Object.keys(defaults || {}),
      ...Object.keys(best || {}),
    ]);
    return [...set].sort();
  }, [defaults, best]);
  if (!keys.length) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[10px] num-mono">
        <thead>
          <tr className="text-muted-foreground text-left">
            <th className="py-1 pr-2 font-medium">Param</th>
            <th className="py-1 pr-2 font-medium">Default</th>
            <th className="py-1 font-medium">Best</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const d = defaults?.[k];
            const b = best?.[k];
            const changed = d !== b && b !== undefined;
            return (
              <tr key={k} className={cn(changed && 'text-emerald-400')}>
                <td className="py-0.5 pr-2 text-muted-foreground">{k}</td>
                <td className="py-0.5 pr-2">{d == null ? '—' : String(d)}</td>
                <td className="py-0.5">{b == null ? '—' : String(b)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SweepProgressBar({
  active,
  label,
  startedAt,
  budgetSec,
  serverProgress,
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active) return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [active, startedAt]);

  if (!active) return null;

  const elapsed = Math.max(0, now - (startedAt || now));
  const timeoutMs = Math.max((Number(budgetSec) || 600) * 1000, 60_000);
  const hasServerPct = serverProgress?.pct != null && Number(serverProgress.pct) > 0;
  const ratio = Math.min(0.94, 1 - Math.exp(-elapsed / (timeoutMs * 0.45)));
  const estPct = Math.max(2, Math.round(ratio * 100));
  const serverPct = hasServerPct ? Math.round(Number(serverProgress.pct)) : 0;
  const doneLike = serverProgress?.status === 'done'
    || serverProgress?.phase === 'done'
    || serverPct >= 100;
  const pct = hasServerPct
    ? (doneLike ? 100 : Math.max(1, Math.min(99, serverPct)))
    : estPct;
  const phaseIdx = SWEEP_PHASES.findIndex((p) => pct < p.until);
  const phase = SWEEP_PHASES[phaseIdx >= 0 ? phaseIdx : Math.max(SWEEP_PHASES.length - 1, 0)];
  const phaseLabel = hasServerPct
    ? [serverProgress.phase, serverProgress.detail].filter(Boolean).join(' · ') || phase?.label
    : phase?.label;
  const trialBit = serverProgress?.trial != null && serverProgress?.max_trials != null
    ? ` · trial ${serverProgress.trial}/${serverProgress.max_trials}`
    : '';
  const bestBit = serverProgress?.best_score != null
    ? ` · best ${serverProgress.best_score}`
    : '';

  return (
    <div className="ml-training__progress" role="status" aria-live="polite">
      <div className="ml-training__progress-head">
        <span className="ml-training__progress-label">
          <Loader2 size={12} className="animate-spin" aria-hidden />
          {label}
        </span>
        <span className="ml-training__progress-meta num-mono">
          {pct}% · {formatElapsed(elapsed)}
          {timeoutMs >= 60_000 ? ` / ~${formatBudgetLabel(timeoutMs / 1000)}` : ''}
        </span>
      </div>
      <div
        className="ml-training__progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label={label}
      >
        <div className="ml-training__progress-fill" style={{ width: `${pct}%` }} />
      </div>
      {phaseLabel && (
        <p className="ml-training__progress-phase mt-1">
          {phaseLabel}{trialBit}{bestBit}
        </p>
      )}
    </div>
  );
}

export default function MlAutoTunePanel({
  symbol,
  strategy,
  timeframe = '1m',
  trainingWindow = '3',
  disabled = false,
  onApplyAndRetrain,
}) {
  const [maxTrials, setMaxTrials] = useState(12);
  const [timeBudget, setTimeBudget] = useState(600);
  const [multiFidelity, setMultiFidelity] = useState(true);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const mlSession = useSyncExternalStore(
    subscribeMlTrainingSession,
    getMlTrainingSession,
    getMlTrainingSession,
  );
  const sessionMatches = (
    String(mlSession.symbol || '').toUpperCase() === String(symbol || '').toUpperCase()
    && String(mlSession.strategy || '').toUpperCase() === String(strategy || '').toUpperCase()
  );
  const sessionTuning = Boolean(sessionMatches && mlSession.tuning);
  const running = starting || sessionTuning;
  const progress = sessionMatches ? mlSession.serverProgress : null;
  const result = sessionMatches ? mlSession.tuneResult : null;
  const jobId = sessionMatches ? mlSession.jobId : null;
  const startedAt = sessionMatches
    ? (mlSession.jobProgress?.startedAt || null)
    : null;

  // Remount / tab-switch: ensure module poller is attached (does not depend on this panel).
  useEffect(() => {
    if (!sessionMatches || !mlSession.tuning || !mlSession.jobId) return;
    if (isMlHyperparamSweepPolling(mlSession.jobId)) return;
    startMlHyperparamSweepPolling(mlSession.jobId, {
      jobToken: mlSession.jobToken,
      timeBudgetSec: Number(timeBudget) || 600,
    });
  }, [
    sessionMatches,
    mlSession.tuning,
    mlSession.jobId,
    mlSession.jobToken,
    timeBudget,
  ]);

  const defaults = useMemo(
    () => DEFAULT_HP[strategy] || DEFAULT_HP.ML_SIGNAL_BOOST,
    [strategy],
  );

  const handleRefresh = useCallback(async () => {
    if (!symbol || !strategy || refreshing) return;
    const sess = getMlTrainingSession();
    if (sess.training || sess.validating) {
      toast.message('Wait for the current train/validate job to finish');
      return;
    }
    const watching = sess.tuning && isMlHyperparamSweepPolling(sess.jobId);
    if (watching) {
      toast.message('Sweep still running — results will appear when it finishes');
      return;
    }
    setRefreshing(true);
    try {
      const job = await fetchLatestMlHyperparamSweep(symbol, strategy);
      if (!job) {
        toast.message('No auto-tune job found for this symbol and strategy');
        return;
      }
      const status = String(job.status || '').toLowerCase();
      const jobId = job.job_id || job.id;
      if (status === 'queued' || status === 'running') {
        if (sess.tuning && sess.jobId === jobId) {
          toast.message('Sweep still running');
          return;
        }
        const { jobToken } = beginMlJob({
          kind: 'hyperparam_sweep',
          strategy,
          symbol,
          jobId,
          jobProgress: {
            active: true,
            kind: 'hyperparam_sweep',
            startedAt: Date.now(),
            timeoutMs: mlHyperparamSweepPollDeadlineMs(Number(timeBudget) || 600),
            label: `Auto-tune · ${strategy}`,
            phases: SWEEP_PHASES,
          },
        });
        if (job.progress && typeof job.progress === 'object') {
          setMlServerProgress({ ...job.progress, status: job.status });
        }
        startMlHyperparamSweepPolling(jobId, {
          jobToken,
          timeBudgetSec: Number(timeBudget) || 600,
        });
        toast.message(`Reattached auto-tune · job ${String(jobId).slice(0, 8)}…`);
        return;
      }
      const res = job.result && typeof job.result === 'object' ? job.result : {};
      if (status === 'cancelled' || res.cancelled) {
        hydrateMlTuneSession({ symbol, strategy, result: null, lastError: null });
        toast.message('Latest auto-tune was cancelled');
        return;
      }
      if (status === 'error' || res.ok === false) {
        const err = res.error || job.error || 'Hyperparam sweep failed';
        hydrateMlTuneSession({ symbol, strategy, result: res, lastError: err });
        toast.error(err);
        return;
      }
      if (status === 'done' || hasSweepOutcome(res)) {
        const outcome = { ...res, ok: res.ok !== false };
        hydrateMlTuneSession({ symbol, strategy, result: outcome, lastError: null });
        toast.success(
          `Loaded sweep · best ${outcome.best_score ?? '—'} · ${outcome.trials_completed ?? 0} trials`,
        );
        return;
      }
      toast.message('Latest auto-tune has no result yet');
    } catch (err) {
      if (!isAbortError(err)) toast.error(err?.message || 'Failed to refresh auto-tune');
    } finally {
      setRefreshing(false);
    }
  }, [symbol, strategy, refreshing, timeBudget]);

  const handleCancel = useCallback(async () => {
    const id = getMlTrainingSession().jobId;
    if (!id || cancelling) return;
    setCancelling(true);
    try {
      const body = await cancelMlJob(id);
      if (body?.ok) {
        toast.message(body.immediate ? 'Sweep cancelled' : 'Cancel requested — finishing current trial…');
      } else {
        toast.error(body?.error || 'Cancel failed');
      }
    } catch (err) {
      if (!isAbortError(err)) toast.error(err?.message || 'Cancel failed');
    } finally {
      setCancelling(false);
    }
  }, [cancelling]);

  const startSweep = useCallback(async () => {
    if (!symbol || !strategy) {
      toast.error('Select a symbol and strategy first');
      return;
    }
    const { jobToken } = beginMlJob({
      kind: 'hyperparam_sweep',
      strategy,
      symbol,
      jobProgress: {
        active: true,
        kind: 'hyperparam_sweep',
        startedAt: Date.now(),
        timeoutMs: mlHyperparamSweepPollDeadlineMs(Number(timeBudget) || 600),
        label: `Auto-tune · ${strategy}`,
        phases: SWEEP_PHASES,
      },
    });
    setStarting(true);
    setMlServerProgress({ pct: 1, phase: 'queued', detail: 'hyperparam sweep', status: 'queued' });
    appendMlPollLog({
      status: 'queued',
      pct: 1,
      phase: 'queued',
      detail: 'starting hyperparam sweep',
      level: 'info',
    });
    try {
      const body = await apiRequest('/api/v1/ml/hyperparam-sweep', {
        method: 'POST',
        body: {
          symbol,
          strategy,
          max_trials: Number(maxTrials) || 12,
          time_budget_sec: Number(timeBudget) || 600,
          multi_fidelity: multiFidelity,
          config: {
            timeframe,
            training_window_months: Number(trainingWindow) || 3,
            max_trials: Number(maxTrials) || 12,
            time_budget_sec: Number(timeBudget) || 600,
            multi_fidelity: multiFidelity,
          },
        },
        timeoutMs: 30_000,
      });
      if (!body?.ok || !body.job_id) {
        toast.error(body?.error || 'Failed to start hyperparam sweep');
        appendMlPollLog({
          status: 'error',
          phase: 'error',
          detail: body?.error || 'start failed',
          level: 'warn',
          warning: body?.error || 'start failed',
        });
        finishMlJob(jobToken, { error: body?.error || 'start failed' });
        return;
      }
      setMlJobId(body.job_id);
      toast.message(`Auto-tune started · job ${String(body.job_id).slice(0, 8)}…`);
      appendMlPollLog({
        status: 'running',
        pct: 2,
        phase: 'queued',
        detail: `job ${String(body.job_id).slice(0, 8)} accepted`,
        level: 'info',
      });
      // Module poller owns progress + completion (survives panel unmount).
      startMlHyperparamSweepPolling(body.job_id, {
        jobToken,
        timeBudgetSec: Number(timeBudget) || 600,
      });
    } catch (err) {
      toast.error(err?.message || 'Hyperparam sweep failed');
      appendMlPollLog({
        status: 'error',
        phase: 'error',
        detail: err?.message || 'failed',
        warning: err?.message || 'failed',
        level: 'warn',
      });
      finishMlJob(jobToken, { error: err?.message || 'failed' });
    } finally {
      setStarting(false);
    }
  }, [
    symbol,
    strategy,
    maxTrials,
    timeBudget,
    multiFidelity,
    timeframe,
    trainingWindow,
  ]);

  const applyRetrain = useCallback(async () => {
    if (!onApplyAndRetrain) {
      toast.error('No best hyperparams to apply');
      return;
    }
    if (disabled) {
      toast.message('Wait for the current ML job to finish before applying hyperparams');
      return;
    }
    let hp = result?.best_hyperparams;
    // After job-result offload, a stale poll can leave best_hyperparams missing
    // even though the sweep saved them on the optimization run.
    if ((!hp || typeof hp !== 'object' || !Object.keys(hp).length) && result?.optimization_run_id) {
      try {
        const body = await apiRequest(
          `/api/v1/backtest/optimizations/${encodeURIComponent(result.optimization_run_id)}`,
          { timeoutMs: 15_000 },
        );
        hp = body?.run?.best_config;
      } catch (err) {
        toast.error(err?.message || 'Failed to load best hyperparams from optimization run');
        return;
      }
    }
    if (!hp || typeof hp !== 'object' || !Object.keys(hp).length) {
      toast.error('No best hyperparams to apply — re-run auto-tune');
      return;
    }
    // Only forward search-space knobs — never trial bookkeeping (skip_persist, etc.).
    const allowed = new Set(Object.keys(DEFAULT_HP[strategy] || DEFAULT_HP.LSTM_DIRECTION || {}));
    // Shared deep-ML extras Optuna may tune beyond the DEFAULT_HP snapshot.
    ['early_stop_patience', 'd_model', 'n_heads', 'dropout'].forEach((k) => allowed.add(k));
    const clean = {};
    for (const [k, v] of Object.entries(hp)) {
      if (!allowed.has(k)) continue;
      if (v == null || typeof v === 'object') continue;
      // Never forward trial flags even if a bad payload includes them.
      if (String(k).startsWith('_') || k === 'skip_persist' || k === 'skip_snapshot'
        || k === 'skip_refit' || k === 'wf_mode') {
        continue;
      }
      clean[k] = v;
    }
    if (!Object.keys(clean).length) {
      toast.error('Best hyperparams were empty after sanitizing — re-run auto-tune');
      return;
    }
    // Sweep budgets are tuned at reduced fidelity (epochs/5, timesteps/5) —
    // never let them shrink the champion below the Lab retrain budget.
    onApplyAndRetrain(floorTunedBudgetKnobs(strategy, clean), result);
  }, [result, onApplyAndRetrain, disabled, strategy]);

  return (
    <section className="ml-training__card" aria-label="Auto-tune hyperparameters">
      <div className="ml-training__card-head">
        <h4 className="ml-training__section-title flex items-center gap-1.5">
          <Wand2 size={14} aria-hidden />
          Auto-Tune Hyperparams
        </h4>
        <span className="ml-training__header-meta num-mono">
          {strategy} · {symbol || '—'}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
        <div>
          <Label className="text-[10px]">Max trials</Label>
          <Input
            className="h-7 text-xs"
            type="number"
            min={3}
            max={40}
            value={maxTrials}
            onChange={(e) => setMaxTrials(e.target.value)}
            disabled={running || disabled}
          />
        </div>
        <div>
          <Label className="text-[10px]">Time budget (sec)</Label>
          <Input
            className="h-7 text-xs"
            type="number"
            min={60}
            max={3600}
            value={timeBudget}
            onChange={(e) => setTimeBudget(e.target.value)}
            disabled={running || disabled}
          />
        </div>
        <div className="flex items-end gap-2 pb-1 col-span-2">
          <Checkbox
            id="mf-screen"
            checked={multiFidelity}
            onCheckedChange={(v) => setMultiFidelity(Boolean(v))}
            disabled={running || disabled}
          />
          <Label htmlFor="mf-screen" className="text-[10px] leading-tight cursor-pointer">
            Multi-fidelity screen (40% data → promote top-k)
          </Label>
        </div>
      </div>

      <div className="flex flex-wrap gap-2 mb-3">
        <Button
          type="button"
          size="sm"
          className="h-8 gap-1"
          disabled={running || disabled || !symbol}
          onClick={startSweep}
        >
          {running ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
          Start Sweep
        </Button>
        {running && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8"
            disabled={cancelling || !jobId}
            onClick={handleCancel}
          >
            {cancelling ? <Loader2 size={14} className="animate-spin" /> : null}
            Cancel
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 gap-1"
          disabled={refreshing || starting || !symbol || !strategy}
          aria-busy={refreshing}
          onClick={handleRefresh}
          title="Load the latest sweep result from the server"
        >
          {refreshing
            ? <Loader2 size={14} className="animate-spin" />
            : <RefreshCw size={14} />}
          Refresh
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8"
          disabled={!result?.best_hyperparams || running || disabled || !onApplyAndRetrain}
          onClick={applyRetrain}
        >
          Apply & Retrain
        </Button>
      </div>

      {sessionMatches && !running && /hyperparam|sweep|auto-tune/i.test(String(mlSession.lastError || '')) && (
        <p className="text-[10px] text-amber-400/90 mb-2" role="status">
          {mlSession.lastError}
          {' — '}
          use Refresh to load the latest server result.
        </p>
      )}

      <SweepProgressBar
        active={running}
        label={`Auto-tune · ${strategy}`}
        startedAt={startedAt}
        budgetSec={Number(timeBudget) || 600}
        serverProgress={progress}
      />

      {hasSweepOutcome(result) && (
        <div className="grid sm:grid-cols-2 gap-3 mt-3">
          <div>
            <p className="text-[10px] uppercase text-muted-foreground mb-1">Convergence</p>
            <ConvergenceSparkline points={result.convergence} />
            <p className="text-[10px] num-mono mt-1">
              Best score <strong>{result.best_score ?? '—'}</strong>
              {' · '}
              {result.trials_completed}/{result.max_trials} trials
              {result.optimization_run_id
                ? ` · run ${String(result.optimization_run_id).slice(0, 8)}`
                : ''}
            </p>
          </div>
          <div>
            <p className="text-[10px] uppercase text-muted-foreground mb-1">Param importance</p>
            <ImportanceBars ranking={result.importance_ranking} />
          </div>
          <div className="sm:col-span-2">
            <p className="text-[10px] uppercase text-muted-foreground mb-1">Default vs best</p>
            <DiffTable defaults={defaults} best={result.best_hyperparams} />
          </div>
        </div>
      )}
    </section>
  );
}
