/**
 * ML training hyperparameter auto-tune (Optuna) — Model Training Lab.
 */
import { useCallback, useMemo, useState } from 'react';
import { Loader2, Sparkles, Wand2 } from 'lucide-react';
import { toast } from 'sonner';
import { apiRequest } from '@/api/client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { cn } from '@/lib/utils';

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
              className="h-full bg-primary/70"
              style={{ width: `${Math.max(4, (val / max) * 100)}%` }}
            />
          </div>
          <span className="w-10 text-right num-mono">{val.toFixed(2)}</span>
        </li>
      ))}
    </ul>
  );
}

function DiffTable({ defaults, best }) {
  const keys = [...new Set([...Object.keys(defaults || {}), ...Object.keys(best || {})])].sort();
  if (!keys.length) return null;
  return (
    <table className="w-full text-[10px] border-collapse">
      <thead>
        <tr className="text-muted-foreground text-left">
          <th className="py-0.5 font-medium">Param</th>
          <th className="py-0.5 font-medium">Default</th>
          <th className="py-0.5 font-medium">Best</th>
        </tr>
      </thead>
      <tbody>
        {keys.map((k) => {
          const a = defaults?.[k];
          const b = best?.[k];
          const changed = a !== b && b != null;
          return (
            <tr key={k} className={cn(changed && 'bg-emerald-500/10')}>
              <td className="py-0.5 num-mono">{k}</td>
              <td className="py-0.5 num-mono text-muted-foreground">{a ?? '—'}</td>
              <td className="py-0.5 num-mono font-medium">{b ?? '—'}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
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
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(null);
  const [result, setResult] = useState(null);

  const defaults = useMemo(
    () => DEFAULT_HP[strategy] || DEFAULT_HP.ML_SIGNAL_BOOST,
    [strategy],
  );

  const pollJob = useCallback(async (jobId) => {
    const deadline = Date.now() + Math.max(timeBudget * 1000, 120_000) + 60_000;
    while (Date.now() < deadline) {
      const body = await apiRequest(`/api/v1/ml/hyperparam-sweep/${encodeURIComponent(jobId)}`, {
        method: 'GET',
        timeoutMs: 15_000,
      });
      const job = body?.job || body;
      if (!job) throw new Error(body?.error || 'Job not found');
      setProgress(job.progress || null);
      if (['done', 'error', 'cancelled'].includes(job.status)) {
        return job;
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
    throw new Error('Hyperparam sweep timed out');
  }, [timeBudget]);

  const startSweep = useCallback(async () => {
    if (!symbol || !strategy) {
      toast.error('Select a symbol and strategy first');
      return;
    }
    setRunning(true);
    setResult(null);
    setProgress({ pct: 1, phase: 'queued', detail: 'starting' });
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
        return;
      }
      toast.message(`Auto-tune started · job ${String(body.job_id).slice(0, 8)}…`);
      const job = await pollJob(body.job_id);
      const res = (job.result && typeof job.result === 'object') ? job.result : {};
      if (job.status === 'cancelled' || res.cancelled) {
        toast.message('Hyperparam sweep cancelled');
        return;
      }
      if (job.status !== 'done' || res.ok === false) {
        toast.error(res.error || job.error || 'Hyperparam sweep failed');
        setResult(res);
        return;
      }
      setResult(res);
      toast.success(
        `Best score ${res.best_score ?? '—'} · ${res.trials_completed ?? 0} trials`,
      );
    } catch (err) {
      toast.error(err?.message || 'Hyperparam sweep failed');
    } finally {
      setRunning(false);
    }
  }, [symbol, strategy, maxTrials, timeBudget, multiFidelity, timeframe, trainingWindow, pollJob]);

  const applyRetrain = useCallback(() => {
    const hp = result?.best_hyperparams;
    if (!hp || !onApplyAndRetrain) {
      toast.error('No best hyperparams to apply');
      return;
    }
    onApplyAndRetrain(hp, result);
  }, [result, onApplyAndRetrain]);

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
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8"
          disabled={!result?.best_hyperparams || running || !onApplyAndRetrain}
          onClick={applyRetrain}
        >
          Apply & Retrain
        </Button>
      </div>

      {progress && running && (
        <p className="text-[10px] text-muted-foreground mb-2 num-mono">
          {progress.pct ?? 0}% · {progress.phase || '…'} · {progress.detail || ''}
        </p>
      )}

      {result?.ok && (
        <div className="grid sm:grid-cols-2 gap-3 mt-2">
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
