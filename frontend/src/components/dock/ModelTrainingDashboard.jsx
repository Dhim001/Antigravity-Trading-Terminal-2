/**
 * Model Training Dashboard — inventory, train, validate, retrain queue.
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  BrainCircuit,
  CheckCircle2,
  FlaskConical,
  Loader2,
  Play,
  RefreshCw,
  Trash2,
  XCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useStore } from '@/store/useStore';
import { apiRequest, isAbortError } from '@/api/client';
import { getStrategyMeta, isDeepMlStrategy, isMlStrategy, ML_STRATEGY_IDS } from '@/config/strategies';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import {
  beginMlJob,
  clearMlJobProgress,
  finishMlJob,
  getCachedModelStatus,
  getMlTrainingSession,
  resolveModelStatusFetch,
  setCachedModelStatus,
  setMlValidation,
  subscribeMlTrainingSession,
} from '@/lib/mlTrainingSession';

const ML_STRATEGIES = ML_STRATEGY_IDS;
const DEEP_ML_STRATEGIES = new Set(
  ML_STRATEGY_IDS.filter((id) => isDeepMlStrategy(id)),
);

/** Client abort budgets (ms) — must stay above worst-case fold training. */
const ML_TRAIN_TIMEOUT_MS = {
  RL_PPO_AGENT: 900_000, // 15 min
  deep: 600_000, // 10 min
  default: 300_000, // 5 min (GBDT + enrich)
};
const ML_VALIDATE_TIMEOUT_MS = {
  RL_PPO_AGENT: 1_200_000, // 20 min (multi-fold PPO)
  deep: 600_000, // 10 min
  default: 300_000, // 5 min
};

function mlJobTimeoutMs(strategy, kind = 'validate') {
  const table = kind === 'train' ? ML_TRAIN_TIMEOUT_MS : ML_VALIDATE_TIMEOUT_MS;
  if (strategy === 'RL_PPO_AGENT') return table.RL_PPO_AGENT;
  if (DEEP_ML_STRATEGIES.has(strategy)) return table.deep;
  return table.default;
}

const TRAINING_WINDOWS = [
  { value: '1', label: '1 month' },
  { value: '3', label: '3 months' },
  { value: '6', label: '6 months' },
  { value: '12', label: '12 months' },
];

const METRIC_LABELS = {
  total_timesteps: 'Timesteps',
  episodes: 'Episodes',
  mean_return_pct: 'Mean return',
  best_mean_return: 'Best return',
  mean_trades_per_episode: 'Trades / ep',
  hidden_dim: 'Hidden dim',
  val_accuracy: 'Val accuracy',
  accuracy: 'Accuracy',
  auc_roc: 'AUC-ROC',
  val_loss: 'Val loss',
  train_loss: 'Train loss',
  log_loss: 'Log loss',
  sharpe: 'Sharpe',
  pbo: 'PBO',
  mean_oos_accuracy: 'Mean OOS acc',
};

const INT_METRIC_KEYS = new Set([
  'total_timesteps',
  'episodes',
  'hidden_dim',
  'n_folds',
  'successful_folds',
  'sample_count',
  'train_samples',
  'val_samples',
  'n_samples',
]);

const PCT_METRIC_KEYS = new Set([
  'val_accuracy',
  'accuracy',
  'auc_roc',
  'pbo',
  'mean_oos_accuracy',
  'mean_return_pct',
  'best_mean_return',
]);

function fmtMetric(v, digits = 3, key = '') {
  if (v == null || Number.isNaN(Number(v))) return null;
  const n = Number(v);
  if (INT_METRIC_KEYS.has(key) || Number.isInteger(n)) {
    return Math.abs(n) >= 1000 ? n.toLocaleString() : String(Math.round(n));
  }
  if (PCT_METRIC_KEYS.has(key)) {
    // RL returns are stored as percent points (e.g. -0.086 = -0.086%);
    // classifier probs are 0–1 fractions.
    if (key === 'mean_return_pct' || key === 'best_mean_return') {
      if (Math.abs(n) <= 1) return `${n.toFixed(3)}%`;
      return `${n.toFixed(2)}%`;
    }
    if (n >= 0 && n <= 1) return `${(n * 100).toFixed(1)}%`;
  }
  if (Math.abs(n) >= 100) return n.toFixed(1);
  if (Math.abs(n) >= 10) return n.toFixed(2);
  return n.toFixed(digits);
}

function metricLabel(key) {
  return METRIC_LABELS[key] || key.replace(/_/g, ' ');
}

function pickMetricEntries(metrics) {
  if (!metrics || typeof metrics !== 'object') return [];
  const preferred = [
    'total_timesteps',
    'episodes',
    'mean_return_pct',
    'best_mean_return',
    'mean_trades_per_episode',
    'hidden_dim',
    'val_accuracy',
    'accuracy',
    'auc_roc',
    'val_loss',
    'sharpe',
    'pbo',
  ];
  const entries = preferred
    .filter((k) => metrics[k] != null && typeof metrics[k] !== 'object')
    .map((k) => [k, metrics[k]]);
  Object.entries(metrics).forEach(([k, v]) => {
    if (
      typeof v === 'number'
      && Number.isFinite(v)
      && !preferred.includes(k)
      && !k.startsWith('last_')
      && entries.length < 8
    ) {
      entries.push([k, v]);
    }
  });
  return entries;
}

function MetricChips({ metrics }) {
  const entries = pickMetricEntries(metrics);
  if (!entries.length) return null;
  return (
    <div className="ml-training__metrics-block">
      <div className="ml-training__metrics-head">
        <h4 className="ml-training__section-title">Latest model metrics</h4>
        <span className="ml-training__header-meta">{entries.length} fields</span>
      </div>
      <div className="ml-training__metrics">
        {entries.map(([k, v], i) => (
          <span
            key={k}
            className={cn('ml-training__metric-chip', i === 0 && 'ml-training__metric-chip--primary')}
            title={k}
          >
            <span className="ml-training__metric-key">{metricLabel(k)}</span>
            <strong className="num-mono">{fmtMetric(v, 3, k) ?? String(v)}</strong>
          </span>
        ))}
      </div>
    </div>
  );
}

function normalizeCurveHistory(history, trainHistory, metrics) {
  const trainRows = Array.isArray(trainHistory) ? trainHistory : [];
  if (trainRows.some((h) => h && h.return_pct != null)) {
    return {
      mode: 'returns',
      title: 'Episode returns',
      rows: trainRows
        .filter((h) => h && h.return_pct != null)
        .map((h, i) => ({
          i: h.episode ?? i + 1,
          primary: Number(h.return_pct),
        })),
      primaryLabel: 'return',
      secondaryLabel: null,
    };
  }

  const lossRows = Array.isArray(history)
    ? history.filter((h) => h && (h.val_loss != null || h.train_loss != null || h.return_pct != null))
    : [];
  if (lossRows.some((h) => h.return_pct != null) && !lossRows.some((h) => h.train_loss != null)) {
    return {
      mode: 'returns',
      title: 'Episode returns',
      rows: lossRows.map((h, i) => ({
        i: h.episode ?? h.epoch ?? i + 1,
        primary: Number(h.return_pct),
      })),
      primaryLabel: 'return',
      secondaryLabel: null,
    };
  }
  if (lossRows.length >= 2) {
    return {
      mode: 'loss',
      title: 'Training curve',
      rows: lossRows.map((h, i) => ({
        i: h.epoch ?? i + 1,
        primary: h.train_loss != null ? Number(h.train_loss) : null,
        secondary: h.val_loss != null ? Number(h.val_loss) : null,
      })),
      primaryLabel: 'train',
      secondaryLabel: 'val',
    };
  }

  const last10 = Array.isArray(metrics?.last_10_returns) ? metrics.last_10_returns : [];
  if (last10.length >= 2) {
    return {
      mode: 'returns',
      title: 'Recent episode returns',
      rows: last10.map((v, i) => ({ i: i + 1, primary: Number(v) })),
      primaryLabel: 'return',
      secondaryLabel: null,
    };
  }
  return null;
}

function LossHistoryChart({ history, trainHistory, metrics }) {
  const curve = normalizeCurveHistory(history, trainHistory, metrics);
  if (!curve || curve.rows.length < 2) {
    return (
      <div className="ml-training__loss ml-training__loss--empty">
        <p className="ml-training__subsection-label">Training curve</p>
        <p className="text-[0.65rem] text-muted-foreground">
          No epoch / episode history yet. Run Trigger retrain to populate the curve.
        </p>
      </div>
    );
  }

  const vals = curve.rows.flatMap((r) => [r.primary, r.secondary].filter((n) => Number.isFinite(n)));
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const pad = Math.max(Math.abs(max - min) * 0.08, 1e-6);
  const yMin = min - pad;
  const yMax = max + pad;
  const span = Math.max(yMax - yMin, 1e-9);
  const w = 360;
  const h = 72;
  const left = 2;
  const right = w - 2;

  const toPath = (key) => {
    const pts = curve.rows
      .map((r, i) => {
        const v = Number(r[key]);
        if (!Number.isFinite(v)) return null;
        const x = left + (i / Math.max(curve.rows.length - 1, 1)) * (right - left);
        const y = h - ((v - yMin) / span) * (h - 10) - 5;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .filter(Boolean);
    if (pts.length < 2) return null;
    return pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p}`).join(' ');
  };

  const primaryD = toPath('primary');
  const secondaryD = curve.secondaryLabel ? toPath('secondary') : null;
  const last = curve.rows[curve.rows.length - 1];
  const fmtY = (n) => (curve.mode === 'returns'
    ? `${Number(n).toFixed(2)}%`
    : Number(n).toFixed(4));

  return (
    <div className="ml-training__loss">
      <div className="ml-training__loss-head">
        <p className="ml-training__subsection-label">{curve.title}</p>
        <span className="text-[0.5rem] text-muted-foreground">
          {curve.primaryLabel && (
            <span className="ml-training__loss-legend ml-training__loss-legend--train">
              {curve.primaryLabel}
            </span>
          )}
          {curve.secondaryLabel && (
            <>
              {' · '}
              <span className="ml-training__loss-legend ml-training__loss-legend--val">
                {curve.secondaryLabel}
              </span>
            </>
          )}
        </span>
      </div>
      <div className="ml-training__loss-plot">
        <div className="ml-training__loss-ylabels num-mono" aria-hidden>
          <span>{fmtY(yMax)}</span>
          <span>{fmtY(yMin)}</span>
        </div>
        <svg viewBox={`0 0 ${w} ${h}`} className="ml-training__loss-svg" aria-label={curve.title}>
          <line x1={left} y1={h / 2} x2={right} y2={h / 2} className="ml-training__loss-grid" />
          {primaryD && (
            <path d={primaryD} className="ml-training__loss-path ml-training__loss-path--train" fill="none" />
          )}
          {secondaryD && (
            <path d={secondaryD} className="ml-training__loss-path ml-training__loss-path--val" fill="none" />
          )}
        </svg>
      </div>
      <p className="ml-training__loss-footer num-mono">
        {curve.rows.length} {curve.mode === 'returns' ? 'episodes' : 'epochs'}
        {Number.isFinite(last?.primary) ? ` · last ${fmtY(last.primary)}` : ''}
        {Number.isFinite(last?.secondary) ? ` · val ${fmtY(last.secondary)}` : ''}
      </p>
    </div>
  );
}

function formatElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m > 0 ? `${m}m ${String(r).padStart(2, '0')}s` : `${r}s`;
}

function JobProgressBar({ job }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!job?.active) return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [job?.active, job?.startedAt]);

  if (!job?.active) return null;

  const elapsed = Math.max(0, now - (job.startedAt || now));
  const timeoutMs = Math.max(job.timeoutMs || 60_000, 15_000);
  // Asymptotic estimate — never claims 100% until the request finishes.
  const ratio = Math.min(0.94, 1 - Math.exp(-elapsed / (timeoutMs * 0.45)));
  const pct = Math.max(2, Math.round(ratio * 100));
  const phases = job.phases || [];
  const phaseIdx = phases.findIndex((p) => pct < p.until);
  const phase = phases[phaseIdx >= 0 ? phaseIdx : Math.max(phases.length - 1, 0)];

  return (
    <div className="ml-training__progress" role="status" aria-live="polite">
      <div className="ml-training__progress-head">
        <span className="ml-training__progress-label">
          <Loader2 size={12} className="animate-spin" aria-hidden />
          {job.label}
        </span>
        <span className="ml-training__progress-meta num-mono">
          {pct}% · {formatElapsed(elapsed)}
          {timeoutMs >= 60_000 ? ` / ~${Math.round(timeoutMs / 60_000)}m` : ''}
        </span>
      </div>
      <div
        className="ml-training__progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label={job.label}
      >
        <div className="ml-training__progress-fill" style={{ width: `${pct}%` }} />
      </div>
      {phase?.label && (
        <p className="ml-training__progress-phase">{phase.label}</p>
      )}
    </div>
  );
}

function trainJobPhases(strategy) {
  if (strategy === 'RL_PPO_AGENT') {
    return [
      { until: 12, label: 'Fetching & enriching candles…' },
      { until: 88, label: 'Running PPO rollouts / policy updates…' },
      { until: 96, label: 'Exporting ONNX policy…' },
      { until: 100, label: 'Saving model artifacts…' },
    ];
  }
  if (DEEP_ML_STRATEGIES.has(strategy)) {
    return [
      { until: 15, label: 'Fetching & enriching candles…' },
      { until: 85, label: 'Training neural network…' },
      { until: 100, label: 'Exporting & saving artifacts…' },
    ];
  }
  return [
    { until: 20, label: 'Fetching & enriching candles…' },
    { until: 80, label: 'Fitting model…' },
    { until: 100, label: 'Saving artifacts…' },
  ];
}

function validateJobPhases(strategy) {
  if (strategy === 'RL_PPO_AGENT') {
    return [
      { until: 10, label: 'Loading candles for validation…' },
      { until: 90, label: 'Walk-forward folds (RL fast mode)…' },
      { until: 100, label: 'Aggregating fold metrics…' },
    ];
  }
  return [
    { until: 12, label: 'Loading candles for validation…' },
    { until: 70, label: 'Walk-forward folds…' },
    { until: 92, label: 'Computing PBO…' },
    { until: 100, label: 'Aggregating results…' },
  ];
}

function DatasetBrowser({
  dataset,
  versions,
  activatingVersionId,
  deletingVersionId,
  onActivateVersion,
  onDeleteVersion,
  onCopyPin,
}) {
  if (!dataset && !(versions && versions.length)) return null;
  const labels = dataset?.label_distribution;
  const features = Array.isArray(dataset?.feature_names) ? dataset.feature_names : [];
  const top = Array.isArray(dataset?.top_features) ? dataset.top_features.slice(0, 8) : [];
  const versionBusy = Boolean(activatingVersionId || deletingVersionId);
  return (
    <section className="ml-training__dataset">
      <div className="ml-training__card-head">
        <h4 className="ml-training__section-title">Dataset & versions</h4>
        <span className="ml-training__header-meta">
          Activate sets the live root · Delete removes a non-active snapshot · pin via Model version pin
        </span>
      </div>
      <div className="ml-training__dataset-grid">
        <div className="ml-training__dataset-main">
          {dataset && (
            <div className="ml-training__dataset-stats">
              <div>
                <span className="text-muted-foreground">Samples</span>
                <p className="num-mono font-medium">
                  {dataset.sample_count ?? dataset.train_samples ?? '—'}
                  {dataset.val_samples != null ? ` / val ${dataset.val_samples}` : ''}
                </p>
              </div>
              <div>
                <span className="text-muted-foreground">Schema</span>
                <p className="num-mono font-medium">
                  {dataset.feature_schema_version != null
                    ? `v${dataset.feature_schema_version}`
                    : '—'}
                  {dataset.lookback != null ? ` · lb ${dataset.lookback}` : ''}
                </p>
              </div>
              <div>
                <span className="text-muted-foreground">Type</span>
                <p className="num-mono font-medium">{dataset.model_type || '—'}</p>
              </div>
            </div>
          )}
          {labels && typeof labels === 'object' && (
            <div>
              <p className="ml-training__subsection-label">Label distribution</p>
              <div className="ml-training__label-dist">
                {Object.entries(labels).map(([k, v]) => (
                  <span key={k}>
                    <span className="ml-training__metric-key">{k}</span>
                    <strong className="num-mono">{v}</strong>
                  </span>
                ))}
              </div>
            </div>
          )}
          {features.length > 0 && (
            <p className="ml-training__feature-line">
              Features ({features.length}):{' '}
              <span className="num-mono">
                {features.slice(0, 12).join(', ')}
                {features.length > 12 ? ` +${features.length - 12}` : ''}
              </span>
            </p>
          )}
          {top.length > 0 && (
            <p className="ml-training__feature-line">
              Top features:{' '}
              <span className="num-mono">
                {top.map((f) => (typeof f === 'string' ? f : f?.name || f?.feature)).filter(Boolean).join(', ')}
              </span>
            </p>
          )}
        </div>
        {Array.isArray(versions) && versions.length > 0 && (
          <div className="ml-training__dataset-versions">
            <p className="ml-training__subsection-label">Version history</p>
            <ul className="ml-training__version-list">
              {versions.slice(0, 12).map((v) => {
                const id = v.version_id || v.trained_at;
                const activating = activatingVersionId && (
                  activatingVersionId === v.version_id
                  || activatingVersionId === v.trained_at
                );
                const deleting = deletingVersionId && (
                  deletingVersionId === v.version_id
                  || deletingVersionId === v.trained_at
                );
                const pinValue = v.trained_at || v.version_id || '';
                return (
                  <li
                    key={id}
                    className={cn(
                      'ml-training__version-row',
                      v.is_current && 'ml-training__version-row--current',
                    )}
                  >
                    <div className="ml-training__version-meta num-mono">
                      <span className="ml-training__version-id">{v.version_id || '—'}</span>
                      <span className="text-muted-foreground">
                        {v.trained_at ? new Date(v.trained_at).toLocaleString() : '—'}
                        {v.is_current ? ' · current' : ''}
                        {v.sample_count != null ? ` · n=${v.sample_count}` : ''}
                      </span>
                    </div>
                    <div className="ml-training__version-actions">
                      {pinValue && onCopyPin && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-6 px-1.5 text-[0.6rem]"
                          title="Copy pin value for bot config model_version"
                          onClick={() => onCopyPin(pinValue)}
                        >
                          Copy pin
                        </Button>
                      )}
                      {v.is_current ? (
                        <span className="ml-training__version-badge">Active</span>
                      ) : (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-6 px-1.5 text-[0.6rem] gap-1"
                          disabled={versionBusy || !onActivateVersion}
                          onClick={() => onActivateVersion?.(v)}
                        >
                          {activating ? <Loader2 size={10} className="animate-spin" /> : null}
                          Use this
                        </Button>
                      )}
                      {!v.is_current && onDeleteVersion && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-6 px-1.5 text-[0.6rem] gap-1 text-destructive hover:text-destructive"
                          disabled={versionBusy}
                          title="Delete this snapshot from disk (cannot undo)"
                          onClick={() => onDeleteVersion(v)}
                        >
                          {deleting ? <Loader2 size={10} className="animate-spin" /> : <Trash2 size={10} />}
                          Delete
                        </Button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

export default function ModelTrainingDashboard() {
  const activeSymbol = useStore((s) => s.activeSymbol);
  const botStrategy = useStore((s) => s.botStrategy);
  const mlSession = useSyncExternalStore(
    subscribeMlTrainingSession,
    getMlTrainingSession,
    getMlTrainingSession,
  );

  const [strategy, setStrategy] = useState(
    () => (isMlStrategy(botStrategy) ? botStrategy : 'ML_SIGNAL_BOOST'),
  );
  const [trainingWindow, setTrainingWindow] = useState('3');
  const [status, setStatus] = useState(null);
  const [inventory, setInventory] = useState([]);
  const [retrainActions, setRetrainActions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [activatingVersionId, setActivatingVersionId] = useState(null);
  const [deletingVersionId, setDeletingVersionId] = useState(null);
  const statusRef = useRef(status);
  statusRef.current = status;

  const jobMatches = mlSession.symbol === activeSymbol && mlSession.strategy === strategy;
  const training = Boolean(jobMatches && mlSession.training);
  const validating = Boolean(jobMatches && mlSession.validating);
  const jobProgress = jobMatches ? mlSession.jobProgress : null;
  const validation = jobMatches ? mlSession.validation : null;
  const busyElsewhere = Boolean(
    (mlSession.training || mlSession.validating)
    && !jobMatches
    && (mlSession.symbol || mlSession.strategy),
  );

  const meta = getStrategyMeta(strategy);

  const startJobProgress = useCallback((kind, strat, symbol) => {
    const timeoutMs = mlJobTimeoutMs(strat, kind === 'train' ? 'train' : 'validate');
    const progress = {
      active: true,
      kind,
      startedAt: Date.now(),
      timeoutMs,
      label: kind === 'train'
        ? `Retraining ${getStrategyMeta(strat).shortLabel || strat}`
        : `Walk-forward${strat === 'RL_PPO_AGENT' ? '' : ' + PBO'} · ${getStrategyMeta(strat).shortLabel || strat}`,
      phases: kind === 'train' ? trainJobPhases(strat) : validateJobPhases(strat),
    };
    const next = beginMlJob({ kind, strategy: strat, symbol, jobProgress: progress });
    return next.jobToken;
  }, []);

  const finishTimersRef = useRef(new Set());

  useEffect(() => () => {
    for (const t of finishTimersRef.current) clearTimeout(t);
    finishTimersRef.current.clear();
  }, []);

  const finishJobProgress = useCallback((token, extras = {}) => {
    finishMlJob(token, extras);
    const t = window.setTimeout(() => {
      finishTimersRef.current.delete(t);
      clearMlJobProgress(token);
    }, 600);
    finishTimersRef.current.add(t);
  }, []);

  const fetchInventory = useCallback(async () => {
    if (!activeSymbol) {
      setInventory([]);
      return;
    }
    const rows = await Promise.all(
      ML_STRATEGIES.map(async (id) => {
        try {
          const body = await apiRequest(
            `/api/v1/ml/model-status?symbol=${encodeURIComponent(activeSymbol)}&strategy=${encodeURIComponent(id)}`,
          );
          if (body) setCachedModelStatus(activeSymbol, id, body);
          return {
            strategy: id,
            trained: Boolean(body?.trained),
            trained_at: body?.trained_at,
            metrics: body?.metrics || {},
            error: body?.error,
          };
        } catch (err) {
          if (isAbortError(err)) {
            const cached = getCachedModelStatus(activeSymbol, id);
            if (cached) {
              return {
                strategy: id,
                trained: Boolean(cached.trained),
                trained_at: cached.trained_at,
                metrics: cached.metrics || {},
              };
            }
          }
          const cached = getCachedModelStatus(activeSymbol, id);
          if (cached?.trained) {
            return {
              strategy: id,
              trained: true,
              trained_at: cached.trained_at,
              metrics: cached.metrics || {},
              stale: true,
            };
          }
          return { strategy: id, trained: false, error: err.message };
        }
      }),
    );
    setInventory(rows);
  }, [activeSymbol]);

  const fetchRetrainQueue = useCallback(async () => {
    try {
      const body = await apiRequest('/api/v1/ml/retrain-status');
      setRetrainActions(Array.isArray(body?.retrain_actions) ? body.retrain_actions : []);
    } catch (err) {
      if (!isAbortError(err)) setRetrainActions([]);
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    if (!activeSymbol || !strategy) return;
    setLoading(true);
    try {
      const body = await apiRequest(
        `/api/v1/ml/model-status?symbol=${encodeURIComponent(activeSymbol)}&strategy=${encodeURIComponent(strategy)}`,
      );
      const next = resolveModelStatusFetch(activeSymbol, strategy, {
        body,
        previous: statusRef.current,
      });
      setStatus(next);
    } catch (err) {
      const next = resolveModelStatusFetch(activeSymbol, strategy, {
        error: err,
        previous: statusRef.current,
      });
      setStatus(next);
    } finally {
      setLoading(false);
    }
  }, [activeSymbol, strategy]);

  const refreshAll = useCallback(async () => {
    await Promise.all([fetchStatus(), fetchInventory(), fetchRetrainQueue()]);
  }, [fetchStatus, fetchInventory, fetchRetrainQueue]);

  useEffect(() => {
    if (isMlStrategy(botStrategy) && botStrategy !== strategy) {
      setStrategy(botStrategy);
    }
  }, [botStrategy]); // eslint-disable-line react-hooks/exhaustive-deps -- sync bot picker → dashboard

  useEffect(() => {
    const cached = getCachedModelStatus(activeSymbol, strategy);
    if (cached) setStatus(cached);
    refreshAll();
  }, [refreshAll]);

  // Re-attach to an in-flight job when remounting the panel mid-train.
  useEffect(() => {
    if (!jobMatches) return undefined;
    if (!mlSession.training && !mlSession.validating) return undefined;
    const id = window.setInterval(() => {
      fetchStatus();
    }, 15_000);
    return () => window.clearInterval(id);
  }, [jobMatches, mlSession.training, mlSession.validating, fetchStatus]);

  const handleActivateVersion = async (version) => {
    if (!activeSymbol || !strategy || !version || activatingVersionId) return;
    const pin = version.trained_at || version.version_id;
    if (!pin) {
      toast.error('Version has no trained_at / version_id');
      return;
    }
    setActivatingVersionId(version.version_id || pin);
    try {
      const body = await apiRequest('/api/v1/ml/activate-version', {
        method: 'POST',
        body: {
          symbol: activeSymbol,
          strategy,
          model_version: pin,
          version_id: version.version_id,
        },
        timeoutMs: 60_000,
      });
      if (body?.ok) {
        setCachedModelStatus(activeSymbol, strategy, body);
        setStatus(body);
        toast.success(
          `Activated ${body.activated_version_id || pin} as current for ${strategy} / ${activeSymbol}`,
        );
        refreshAll();
      } else {
        toast.error(body?.error || 'Failed to activate version');
      }
    } catch (err) {
      toast.error(err.message || 'Activate request failed');
    } finally {
      setActivatingVersionId(null);
    }
  };

  const handleCopyPin = async (pinValue) => {
    try {
      await navigator.clipboard.writeText(String(pinValue));
      toast.message('Pin copied — paste into bot config → Model version pin');
    } catch {
      toast.message(`Pin: ${pinValue}`);
    }
  };

  const handleDeleteVersion = async (version) => {
    if (!activeSymbol || !strategy || !version || activatingVersionId || deletingVersionId) return;
    if (version.is_current) {
      toast.error('Activate another version before deleting the active one');
      return;
    }
    const pin = version.trained_at || version.version_id;
    if (!pin) {
      toast.error('Version has no trained_at / version_id');
      return;
    }
    const label = version.version_id || pin;
    if (!window.confirm(
      `Delete model version ${label} for ${strategy} / ${activeSymbol}?\n\nThis removes the snapshot from disk and cannot be undone.`,
    )) {
      return;
    }
    setDeletingVersionId(version.version_id || pin);
    try {
      const body = await apiRequest('/api/v1/ml/delete-version', {
        method: 'POST',
        body: {
          symbol: activeSymbol,
          strategy,
          model_version: pin,
          version_id: version.version_id,
        },
        timeoutMs: 60_000,
      });
      if (body?.ok) {
        setCachedModelStatus(activeSymbol, strategy, body);
        setStatus(body);
        toast.success(`Deleted version ${body.deleted_version_id || label}`);
        refreshAll();
      } else {
        toast.error(body?.error || 'Failed to delete version');
      }
    } catch (err) {
      toast.error(err.message || 'Delete request failed');
    } finally {
      setDeletingVersionId(null);
    }
  };

  const handleTrain = async () => {
    if (training || validating || busyElsewhere || !activeSymbol) return;
    setMlValidation(null);
    const trainTimeoutMs = mlJobTimeoutMs(strategy, 'train');
    const token = startJobProgress('train', strategy, activeSymbol);
    try {
      if (DEEP_ML_STRATEGIES.has(strategy) || strategy === 'RL_PPO_AGENT') {
        toast.message(
          `Training ${strategy}… allowing up to ${Math.round(trainTimeoutMs / 60_000)} min`,
        );
      }
      const body = await apiRequest('/api/v1/ml/train', {
        method: 'POST',
        body: {
          symbol: activeSymbol,
          strategy,
          config: { training_window_months: Number(trainingWindow) },
        },
        timeoutMs: trainTimeoutMs,
      });
      if (body?.ok) {
        toast.success(`Training complete for ${strategy} / ${activeSymbol}`);
        const next = { trained: true, ...body };
        setCachedModelStatus(activeSymbol, strategy, next);
        setStatus(next);
      } else {
        toast.error(body?.error || 'Training failed');
        setStatus({ trained: false, error: body?.error || 'Training failed' });
      }
    } catch (err) {
      if (!isAbortError(err)) {
        toast.error(err.message || 'Training request failed');
        setStatus({ trained: false, error: err.message });
      }
    } finally {
      finishJobProgress(token);
      refreshAll();
    }
  };

  const handleValidate = async () => {
    if (validating || training || busyElsewhere || !activeSymbol) return;
    setMlValidation(null);
    const isRl = strategy === 'RL_PPO_AGENT';
    const isDeep = DEEP_ML_STRATEGIES.has(strategy);
    const validateTimeoutMs = mlJobTimeoutMs(strategy, 'validate');
    const token = startJobProgress('validate', strategy, activeSymbol);
    try {
      toast.message(
        isRl
          ? `Running RL walk-forward (fast mode, no PBO)… up to ${Math.round(validateTimeoutMs / 60_000)} min`
          : isDeep
            ? `Running walk-forward + PBO… up to ${Math.round(validateTimeoutMs / 60_000)} min`
            : 'Running walk-forward + PBO… usually under 5 minutes',
      );
      const body = await apiRequest('/api/v1/ml/validate', {
        method: 'POST',
        body: {
          symbol: activeSymbol,
          strategy,
          n_folds: isRl ? 2 : 3,
          mode: 'rolling',
          // Full PBO re-trains every combo — too heavy for PPO in the dock.
          pbo: !isRl,
          pbo_segments: 4,
          config: {
            training_window_months: Number(trainingWindow),
            symbol: activeSymbol,
            model_symbol: activeSymbol,
            _wf_mode: true,
            validate_max_bars: isRl ? 1200 : 2500,
            pbo_max_combos: 4,
            ...(isRl
              ? { total_timesteps: 2048, n_steps: 512, ppo_epochs: 2, hidden_dim: 64 }
              : {}),
          },
        },
        timeoutMs: validateTimeoutMs,
      });
      setMlValidation(body);
      if (body?.ok) {
        toast.success('Walk-forward validation finished');
      } else {
        const foldErr = Array.isArray(body?.folds)
          ? body.folds.find((f) => f?.error)?.error
          : null;
        toast.error(body?.error || foldErr || 'Validation failed');
      }
    } catch (err) {
      const msg = err?.message || String(err) || 'Validation request failed';
      const timedOut = /timed out|aborted|failed to fetch|network/i.test(msg);
      const badJson = /invalid json|internal server error/i.test(msg);
      const friendly = timedOut
        ? `${msg} — server may still be finishing folds. Wait, then Refresh; recycle only if the backend is unresponsive.`
        : badJson
          ? 'Validation hit a server error (non-JSON response). Recycle Massive backend and retry — RL walk-forward needs the latest ONNX export fix.'
          : msg;
      setMlValidation({ ok: false, error: friendly });
      if (!isAbortError(err)) {
        toast.error(
          timedOut
            ? `Validation timed out after ${Math.round(validateTimeoutMs / 60_000)} min`
            : badJson
              ? 'Validation failed — recycle backend and retry'
              : msg,
        );
      }
    } finally {
      finishJobProgress(token);
    }
  };

  const statusLabel = training
    ? 'Training'
    : validating
      ? 'Validating'
      : status?.trained
        ? (status?.stale ? 'Ready (cached)' : 'Ready')
        : status?.error
          ? 'Failed'
          : 'Idle';

  const trainedCount = inventory.filter((r) => r.trained).length;

  return (
    <div className="dock-panel dock-panel--ml-training overflow-y-auto h-full">
      <header
        title="Train and validate ML models per symbol. Optimizer Lab handles hyperparameter sweeps only."
      >
        <h3 className="ml-training__title">
          <BrainCircuit size={16} aria-hidden />
          Model Training
        </h3>
        {status?.trained_at && (
          <span className="ml-training__header-meta num-mono">
            {new Date(status.trained_at).toLocaleString()}
          </span>
        )}
      </header>

      <section className="ml-training__controls">
        <div className="ml-training__controls-grid">
          <div className="ml-training__field">
            <Label className="text-xs">Symbol</Label>
            <p className="text-sm font-medium num-mono">{activeSymbol || '—'}</p>
          </div>
          <div className="ml-training__field">
            <Label className="text-xs">Strategy</Label>
            <Select value={strategy} onValueChange={setStrategy}>
              <SelectTrigger size="sm" className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ML_STRATEGIES.map((id) => (
                  <SelectItem key={id} value={id} className="text-xs">
                    {getStrategyMeta(id).label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="ml-training__field">
            <Label className="text-xs">Training window</Label>
            <Select value={trainingWindow} onValueChange={setTrainingWindow}>
              <SelectTrigger size="sm" className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TRAINING_WINDOWS.map((w) => (
                  <SelectItem key={w.value} value={w.value} className="text-xs">
                    {w.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="ml-training__field">
            <Label className="text-xs">Status</Label>
            <p className="text-sm flex items-center gap-2">
              {(training || validating || loading) && (
                <Loader2 size={14} className="animate-spin" aria-hidden />
              )}
              {statusLabel}
              <span className="text-xs text-muted-foreground">({meta.shortLabel})</span>
            </p>
          </div>
        </div>

        <JobProgressBar job={jobProgress} />

        {busyElsewhere && (
          <p className="text-xs text-amber-400/90">
            Job running for {mlSession.strategy} / {mlSession.symbol} — switch back to that
            pair to watch progress (it keeps going in the background).
          </p>
        )}

        <MetricChips metrics={status?.metrics} />
        <LossHistoryChart
          history={status?.loss_history}
          trainHistory={status?.train_history}
          metrics={status?.metrics}
        />
        {status?.fetch_error && status?.trained && (
          <p className="text-xs text-muted-foreground">
            Showing last known status ({status.fetch_error}).
          </p>
        )}
        {status?.error && !status?.trained && (
          <p className="text-xs text-destructive">{status.error}</p>
        )}

        <div className="ml-training__actions">
          {(status?.artifact || status?.version_id) && (
            <span className="ml-training__artifact num-mono">
              {status.artifact || 'artifact'}
              {status.version_id ? ` · ${status.version_id}` : status.trained_at ? ` · ${status.trained_at}` : ''}
            </span>
          )}
          <Button
            type="button"
            size="sm"
            className="h-8 text-xs gap-1"
            disabled={training || validating || busyElsewhere || !activeSymbol}
            onClick={handleTrain}
          >
            {training ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            Trigger retrain
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 text-xs gap-1"
            disabled={training || validating || busyElsewhere || !activeSymbol}
            onClick={handleValidate}
          >
            {validating ? <Loader2 size={14} className="animate-spin" /> : <FlaskConical size={14} />}
            Walk-forward + PBO
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 text-xs gap-1"
            disabled={loading}
            onClick={refreshAll}
          >
            <RefreshCw size={14} />
            Refresh
          </Button>
        </div>
      </section>

      <DatasetBrowser
        dataset={status?.dataset}
        versions={status?.versions}
        activatingVersionId={activatingVersionId}
        deletingVersionId={deletingVersionId}
        onActivateVersion={handleActivateVersion}
        onDeleteVersion={handleDeleteVersion}
        onCopyPin={handleCopyPin}
      />

      {validation && (
        <section className="ml-training__card">
          <h4 className="ml-training__section-title">Validation result</h4>
          {validation.ok === false && (
            <p className="text-xs text-destructive">
              {validation.error
                || (Array.isArray(validation.folds) && validation.folds.find((f) => f?.error)?.error)
                || 'Validation failed'}
            </p>
          )}
          {validation.ok && (
            <div className="grid gap-2 sm:grid-cols-3 text-xs">
              {(validation.mean_accuracy ?? validation.aggregate?.mean_oos_accuracy) != null && (
                <div>
                  <span className="text-muted-foreground">Mean OOS accuracy</span>
                  <p className="num-mono font-medium">
                    {fmtMetric(validation.mean_accuracy ?? validation.aggregate?.mean_oos_accuracy)}
                  </p>
                </div>
              )}
              {validation.n_folds != null && (
                <div>
                  <span className="text-muted-foreground">Folds</span>
                  <p className="num-mono font-medium">
                    {validation.successful_folds ?? validation.n_folds}/{validation.n_folds}
                  </p>
                </div>
              )}
              {validation.pbo?.pbo != null && (
                <div>
                  <span className="text-muted-foreground">PBO</span>
                  <p className={cn(
                    'num-mono font-medium',
                    Number(validation.pbo.pbo) > 0.5 && 'text-destructive',
                  )}
                  >
                    {fmtMetric(validation.pbo.pbo)}
                  </p>
                </div>
              )}
              {validation.recommendation && (
                <div className="sm:col-span-3">
                  <span className="text-muted-foreground">Recommendation</span>
                  <p className="text-xs">{validation.recommendation}</p>
                </div>
              )}
            </div>
          )}
          {Array.isArray(validation.folds) && validation.folds.length > 0 && (
            <ul className="ml-training__fold-list text-[0.65rem] text-muted-foreground">
              {validation.folds.slice(0, 8).map((f, i) => (
                <li key={i} className={cn('num-mono', f.ok === false && 'text-destructive')}>
                  fold {f.fold ?? i + 1}
                  {f.ok === false
                    ? `: FAIL ${f.error || '—'}`
                    : `: acc ${fmtMetric(f.accuracy ?? f.oos_metrics?.accuracy ?? f.val_accuracy) ?? '—'}`}
                  {(f.n_samples ?? f.oos_metrics?.n_signals ?? f.test_bars) != null
                    ? ` · n=${f.n_samples ?? f.oos_metrics?.n_signals ?? f.test_bars}`
                    : ''}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <section className="ml-training__card">
        <div className="ml-training__card-head">
          <h4 className="ml-training__section-title">Model inventory</h4>
          <span className="ml-training__header-meta">
            {trainedCount}/{ML_STRATEGIES.length} trained · {activeSymbol || '—'}
          </span>
        </div>
        <ul className="ml-training__inventory">
          {inventory.map((row) => {
            const rowMeta = getStrategyMeta(row.strategy);
            const selected = row.strategy === strategy;
            return (
              <li key={row.strategy}>
                <button
                  type="button"
                  className={cn(
                    'ml-training__inventory-row',
                    selected && 'ml-training__inventory-row--active',
                  )}
                  onClick={() => setStrategy(row.strategy)}
                >
                  <span className="ml-training__inventory-icon" aria-hidden>
                    {row.trained
                      ? <CheckCircle2 size={14} className="text-emerald-400" />
                      : <XCircle size={14} className="text-muted-foreground/60" />}
                  </span>
                  <span className="ml-training__inventory-name">
                    {rowMeta.shortLabel || rowMeta.label}
                    <span className="text-muted-foreground font-normal"> · {row.strategy}</span>
                  </span>
                  <span className="ml-training__inventory-meta num-mono">
                    {row.trained_at
                      ? new Date(row.trained_at).toLocaleDateString()
                      : 'not trained'}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      {retrainActions.length > 0 && (
        <section className="ml-training__card ml-training__card--warn">
          <h4 className="ml-training__section-title">Retrain queue</h4>
          <ul className="space-y-1 text-[0.65rem] text-muted-foreground">
            {retrainActions.slice(0, 8).map((a, i) => (
              <li key={`${a.strategy}-${a.symbol}-${i}`} className="num-mono">
                {a.strategy} / {a.symbol}
                {a.reason ? ` — ${a.reason}` : ''}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
