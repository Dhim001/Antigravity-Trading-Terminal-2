/**
 * Model Training Dashboard — inventory, train, validate, retrain queue.
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  BrainCircuit,
  CheckCircle2,
  ExternalLink,
  FlaskConical,
  Loader2,
  PanelLeft,
  Play,
  RefreshCw,
  Trash2,
  XCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import FeatureImportanceChart from '@/components/FeatureImportanceChart';
import { useStore } from '@/store/useStore';
import { apiRequest, isAbortError } from '@/api/client';
import { getStrategyMeta, isDeepMlStrategy, isMlStrategy, ML_STRATEGY_IDS } from '@/config/strategies';
import { buildChallengerHint } from '@/lib/mlChallengerHint';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import { useVirtualRows, VirtualTablePadding } from '@/components/VirtualTableBody';
import {
  beginMlJob,
  clearMlJobProgress,
  clearMlPollLog,
  finishMlJob,
  getCachedModelStatus,
  getMlTrainingSession,
  resolveModelStatusFetch,
  setCachedModelStatus,
  setMlJobId,
  setMlServerProgress,
  setMlValidation,
  subscribeMlTrainingSession,
  appendMlPollLog,
} from '@/lib/mlTrainingSession';
import {
  formatMlJobBudgetLabel,
  isTransientMlPollError,
  ML_JOB_STATUS_POLL_TIMEOUT_MS,
  ML_JOB_SUBMIT_TIMEOUT_MS,
  mlJobPollDeadlineMs,
  mlJobPollIntervalMs,
  mlJobTimeoutMs,
} from '@/lib/mlJobTimeouts';

const ML_STRATEGIES = ML_STRATEGY_IDS;
const DEEP_ML_STRATEGIES = new Set(
  ML_STRATEGY_IDS.filter((id) => isDeepMlStrategy(id)),
);

/** Defaults: Train uses GPU-era capacity; Validate stays interactive-sized. */
function defaultAdvancedKnobs(strategy, kind = 'validate') {
  const isRl = strategy === 'RL_PPO_AGENT';
  const train = kind === 'train';
  // Validate fold budgets (GPU) — not full production train schedules.
  let epochs = train ? 100 : 12;
  if (strategy === 'TCN_MULTI_HORIZON') epochs = train ? 100 : 10;
  else if (strategy === 'VAE_REGIME_DETECTOR') epochs = train ? 120 : 10;
  else if (strategy === 'GNN_CROSS_ASSET') epochs = train ? 60 : 8;
  else if (strategy === 'TRANSFORMER_SIGNAL') epochs = train ? 80 : 8;
  else if (strategy === 'LSTM_DIRECTION') epochs = train ? 100 : 12;
  return {
    nFolds: isRl ? 2 : 3,
    validateMaxBars: isRl ? 1200 : 2500,
    pboSegments: 4,
    pboMaxCombos: 4,
    // Lab Train previously defaulted PPO to 2048 — that made models look weak.
    totalTimesteps: train ? 200_000 : 2048,
    epochs,
    hiddenDim: train ? (isRl ? 256 : 128) : (isRl ? 64 : 64),
    gbmMaxIter: train ? 300 : 40,
    gbmMaxDepth: train ? 6 : 4,
  };
}

function normalizeTopFeatures(top) {
  if (!Array.isArray(top)) return [];
  return top
    .map((f) => {
      if (typeof f === 'string') return { name: f, importance: 1 };
      const name = f?.name || f?.feature;
      if (!name) return null;
      const importance = Number(f.importance ?? f.gain ?? f.weight ?? 0);
      return {
        name: String(name),
        importance: Number.isFinite(importance) ? importance : 0,
        category: f.category,
      };
    })
    .filter(Boolean);
}

function parsePositiveInt(value, fallback, { min = 1, max = 1_000_000 } = {}) {
  const n = Number.parseInt(String(value), 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

const TRAINING_WINDOWS = [
  { value: '1', label: '1 month', targetBars1m: 12000 },
  { value: '3', label: '3 months', targetBars1m: 25000 },
  { value: '6', label: '6 months', targetBars1m: 40000 },
  { value: '12', label: '12 months', targetBars1m: 50000 },
];

const TRAINING_TIMEFRAMES = [
  { value: '1m', label: '1 minute', secs: 60 },
  { value: '5m', label: '5 minutes', secs: 300 },
  { value: '15m', label: '15 minutes', secs: 900 },
  { value: '1h', label: '1 hour', secs: 3600 },
  { value: '4h', label: '4 hours', secs: 14400 },
];

const ML_LAB_WINDOW_KEY = 'ml-lab-training-window';
const ML_LAB_TF_KEY = 'ml-lab-training-timeframe';

function estimateTrainingBars(monthsValue, tfValue) {
  // Mirror backend ``bar_limit_for_training_window`` (train purpose).
  const months = Number(monthsValue) || 3;
  const tf = TRAINING_TIMEFRAMES.find((t) => t.value === tfValue) || TRAINING_TIMEFRAMES[0];
  const secs = tf.secs || 60;
  const hard = 50_000;
  const ideal = Math.floor(months * 30 * 86400 / secs);
  if (secs > 60) {
    // HTF: honor calendar window up to hard max (do not scale-crush from 1m caps).
    return Math.max(500, Math.min(ideal, hard));
  }
  const win = TRAINING_WINDOWS.find((w) => w.value === String(monthsValue));
  const cap1m = win?.targetBars1m ?? 25000;
  return Math.max(500, Math.min(ideal, cap1m, hard));
}

/** Interactive Validate budget — mirrors backend HTF lean + Lab 8k ceiling. */
function estimateValidateBars(monthsValue, tfValue, strategy) {
  if (strategy === 'RL_PPO_AGENT') return 1200;
  const trainBars = estimateTrainingBars(monthsValue, tfValue);
  const months = Number(monthsValue) || 3;
  const tf = TRAINING_TIMEFRAMES.find((t) => t.value === tfValue) || TRAINING_TIMEFRAMES[0];
  const secs = tf.secs || 60;
  if (secs > 60) {
    const ideal = Math.floor(months * 30 * 86400 / secs);
    return Math.max(500, Math.min(trainBars, Math.max(2_500, Math.floor(ideal / 3)), 12_000, 8_000));
  }
  const byMonth = { 1: 2_000, 3: 2_500, 6: 5_000, 12: 8_000 };
  return Math.max(500, Math.min(byMonth[months] ?? 2_500, trainBars, 8_000));
}

function suggestedNFolds(monthsValue, strategy) {
  if (strategy === 'RL_PPO_AGENT') return 2;
  const months = Number(monthsValue) || 3;
  if (months >= 12) return 4;
  if (months >= 6) return 3;
  return 3;
}

function suggestedPboSegments(monthsValue, strategy) {
  if (strategy === 'RL_PPO_AGENT') return 4;
  const months = Number(monthsValue) || 3;
  if (months >= 12) return 6;
  if (months >= 6) return 5;
  return 4;
}

/** Apply window/TF-driven defaults onto Advanced knobs (keeps architecture fields). */
function syncAdvancedForWindow(prev, strategy, monthsValue, tfValue) {
  const base = defaultAdvancedKnobs(strategy, 'train');
  return {
    ...base,
    ...prev,
    // Always re-derive data-budget knobs from the Lab window pick.
    nFolds: String(suggestedNFolds(monthsValue, strategy)),
    validateMaxBars: String(estimateValidateBars(monthsValue, tfValue, strategy)),
    pboSegments: String(suggestedPboSegments(monthsValue, strategy)),
    // Keep user architecture / epochs if they already edited them this session.
    epochs: prev?.epochs ?? base.epochs,
    hiddenDim: prev?.hiddenDim ?? base.hiddenDim,
    totalTimesteps: prev?.totalTimesteps ?? base.totalTimesteps,
    gbmMaxIter: prev?.gbmMaxIter ?? base.gbmMaxIter,
    gbmMaxDepth: prev?.gbmMaxDepth ?? base.gbmMaxDepth,
    pboMaxCombos: prev?.pboMaxCombos ?? base.pboMaxCombos,
  };
}

function readStoredTrainingWindow() {
  try {
    const v = window.localStorage.getItem(ML_LAB_WINDOW_KEY);
    if (TRAINING_WINDOWS.some((w) => w.value === v)) return v;
  } catch {
    /* ignore */
  }
  return '3';
}

function readStoredTrainingTimeframe(fallback) {
  try {
    const v = window.localStorage.getItem(ML_LAB_TF_KEY);
    if (TRAINING_TIMEFRAMES.some((t) => t.value === v)) return v;
  } catch {
    /* ignore */
  }
  return fallback;
}

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

/** Deploy-gate mirror: trained / walk-forward / PBO from model-status enrich. */
function DeployReadinessStrip({ status }) {
  if (!status?.trained) return null;

  const wf = status.walk_forward && typeof status.walk_forward === 'object'
    ? status.walk_forward
    : null;
  const pbo = status.pbo && typeof status.pbo === 'object' ? status.pbo : null;
  const validatedAt = status.validated_at || wf?.validated_at || null;
  const cal = status.data_calendar && typeof status.data_calendar === 'object'
    ? status.data_calendar
    : null;

  const trainedOk = true;
  const wfOk = Boolean(wf?.ok && validatedAt);
  const wfMissing = !validatedAt || !wf?.ok;
  const pboSkipped = Boolean(pbo?.skipped);
  const pboPresent = pbo != null && pbo.pbo != null && !pboSkipped;
  const pboOk = pboPresent && pbo.ok === true;
  const pboWarn = pboPresent && pbo.ok === false;
  const holdoutOk = Boolean(cal?.holdout_days && cal?.fit_end_ts);

  const ageLabel = (() => {
    if (!validatedAt) return null;
    try {
      const d = new Date(validatedAt);
      if (Number.isNaN(d.getTime())) return null;
      return d.toLocaleString();
    } catch {
      return null;
    }
  })();

  const chip = (ok, warn, label, title) => (
    <span
      className={cn(
        'ml-training__ready-chip',
        ok && 'ml-training__ready-chip--ok',
        warn && 'ml-training__ready-chip--warn',
        !ok && !warn && 'ml-training__ready-chip--fail',
      )}
      title={title}
    >
      {ok ? <CheckCircle2 size={11} aria-hidden /> : warn ? <FlaskConical size={11} aria-hidden /> : <XCircle size={11} aria-hidden />}
      {label}
    </span>
  );

  return (
    <section className="ml-training__ready" aria-label="Deploy readiness">
      <div className="ml-training__ready-head">
        <h4 className="ml-training__section-title">Deploy readiness</h4>
        {ageLabel && (
          <span className="ml-training__header-meta num-mono">
            validated {ageLabel}
          </span>
        )}
      </div>
      <div className="ml-training__ready-chips">
        {chip(trainedOk, false, 'Trained', 'Model artifact on disk')}
        {chip(
          wfOk,
          false,
          wfOk
            ? `Walk-forward${wf?.mean_oos_accuracy != null ? ` · ${fmtMetric(wf.mean_oos_accuracy, 3, 'mean_oos_accuracy')}` : ''}`
            : 'Walk-forward',
          wfMissing
            ? 'Run Walk-forward + PBO before deploy — gate will block without it'
            : (wf?.recommendation || 'Walk-forward validation passed'),
        )}
        {pboSkipped
          ? chip(false, true, 'PBO skipped', pbo?.error || 'PBO was skipped for this strategy')
          : pboPresent
            ? chip(
              pboOk,
              pboWarn,
              `PBO ${fmtMetric(pbo.pbo, 3, 'pbo') ?? '—'}`,
              pboOk
                ? 'PBO under 50% — acceptable overfitting risk'
                : 'PBO ≥ 50% — elevated overfitting risk for deploy',
            )
            : chip(false, true, 'PBO', 'No PBO result yet — run Walk-forward + PBO')}
        {cal && chip(
          holdoutOk,
          false,
          holdoutOk ? `Holdout · ${cal.holdout_days}d` : 'Holdout',
          holdoutOk
            ? 'Champion FIT ends before locked holdout — use Algo BT on holdout only'
            : 'Train with ML_CALENDAR_HOLDOUT=1 to stamp FIT / holdout',
        )}
      </div>
    </section>
  );
}

function DataCalendarStrip({ calendar, trainingWindow }) {
  const cal = calendar && typeof calendar === 'object' ? calendar : null;
  if (!cal?.fit_end_ts && !cal?.holdout_days) {
    const months = Number(trainingWindow) || 3;
    const holdout = months <= 1 ? 7 : Math.min(30, Math.max(14, Math.round(months * 30 * 0.15)));
    return (
      <p className="text-[10px] text-muted-foreground mt-1 leading-snug">
        Calendar (when <span className="font-mono">ML_CALENDAR_HOLDOUT=1</span>): FIT → embargo → HOLDOUT (~{holdout}d).
        Train/Validate use FIT only; Algo ML BT defaults to holdout.
      </p>
    );
  }
  const fitDays = cal.fit_days != null ? `${cal.fit_days}d` : '—';
  const embargo = cal.embargo_bars != null ? `${cal.embargo_bars} bars` : '—';
  const holdout = cal.holdout_days != null ? `${cal.holdout_days}d` : '—';
  return (
    <p className="text-[10px] text-muted-foreground mt-1 leading-snug" title="Locked OOS holdout after FIT">
      <span className="text-foreground/80">FIT</span> ~{fitDays}
      {' · '}
      <span className="text-foreground/80">EMBARGO</span> {embargo}
      {' · '}
      <span className="text-foreground/80">HOLDOUT</span> {holdout}
    </p>
  );
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
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  return m > 0 ? `${m}m ${String(r).padStart(2, '0')}s` : `${r}s`;
}

function formatDurationMs(ms) {
  if (ms == null || Number.isNaN(Number(ms))) return '—';
  return formatElapsed(Number(ms));
}

function formatPollLogTime(ts) {
  try {
    return new Date(ts).toLocaleTimeString(undefined, { hour12: false });
  } catch {
    return '—';
  }
}

function formatPollLogLine(entry) {
  const bits = [formatPollLogTime(entry.t)];
  if (entry.status) bits.push(`status=${entry.status}`);
  if (entry.pct != null) bits.push(`pct=${Math.round(entry.pct)}`);
  if (entry.phase) bits.push(`phase=${entry.phase}`);
  if (entry.detail) bits.push(`detail=${entry.detail}`);
  if (entry.note) bits.push(entry.note);
  return bits.join(' ');
}

const POLL_LOG_PREF_KEY = 'ml-lab-show-poll-log';

function JobPollLog({ entries, enabled, onEnabledChange, onClear }) {
  const logRef = useRef(null);
  const lines = Array.isArray(entries) ? entries : [];

  useEffect(() => {
    if (!enabled || !logRef.current) return;
    logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [enabled, lines.length, lines[lines.length - 1]?.t]);

  return (
    <div className="ml-training__poll-log">
      <div className="ml-training__poll-log-head">
        <label className="ml-training__poll-log-toggle">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => onEnabledChange(Boolean(e.target.checked))}
          />
          <span>Show poll log</span>
        </label>
        {enabled && (
          <div className="ml-training__poll-log-actions">
            <span className="ml-training__header-meta num-mono">{lines.length} lines</span>
            {lines.length > 0 && typeof onClear === 'function' && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-[0.6rem]"
                onClick={onClear}
              >
                Clear
              </Button>
            )}
          </div>
        )}
      </div>
      {enabled && (
        <pre
          ref={logRef}
          className="ml-training__poll-log-body num-mono"
          aria-label="Training job poll log"
        >
          {lines.length === 0
            ? '# Poll snapshots appear while Train / Validate runs…'
            : lines.map(formatPollLogLine).join('\n')}
        </pre>
      )}
    </div>
  );
}

function JobProgressBar({ job, serverProgress, onCancel, cancelling }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!job?.active) return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [job?.active, job?.startedAt]);

  if (!job?.active) return null;

  const elapsed = Math.max(0, now - (job.startedAt || now));
  const timeoutMs = Math.max(job.timeoutMs || 60_000, 15_000);
  const hasServerPct = serverProgress?.pct != null && Number(serverProgress.pct) > 0;
  // Asymptotic estimate — never claims 100% until the request finishes.
  const ratio = Math.min(0.94, 1 - Math.exp(-elapsed / (timeoutMs * 0.45)));
  const estPct = Math.max(2, Math.round(ratio * 100));
  const pct = hasServerPct
    ? Math.max(1, Math.min(99, Math.round(Number(serverProgress.pct))))
    : estPct;
  const phases = job.phases || [];
  const phaseIdx = phases.findIndex((p) => pct < p.until);
  const phase = phases[phaseIdx >= 0 ? phaseIdx : Math.max(phases.length - 1, 0)];
  const phaseLabel = hasServerPct
    ? [serverProgress.phase, serverProgress.detail].filter(Boolean).join(' · ')
      || phase?.label
    : phase?.label;

  return (
    <div className="ml-training__progress" role="status" aria-live="polite">
      <div className="ml-training__progress-head">
        <span className="ml-training__progress-label">
          <Loader2 size={12} className="animate-spin" aria-hidden />
          {job.label}
        </span>
        <span className="ml-training__progress-meta num-mono">
          {pct}% · {formatElapsed(elapsed)}
          {timeoutMs >= 60_000 ? ` / ~${formatMlJobBudgetLabel(timeoutMs)}` : ''}
          {hasServerPct ? ' · live' : ''}
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
      <div className="ml-training__progress-foot">
        {phaseLabel && (
          <p className="ml-training__progress-phase">{phaseLabel}</p>
        )}
        {typeof onCancel === 'function' && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 text-[0.65rem] shrink-0"
            disabled={cancelling}
            onClick={onCancel}
          >
            {cancelling ? <Loader2 size={12} className="animate-spin" /> : null}
            Cancel
          </Button>
        )}
      </div>
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
  const topFeatures = normalizeTopFeatures(dataset?.top_features).slice(0, 10);
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
                <span className="text-muted-foreground">Seq. samples</span>
                <p className="num-mono font-medium">
                  {dataset.sample_count ?? dataset.train_samples ?? '—'}
                  {dataset.val_samples != null ? ` / val ${dataset.val_samples}` : ''}
                </p>
                {(dataset.candle_bars != null || dataset.bar_target != null) && (
                  <p className="text-[10px] text-muted-foreground mt-0.5">
                    {dataset.candle_bars != null ? `${dataset.candle_bars} bars` : null}
                    {dataset.bar_target != null ? ` · target ${dataset.bar_target}` : null}
                  </p>
                )}
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
          {topFeatures.length > 0 && (
            <div className="ml-training__feature-importance">
              <p className="ml-training__subsection-label">Feature importance</p>
              <FeatureImportanceChart features={topFeatures} maxBars={10} compact />
            </div>
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

export default function ModelTrainingDashboard({
  detached = false,
  onDetach,
  onAttach,
} = {}) {
  const activeSymbol = useStore((s) => s.activeSymbol);
  const botStrategy = useStore((s) => s.botStrategy);
  const botTimeframe = useStore((s) => s.botTimeframe);
  const mlSession = useSyncExternalStore(
    subscribeMlTrainingSession,
    getMlTrainingSession,
    getMlTrainingSession,
  );

  const [strategy, setStrategy] = useState(
    () => (isMlStrategy(botStrategy) ? botStrategy : 'ML_SIGNAL_BOOST'),
  );
  const [trainingWindow, setTrainingWindow] = useState(readStoredTrainingWindow);
  const [trainingTimeframe, setTrainingTimeframe] = useState(() => {
    const tf = String(botTimeframe || '1m').toLowerCase();
    const botTf = tf === 'tick' ? '1m' : (tf || '1m');
    return readStoredTrainingTimeframe(botTf);
  });
  const [advanced, setAdvanced] = useState(() => {
    const strat = isMlStrategy(botStrategy) ? botStrategy : 'ML_SIGNAL_BOOST';
    const win = readStoredTrainingWindow();
    const tf = String(botTimeframe || '1m').toLowerCase();
    const botTf = tf === 'tick' ? '1m' : (tf || '1m');
    const timeframe = readStoredTrainingTimeframe(botTf);
    return syncAdvancedForWindow(
      defaultAdvancedKnobs(strat, 'train'),
      strat,
      win,
      timeframe,
    );
  });
  const [status, setStatus] = useState(null);
  const championOosRef = useRef(null);
  const [inventory, setInventory] = useState([]);
  const [retrainActions, setRetrainActions] = useState([]);
  const [retrainPending, setRetrainPending] = useState([]);
  const [retrainHistory, setRetrainHistory] = useState([]);
  const [runNowKey, setRunNowKey] = useState(null);
  const [cancellingJob, setCancellingJob] = useState(false);
  const [queueTelemetry, setQueueTelemetry] = useState({ active: 0, queued: 0 });
  const [trainRuns, setTrainRuns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const panelScrollRef = useRef(null);
  const [activatingVersionId, setActivatingVersionId] = useState(null);
  const [deletingVersionId, setDeletingVersionId] = useState(null);
  const [challengerDismissed, setChallengerDismissed] = useState(false);
  const [showPollLog, setShowPollLog] = useState(() => {
    try {
      return window.localStorage.getItem(POLL_LOG_PREF_KEY) === '1';
    } catch {
      return false;
    }
  });
  const statusRef = useRef(status);
  statusRef.current = status;

  const jobMatches = mlSession.symbol === activeSymbol && mlSession.strategy === strategy;
  const training = Boolean(jobMatches && mlSession.training);
  const validating = Boolean(jobMatches && mlSession.validating);
  const jobProgress = jobMatches ? mlSession.jobProgress : null;
  const serverProgress = jobMatches ? mlSession.serverProgress : null;
  const pollLog = jobMatches ? (mlSession.pollLog || []) : [];
  const activeJobId = jobMatches ? mlSession.jobId : null;
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
            `/api/v1/ml/model-status?symbol=${encodeURIComponent(activeSymbol)}&strategy=${encodeURIComponent(id)}&timeframe=${encodeURIComponent(trainingTimeframe)}`,
          );
          if (body) setCachedModelStatus(activeSymbol, id, body, trainingTimeframe);
          return {
            strategy: id,
            trained: Boolean(body?.trained),
            trained_at: body?.trained_at,
            metrics: body?.metrics || {},
            error: body?.error,
            timeframe: body?.timeframe || trainingTimeframe,
          };
        } catch (err) {
          if (isAbortError(err)) {
            const cached = getCachedModelStatus(activeSymbol, id, trainingTimeframe);
            if (cached) {
              return {
                strategy: id,
                trained: Boolean(cached.trained),
                trained_at: cached.trained_at,
                metrics: cached.metrics || {},
                timeframe: trainingTimeframe,
              };
            }
          }
          const cached = getCachedModelStatus(activeSymbol, id, trainingTimeframe);
          if (cached?.trained) {
            return {
              strategy: id,
              trained: true,
              trained_at: cached.trained_at,
              metrics: cached.metrics || {},
              stale: true,
              timeframe: trainingTimeframe,
            };
          }
          return { strategy: id, trained: false, error: err.message, timeframe: trainingTimeframe };
        }
      }),
    );
    setInventory(rows);
  }, [activeSymbol, trainingTimeframe]);

  const fetchRetrainQueue = useCallback(async () => {
    try {
      const body = await apiRequest('/api/v1/ml/retrain-status');
      const actions = Array.isArray(body?.retrain_actions) ? body.retrain_actions : [];
      setRetrainActions(actions.filter((a) => isMlStrategy(a?.strategy)));
      const pendingMap = body?.pending && typeof body.pending === 'object' ? body.pending : {};
      setRetrainPending(
        Object.entries(pendingMap)
          .map(([key, info]) => ({
            key,
            strategy: info?.strategy,
            symbol: info?.symbol,
            reasons: Array.isArray(info?.reasons) ? info.reasons : [],
            requested_at: info?.requested_at,
          }))
          .filter((p) => isMlStrategy(p.strategy)),
      );
      setRetrainHistory(Array.isArray(body?.history) ? body.history : []);
    } catch (err) {
      if (!isAbortError(err)) {
        setRetrainActions([]);
        setRetrainPending([]);
        setRetrainHistory([]);
      }
    }
  }, []);

  const fetchQueueTelemetry = useCallback(async () => {
    try {
      const body = await apiRequest('/api/v1/ml/jobs?limit=5');
      setQueueTelemetry({
        active: Number(body?.active) || 0,
        queued: Number(body?.queued) || 0,
      });
    } catch (err) {
      if (!isAbortError(err)) {
        /* keep last known */
      }
    }
  }, []);

  const fetchTrainRuns = useCallback(async () => {
    if (!activeSymbol) {
      setTrainRuns([]);
      return;
    }
    try {
      const qs = new URLSearchParams({
        symbol: activeSymbol,
        limit: '15',
        timeframe: trainingTimeframe,
      });
      if (strategy) qs.set('strategy', strategy);
      const body = await apiRequest(`/api/v1/ml/runs?${qs.toString()}`);
      setTrainRuns(Array.isArray(body?.runs) ? body.runs : []);
    } catch (err) {
      if (!isAbortError(err)) setTrainRuns([]);
    }
  }, [activeSymbol, strategy, trainingTimeframe]);

  const fetchStatus = useCallback(async ({ quiet = false } = {}) => {
    if (!activeSymbol || !strategy) return;
    if (!quiet) setLoading(true);
    try {
      const body = await apiRequest(
        `/api/v1/ml/model-status?symbol=${encodeURIComponent(activeSymbol)}&strategy=${encodeURIComponent(strategy)}&timeframe=${encodeURIComponent(trainingTimeframe)}`,
      );
      const next = resolveModelStatusFetch(activeSymbol, strategy, {
        body,
        previous: statusRef.current,
        timeframe: trainingTimeframe,
      });
      setStatus(next);
    } catch (err) {
      const next = resolveModelStatusFetch(activeSymbol, strategy, {
        error: err,
        previous: statusRef.current,
        timeframe: trainingTimeframe,
      });
      setStatus(next);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [activeSymbol, strategy, trainingTimeframe]);

  const refreshAll = useCallback(async ({
    clearSessionValidation = false,
    quiet = false,
    preserveScroll = false,
  } = {}) => {
    const scroller = panelScrollRef.current;
    const scrollTop = preserveScroll && scroller ? scroller.scrollTop : null;
    if (clearSessionValidation) {
      setMlValidation(null);
      setChallengerDismissed(true);
      championOosRef.current = null;
    }
    await Promise.all([
      fetchStatus({ quiet }),
      fetchInventory(),
      fetchRetrainQueue(),
      fetchQueueTelemetry(),
      fetchTrainRuns(),
    ]);
    if (scrollTop != null && scroller) {
      requestAnimationFrame(() => {
        scroller.scrollTop = scrollTop;
      });
    }
  }, [fetchStatus, fetchInventory, fetchRetrainQueue, fetchQueueTelemetry, fetchTrainRuns]);

  const handleManualRefresh = useCallback(async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      // Soft refresh: keep validation block + scroll position; do not jump the panel.
      await refreshAll({
        clearSessionValidation: false,
        quiet: true,
        preserveScroll: true,
      });
    } finally {
      setRefreshing(false);
    }
  }, [refreshAll, refreshing]);

  useEffect(() => {
    if (isMlStrategy(botStrategy) && botStrategy !== strategy) {
      setStrategy(botStrategy);
    }
  }, [botStrategy]); // eslint-disable-line react-hooks/exhaustive-deps -- sync bot picker → dashboard

  const lastBotTfRef = useRef(null);
  useEffect(() => {
    const tf = String(botTimeframe || '1m').toLowerCase();
    if (!tf || tf === 'tick') return;
    // First mount: keep Lab TF from localStorage (already in state). Only follow
    // the bot picker when the bot timeframe itself changes afterward.
    if (lastBotTfRef.current === null) {
      lastBotTfRef.current = tf;
      return;
    }
    if (lastBotTfRef.current === tf) return;
    lastBotTfRef.current = tf;
    setTrainingTimeframe(tf);
  }, [botTimeframe]);

  // Strategy change: reset architecture defaults, then re-apply window budgets.
  useEffect(() => {
    setAdvanced((prev) => syncAdvancedForWindow(
      defaultAdvancedKnobs(strategy, 'train'),
      strategy,
      trainingWindow,
      trainingTimeframe,
    ));
    // trainingWindow/TF intentionally omitted — window effect owns those syncs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategy]);

  // Training window / bar TF: immediately retarget Validate bars, folds, PBO.
  useEffect(() => {
    setAdvanced((prev) => syncAdvancedForWindow(
      prev,
      strategy,
      trainingWindow,
      trainingTimeframe,
    ));
    try {
      window.localStorage.setItem(ML_LAB_WINDOW_KEY, String(trainingWindow));
      window.localStorage.setItem(ML_LAB_TF_KEY, String(trainingTimeframe));
    } catch {
      /* ignore */
    }
  }, [trainingWindow, trainingTimeframe, strategy]);

  useEffect(() => {
    // Clear previous TF's status immediately so we never flash the wrong model.
    const cached = getCachedModelStatus(activeSymbol, strategy, trainingTimeframe);
    setStatus(cached);
    // Quiet background refresh — avoid freezing the controls spinner on every pick.
    refreshAll({ quiet: true, preserveScroll: true });
  }, [refreshAll, trainingTimeframe, activeSymbol, strategy]);

  // Poll queue depth while the panel is open (cheap).
  useEffect(() => {
    const id = window.setInterval(() => {
      fetchQueueTelemetry();
    }, 5_000);
    return () => window.clearInterval(id);
  }, [fetchQueueTelemetry]);

  // Re-attach only when the panel remounts mid-job *without* a local waiter
  // (pollMlJobUntilDone owns the submit path — avoid double finishMlJob).
  const localJobWaiterRef = useRef(false);
  useEffect(() => {
    if (!jobMatches) return undefined;
    if (!mlSession.training && !mlSession.validating) return undefined;
    const jobId = mlSession.jobId;
    if (!jobId) {
      const id = window.setInterval(() => {
        fetchStatus();
      }, 15_000);
      return () => window.clearInterval(id);
    }
    if (localJobWaiterRef.current) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const body = await apiRequest(`/api/v1/ml/jobs/${encodeURIComponent(jobId)}`, {
          timeoutMs: ML_JOB_STATUS_POLL_TIMEOUT_MS,
        });
        const job = body?.job;
        if (cancelled || !job) return;
        if (job.progress) setMlServerProgress({ ...job.progress, status: job.status });
        if (job.status === 'done' || job.status === 'error' || job.status === 'cancelled') {
          if (job.kind === 'validate' && job.result) setMlValidation(job.result);
          finishMlJob(mlSession.jobToken, {
            validation: job.kind === 'validate' ? job.result : undefined,
            error: job.status === 'error' ? (job.error || 'failed') : null,
          });
          fetchStatus();
        }
      } catch {
        appendMlPollLog({
          status: 'running',
          phase: 'waiting',
          detail: 'server busy — still polling…',
          note: 'poll_err',
        });
      }
    };
    tick();
    const id = window.setInterval(tick, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [
    jobMatches,
    mlSession.training,
    mlSession.validating,
    mlSession.jobId,
    mlSession.jobToken,
    fetchStatus,
  ]);

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
          timeframe: trainingTimeframe,
          model_version: pin,
          version_id: version.version_id,
        },
        timeoutMs: 60_000,
      });
      if (body?.ok) {
        setCachedModelStatus(activeSymbol, strategy, body, trainingTimeframe);
        setStatus(body);
        setChallengerDismissed(true);
        championOosRef.current = null;
        setMlValidation(null);
        toast.success(
          `Activated ${body.activated_version_id || pin} as current for ${strategy} / ${activeSymbol}`,
        );
        await refreshAll();
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
          timeframe: trainingTimeframe,
          model_version: pin,
          version_id: version.version_id,
        },
        timeoutMs: 60_000,
      });
      if (body?.ok) {
        setCachedModelStatus(activeSymbol, strategy, body, trainingTimeframe);
        setStatus(body);
        toast.success(`Deleted version ${body.deleted_version_id || label}`);
        await refreshAll();
      } else {
        toast.error(body?.error || 'Failed to delete version');
      }
    } catch (err) {
      toast.error(err.message || 'Delete request failed');
    } finally {
      setDeletingVersionId(null);
    }
  };

  const pollMlJobUntilDone = useCallback(async (jobId, { strategy: strat, kind = 'train' } = {}) => {
    const terminal = new Set(['done', 'error', 'cancelled']);
    const budgetMs = mlJobPollDeadlineMs(strat || strategy, kind);
    const started = Date.now();
    const deadline = started + budgetMs;
    let transientStreak = 0;
    let warnedTransient = false;
    let warnedPastBudget = false;
    // Keep polling until the job reaches a terminal status. Transient HTTP
    // timeouts and even the soft budget must not clear the progress bar.
    for (;;) {
      const pastBudget = Date.now() >= deadline;
      if (pastBudget && !warnedPastBudget) {
        warnedPastBudget = true;
        toast.message(
          `Still ${kind === 'train' ? 'training' : 'validating'} past ${formatMlJobBudgetLabel(budgetMs)} — progress stays open`,
        );
      }
      try {
        const body = await apiRequest(`/api/v1/ml/jobs/${encodeURIComponent(jobId)}`, {
          timeoutMs: ML_JOB_STATUS_POLL_TIMEOUT_MS,
        });
        transientStreak = 0;
        const job = body?.job;
        if (!job) throw new Error('ML job not found');
        if (job.progress) setMlServerProgress({ ...job.progress, status: job.status });
        if (terminal.has(job.status)) return job;
      } catch (err) {
        // Single GET timeouts must not kill the progress bar — GPU trains can
        // starve the event loop briefly; the job is usually still running.
        if (!isTransientMlPollError(err)) throw err;
        transientStreak += 1;
        const prev = getMlTrainingSession().serverProgress || {};
        setMlServerProgress({
          pct: Number(prev.pct) || 0,
          phase: prev.phase || 'waiting',
          detail: 'server busy — still polling…',
          status: prev.status || 'running',
          note: 'poll_err',
        });
        if (!warnedTransient) {
          warnedTransient = true;
          toast.message('Job status briefly unreachable — keeping progress open and retrying…');
        }
        const backoff = Math.min(15_000, 2_000 * transientStreak);
        await new Promise((r) => setTimeout(r, backoff));
        continue;
      }
      const elapsed = Date.now() - started;
      const interval = pastBudget
        ? Math.max(8_000, mlJobPollIntervalMs(elapsed, budgetMs))
        : mlJobPollIntervalMs(elapsed, budgetMs);
      await new Promise((r) => setTimeout(r, interval));
    }
  }, [strategy]);

  const handleCancelJob = useCallback(async () => {
    const jobId = getMlTrainingSession().jobId;
    if (!jobId || cancellingJob) return;
    setCancellingJob(true);
    try {
      const body = await apiRequest(`/api/v1/ml/jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: 'POST',
        timeoutMs: 30_000,
      });
      if (body?.ok) {
        toast.message(body.immediate ? 'Job cancelled' : 'Cancel requested — finishing current step…');
      } else {
        toast.error(body?.error || 'Cancel failed');
      }
    } catch (err) {
      if (!isAbortError(err)) toast.error(err.message || 'Cancel failed');
    } finally {
      setCancellingJob(false);
    }
  }, [cancellingJob]);

  const runTrainJob = async (strat, symbol, { fromQueue = false } = {}) => {
    if (training || validating || busyElsewhere || !symbol || !strat) return;
    const queueKey = `${String(symbol).toUpperCase()}:${String(strat).toUpperCase()}`;
    if (fromQueue) setRunNowKey(queueKey);
    setMlValidation(null);
    if (strat !== strategy) setStrategy(strat);
    const trainTimeoutMs = mlJobTimeoutMs(strat, 'train');
    const token = startJobProgress('train', strat, symbol);
    const knobs = strat === strategy ? advanced : defaultAdvancedKnobs(strat, 'train');
    const trainDefaults = defaultAdvancedKnobs(strat, 'train');
    localJobWaiterRef.current = true;
    try {
      if (DEEP_ML_STRATEGIES.has(strat) || strat === 'RL_PPO_AGENT' || strat === 'ML_SIGNAL_BOOST') {
        toast.message(
          `Training ${strat}… up to ${formatMlJobBudgetLabel(trainTimeoutMs)} (CUDA if the backend torch build supports it)`,
        );
      }
      const body = await apiRequest('/api/v1/ml/train', {
        method: 'POST',
        body: {
          symbol,
          strategy: strat,
          async: true,
          config: {
            timeframe: trainingTimeframe,
            training_window_months: Number(trainingWindow),
            ...(strat === 'RL_PPO_AGENT'
              ? {
                  total_timesteps: parsePositiveInt(
                    knobs.totalTimesteps, trainDefaults.totalTimesteps, { min: 256, max: 500_000 },
                  ),
                  hidden_dim: parsePositiveInt(
                    knobs.hiddenDim, trainDefaults.hiddenDim, { min: 32, max: 1024 },
                  ),
                }
              : {}),
            ...(DEEP_ML_STRATEGIES.has(strat)
              ? {
                  epochs: parsePositiveInt(knobs.epochs, trainDefaults.epochs, { min: 1, max: 500 }),
                  hidden_dim: parsePositiveInt(
                    knobs.hiddenDim, trainDefaults.hiddenDim, { min: 32, max: 1024 },
                  ),
                  ...(strat === 'TRANSFORMER_SIGNAL'
                    ? { d_model: parsePositiveInt(knobs.hiddenDim, 128, { min: 32, max: 512 }) }
                    : {}),
                  ...(strat === 'TCN_MULTI_HORIZON' ? { num_blocks: 6 } : {}),
                }
              : {}),
            ...(strat === 'ML_SIGNAL_BOOST'
              ? {
                  gbm_max_iter: parsePositiveInt(knobs.gbmMaxIter, 300, { min: 40, max: 1000 }),
                  gbm_max_depth: parsePositiveInt(knobs.gbmMaxDepth, 6, { min: 3, max: 12 }),
                }
              : {}),
          },
        },
        // Candle fetch for long Lab windows; train itself is async + polled.
        timeoutMs: ML_JOB_SUBMIT_TIMEOUT_MS,
      });
      if (!body?.ok) {
        toast.error(body?.error || 'Training failed to start');
        return;
      }
      const jobId = body.job_id;
      if (!jobId) {
        toast.error('Server did not return a job_id');
        return;
      }
      setMlJobId(jobId);
      const job = await pollMlJobUntilDone(jobId, { strategy: strat, kind: 'train' });
      const result = (job.result && typeof job.result === 'object') ? job.result : {};
      if (job.status === 'cancelled' || result.cancelled) {
        toast.message('Training cancelled');
        return;
      }
      if (job.status === 'done' && result.ok !== false) {
        const tw = result.training_window;
        const twNote = tw?.bars != null
          ? ` · ${Number(tw.bars).toLocaleString()} bars`
            + (tw.span_days != null ? ` (~${tw.span_days}d)` : '')
            + (tw.training_window_months != null ? ` / ${tw.training_window_months}mo` : '')
          : '';
        toast.success(`Training complete for ${strat} / ${symbol}${twNote}`);
        // Drop from retrain audit immediately (backend also clears via record_retrain).
        setRetrainPending((prev) => prev.filter((p) => p.key !== queueKey));
        setRetrainActions((prev) => prev.filter((a) => (
          `${String(a.symbol || '').toUpperCase()}:${String(a.strategy || '').toUpperCase()}` !== queueKey
        )));
      } else {
        toast.error(job.error || result.error || 'Training failed');
      }
    } catch (err) {
      if (!isAbortError(err)) {
        toast.error(err.message || 'Training request failed');
      }
    } finally {
      localJobWaiterRef.current = false;
      finishJobProgress(token);
      if (fromQueue) setRunNowKey(null);
      // Always refresh enriched status — never cache thin train payloads as status.
      await refreshAll();
    }
  };

  const handleTrain = async () => {
    await runTrainJob(strategy, activeSymbol);
  };

  const handleRunNow = async (strat, symbol) => {
    if (!strat || !symbol) return;
    if (!isMlStrategy(strat)) {
      toast.message(
        `Lab training not supported for ${strat} — technical/agent bots use meta-label retrain, not Lab warming.`,
      );
      return;
    }
    if (symbol !== activeSymbol) {
      toast.message(`Training ${strat} for ${symbol} (chart symbol is ${activeSymbol || '—'})`);
    }
    await runTrainJob(strat, symbol, { fromQueue: true });
  };

  const handleValidate = async () => {
    if (validating || training || busyElsewhere || !activeSymbol) return;
    setMlValidation(null);
    const isRl = strategy === 'RL_PPO_AGENT';
    const isDeep = DEEP_ML_STRATEGIES.has(strategy);
    const defaults = defaultAdvancedKnobs(strategy, 'validate');
    const nFolds = parsePositiveInt(advanced.nFolds, defaults.nFolds, { min: 2, max: 8 });
    const validateMaxBars = parsePositiveInt(
      advanced.validateMaxBars,
      defaults.validateMaxBars,
      { min: 200, max: 20_000 },
    );
    const pboSegments = parsePositiveInt(advanced.pboSegments, defaults.pboSegments, { min: 2, max: 8 });
    const pboMaxCombos = parsePositiveInt(advanced.pboMaxCombos, defaults.pboMaxCombos, { min: 1, max: 16 });
    const totalTimesteps = parsePositiveInt(
      advanced.totalTimesteps,
      defaults.totalTimesteps,
      { min: 256, max: 500_000 },
    );
    championOosRef.current = status?.walk_forward?.mean_oos_accuracy ?? null;
    setChallengerDismissed(false);
    const validateTimeoutMs = mlJobTimeoutMs(strategy, 'validate');
    const token = startJobProgress('validate', strategy, activeSymbol);
    localJobWaiterRef.current = true;
    try {
      toast.message(
        isRl
          ? `Running RL walk-forward (fast mode, no PBO)… up to ${formatMlJobBudgetLabel(validateTimeoutMs)}`
          : isDeep
            ? `Running walk-forward (fast folds, no PBO)… up to ${formatMlJobBudgetLabel(validateTimeoutMs)}`
            : `Running walk-forward + PBO… up to ${formatMlJobBudgetLabel(validateTimeoutMs)}`,
      );
      // Do not reuse Train Advanced epochs (e.g. 80) — those interrupt WF before folds finish.
      const wfEpochs = isDeep
        ? parsePositiveInt(defaults.epochs, defaults.epochs, { min: 1, max: 40 })
        : null;
      const body = await apiRequest('/api/v1/ml/validate', {
        method: 'POST',
        body: {
          symbol: activeSymbol,
          strategy,
          async: true,
          n_folds: nFolds,
          mode: 'rolling',
          // Deep/RL fold PBO re-trains every combo — too heavy for Lab Validate.
          pbo: !isRl && !isDeep,
          pbo_segments: pboSegments,
          timeframe: trainingTimeframe,
          config: {
            timeframe: trainingTimeframe,
            training_window_months: Number(trainingWindow),
            symbol: activeSymbol,
            model_symbol: activeSymbol,
            _wf_mode: true,
            wf_use_gpu: true,
            validate_max_bars: validateMaxBars,
            pbo_max_combos: pboMaxCombos,
            ...(isRl
              ? { total_timesteps: totalTimesteps, n_steps: 512, ppo_epochs: 2, hidden_dim: 64 }
              : {}),
            ...(wfEpochs != null ? { epochs: wfEpochs, wf_epochs: wfEpochs } : {}),
          },
        },
        timeoutMs: ML_JOB_SUBMIT_TIMEOUT_MS,
      });
      if (!body?.ok) {
        const foldErr = Array.isArray(body?.folds)
          ? body.folds.find((f) => f?.error)?.error
          : null;
        toast.error(body?.error || foldErr || 'Validation failed to start');
        setMlValidation(body || { ok: false, error: 'Validation failed' });
        return;
      }
      const jobId = body.job_id;
      if (!jobId) {
        toast.error('Server did not return a job_id');
        return;
      }
      setMlJobId(jobId);
      const job = await pollMlJobUntilDone(jobId, { strategy, kind: 'validate' });
      const result = (job.result && typeof job.result === 'object')
        ? job.result
        : { ok: false, error: job.error || 'Validation failed' };
      setMlValidation(result);
      if (job.status === 'cancelled' || result.cancelled) {
        toast.message('Validation cancelled');
      } else if (job.status === 'done' && result.ok) {
        const tw = result.training_window;
        const twNote = tw?.bars != null
          ? ` · ${Number(tw.bars).toLocaleString()} bars`
            + (tw.span_days != null ? ` (~${tw.span_days}d)` : '')
          : '';
        const persisted = result.validation_persisted;
        if (persisted && persisted.ok === false) {
          toast.error(
            persisted.error
              || 'Walk-forward finished but deploy stamp was not saved — retry Validate',
          );
        } else {
          toast.success(`Walk-forward validation finished${twNote}`);
        }
      } else {
        const foldErr = Array.isArray(result?.folds)
          ? result.folds.find((f) => f?.error)?.error
          : null;
        toast.error(job.error || result.error || foldErr || 'Validation failed');
      }
    } catch (err) {
      const msg = err?.message || String(err) || 'Validation request failed';
      const badJson = /invalid json|internal server error/i.test(msg);
      const friendly = badJson
        ? 'Validation hit a server error (non-JSON response). Recycle Massive backend and retry — RL walk-forward needs the latest ONNX export fix.'
        : msg;
      setMlValidation({ ok: false, error: friendly });
      if (!isAbortError(err)) {
        toast.error(badJson ? 'Validation failed — recycle backend and retry' : msg);
      }
    } finally {
      localJobWaiterRef.current = false;
      finishJobProgress(token);
      await refreshAll();
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
  const queueBadge = (queueTelemetry.active > 0 || queueTelemetry.queued > 0)
    ? `${queueTelemetry.active} running · ${queueTelemetry.queued} queued`
    : null;

  const { onScroll: onRunsScroll, window: runsWindow } = useVirtualRows(trainRuns, {
    rowHeight: 32,
    overscan: 6,
  });

  const displayValidation = validation || (
    status?.walk_forward || status?.pbo
      ? {
        ok: Boolean(status.walk_forward?.ok),
        mean_accuracy: status.walk_forward?.mean_oos_accuracy,
        n_folds: status.walk_forward?.n_folds,
        successful_folds: status.walk_forward?.successful_folds,
        recommendation: status.walk_forward?.recommendation,
        pbo: status.pbo,
        _persisted: true,
      }
      : null
  );

  const challengerHint = (
    !challengerDismissed
    && !displayValidation?._persisted
    && displayValidation?.ok
  )
    ? buildChallengerHint({
      validation: displayValidation,
      championOos: championOosRef.current,
      versions: status?.versions,
    })
    : null;

  const dismissChallengerHint = () => {
    setChallengerDismissed(true);
    championOosRef.current = null;
  };

  return (
    <div
      ref={panelScrollRef}
      className="dock-panel dock-panel--ml-training overflow-y-auto h-full"
    >
      <header
        title="Train and validate ML models per symbol. Optimizer Lab handles hyperparameter sweeps only."
      >
        <h3 className="ml-training__title">
          <BrainCircuit size={16} aria-hidden />
          Model Training
        </h3>
        <div className="ml-training__header-right">
          {queueBadge && (
            <span className="ml-training__queue-badge num-mono" title="ML train/validate worker queue">
              {queueBadge}
            </span>
          )}
          {status?.trained_at && (
            <span className="ml-training__header-meta num-mono">
              {trainingTimeframe} · {new Date(status.trained_at).toLocaleString()}
            </span>
          )}
          {status && !status.trained && (
            <span className="ml-training__header-meta text-muted-foreground">
              no {trainingTimeframe} model
            </span>
          )}
          {(onDetach || onAttach) && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs gap-1 shrink-0"
              title={
                detached
                  ? 'Dock ML Lab back into the trading layout'
                  : 'Open ML Lab in a separate window (keeps one Lab instance)'
              }
              onClick={() => (detached ? onAttach?.() : onDetach?.())}
            >
              {detached ? (
                <>
                  <PanelLeft size={14} aria-hidden />
                  Reattach
                </>
              ) : (
                <>
                  <ExternalLink size={14} aria-hidden />
                  Detach
                </>
              )}
            </Button>
          )}
        </div>
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
            <Label className="text-xs">Bar timeframe</Label>
            <Select value={trainingTimeframe} onValueChange={setTrainingTimeframe}>
              <SelectTrigger size="sm" className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TRAINING_TIMEFRAMES.map((t) => (
                  <SelectItem key={t.value} value={t.value} className="text-xs">
                    {t.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[10px] text-muted-foreground mt-0.5 leading-snug">
              Must match the bot execution TF. HTF models store separately (e.g. ETHUSDT__15M).
            </p>
          </div>
          <div className="ml-training__field">
            <Label className="text-xs">Training window</Label>
            <Select
              value={trainingWindow}
              onValueChange={(v) => setTrainingWindow(v)}
            >
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
            <p className="text-[10px] text-muted-foreground mt-0.5 leading-snug">
              Train ~{estimateTrainingBars(trainingWindow, trainingTimeframe).toLocaleString()}{' '}
              {trainingTimeframe} bars · Validate ~{estimateValidateBars(trainingWindow, trainingTimeframe, strategy).toLocaleString()}{' '}
              bars · {suggestedNFolds(trainingWindow, strategy)} folds
              (Advanced knobs update with this pick).
            </p>
            <DataCalendarStrip
              calendar={status?.data_calendar}
              trainingWindow={trainingWindow}
            />
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

        <details className="ml-training__advanced">
          <summary>Advanced</summary>
          <div className="ml-training__advanced-grid">
            <label className="ml-training__advanced-field">
              <span>n_folds</span>
              <Input
                type="number"
                min={2}
                max={8}
                className="h-7 text-xs"
                value={advanced.nFolds}
                onChange={(e) => setAdvanced((a) => ({ ...a, nFolds: e.target.value }))}
              />
            </label>
            <label className="ml-training__advanced-field">
              <span>validate_max_bars</span>
              <Input
                type="number"
                min={200}
                max={20000}
                step={100}
                className="h-7 text-xs"
                value={advanced.validateMaxBars}
                onChange={(e) => setAdvanced((a) => ({ ...a, validateMaxBars: e.target.value }))}
              />
            </label>
            <label className="ml-training__advanced-field">
              <span>pbo_segments</span>
              <Input
                type="number"
                min={2}
                max={8}
                className="h-7 text-xs"
                disabled={strategy === 'RL_PPO_AGENT'}
                value={advanced.pboSegments}
                onChange={(e) => setAdvanced((a) => ({ ...a, pboSegments: e.target.value }))}
              />
            </label>
            <label className="ml-training__advanced-field">
              <span>pbo_max_combos</span>
              <Input
                type="number"
                min={1}
                max={16}
                className="h-7 text-xs"
                disabled={strategy === 'RL_PPO_AGENT'}
                value={advanced.pboMaxCombos}
                onChange={(e) => setAdvanced((a) => ({ ...a, pboMaxCombos: e.target.value }))}
              />
            </label>
            {strategy === 'RL_PPO_AGENT' && (
              <label className="ml-training__advanced-field">
                <span>total_timesteps</span>
                <Input
                  type="number"
                  min={256}
                  max={500000}
                  step={256}
                  className="h-7 text-xs"
                  value={advanced.totalTimesteps}
                  onChange={(e) => setAdvanced((a) => ({ ...a, totalTimesteps: e.target.value }))}
                />
              </label>
            )}
            {(DEEP_ML_STRATEGIES.has(strategy) || strategy === 'RL_PPO_AGENT') && (
              <label className="ml-training__advanced-field">
                <span>hidden_dim</span>
                <Input
                  type="number"
                  min={32}
                  max={1024}
                  step={32}
                  className="h-7 text-xs"
                  value={advanced.hiddenDim}
                  onChange={(e) => setAdvanced((a) => ({ ...a, hiddenDim: e.target.value }))}
                />
              </label>
            )}
            {DEEP_ML_STRATEGIES.has(strategy) && (
              <label className="ml-training__advanced-field">
                <span>train epochs</span>
                <Input
                  type="number"
                  min={1}
                  max={500}
                  className="h-7 text-xs"
                  value={advanced.epochs}
                  onChange={(e) => setAdvanced((a) => ({ ...a, epochs: e.target.value }))}
                />
              </label>
            )}
            {strategy === 'ML_SIGNAL_BOOST' && (
              <>
                <label className="ml-training__advanced-field">
                  <span>gbm_max_iter</span>
                  <Input
                    type="number"
                    min={40}
                    max={1000}
                    step={10}
                    className="h-7 text-xs"
                    value={advanced.gbmMaxIter}
                    onChange={(e) => setAdvanced((a) => ({ ...a, gbmMaxIter: e.target.value }))}
                  />
                </label>
                <label className="ml-training__advanced-field">
                  <span>gbm_max_depth</span>
                  <Input
                    type="number"
                    min={3}
                    max={12}
                    className="h-7 text-xs"
                    value={advanced.gbmMaxDepth}
                    onChange={(e) => setAdvanced((a) => ({ ...a, gbmMaxDepth: e.target.value }))}
                  />
                </label>
              </>
            )}
          </div>
          <p className="ml-training__advanced-hint">
            Train uses GPU (CUDA) when PyTorch detects it; Validate stays lighter on CPU.
            Client waits up to ~90 min for PPO / ~60 min for deep models (plus a poll buffer).
            Live bots still infer via CPU ONNX. Retrain after changing hidden_dim / architecture.
          </p>
        </details>

        <JobProgressBar
          job={jobProgress}
          serverProgress={serverProgress}
          onCancel={activeJobId ? handleCancelJob : undefined}
          cancelling={cancellingJob}
        />
        <JobPollLog
          entries={pollLog}
          enabled={showPollLog}
          onEnabledChange={(on) => {
            setShowPollLog(on);
            try {
              window.localStorage.setItem(POLL_LOG_PREF_KEY, on ? '1' : '0');
            } catch {
              /* ignore */
            }
          }}
          onClear={() => clearMlPollLog()}
        />

        {busyElsewhere && (
          <p className="text-xs text-amber-400/90">
            Job running for {mlSession.strategy} / {mlSession.symbol}
            {queueBadge ? ` · ${queueBadge}` : ''}
            {' '}— switch back to that pair to watch progress.
          </p>
        )}
        {!busyElsewhere && queueBadge && !(training || validating) && (
          <p className="text-xs text-muted-foreground">
            Worker queue: {queueBadge}
          </p>
        )}
        <MetricChips metrics={status?.metrics} />
        <DeployReadinessStrip status={status} />
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
            disabled={refreshing}
            aria-busy={refreshing}
            onClick={handleManualRefresh}
            title="Reload model status without leaving this panel"
          >
            {refreshing
              ? <Loader2 size={14} className="animate-spin" />
              : <RefreshCw size={14} />}
            Refresh
          </Button>
        </div>
        <p className="text-[10px] text-muted-foreground -mt-1">
          Trigger retrain uses the Training window above. Walk-forward uses validate_max_bars
          ({Number(advanced.validateMaxBars).toLocaleString()} bars) — both update when you
          change months / timeframe.
        </p>
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

      {displayValidation && (
        <section className="ml-training__card">
          <div className="ml-training__card-head">
            <h4 className="ml-training__section-title">Validation result</h4>
            {displayValidation._persisted && (
              <span className="ml-training__header-meta">from model status</span>
            )}
          </div>
          {displayValidation.ok === false && (
            <p className="text-xs text-destructive">
              {displayValidation.error
                || (Array.isArray(displayValidation.folds) && displayValidation.folds.find((f) => f?.error)?.error)
                || 'Validation failed'}
            </p>
          )}
          {displayValidation.ok && (
            <div className="grid gap-2 sm:grid-cols-3 text-xs">
              {(displayValidation.mean_accuracy ?? displayValidation.aggregate?.mean_oos_accuracy) != null && (
                <div>
                  <span className="text-muted-foreground">Mean OOS accuracy</span>
                  <p className="num-mono font-medium">
                    {fmtMetric(displayValidation.mean_accuracy ?? displayValidation.aggregate?.mean_oos_accuracy)}
                  </p>
                </div>
              )}
              {displayValidation.n_folds != null && (
                <div>
                  <span className="text-muted-foreground">Folds</span>
                  <p className="num-mono font-medium">
                    {displayValidation.successful_folds ?? displayValidation.n_folds}/{displayValidation.n_folds}
                  </p>
                </div>
              )}
              {displayValidation.pbo?.pbo != null && (
                <div>
                  <span className="text-muted-foreground">PBO</span>
                  <p className={cn(
                    'num-mono font-medium',
                    Number(displayValidation.pbo.pbo) >= 0.5 && 'text-destructive',
                  )}
                  >
                    {fmtMetric(displayValidation.pbo.pbo)}
                  </p>
                </div>
              )}
              {displayValidation.recommendation && (
                <div className="sm:col-span-3">
                  <span className="text-muted-foreground">Recommendation</span>
                  <p className="text-xs">{displayValidation.recommendation}</p>
                </div>
              )}
            </div>
          )}
          {Array.isArray(displayValidation.folds) && displayValidation.folds.length > 0 && (
            <ul className="ml-training__fold-list text-[0.65rem] text-muted-foreground">
              {displayValidation.folds.slice(0, 8).map((f, i) => (
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
          {challengerHint && (
            <div className="ml-training__challenger">
              <div className="ml-training__challenger-text">
                <p className="ml-training__challenger-title">Challenger beats champion</p>
                <p className="text-[0.65rem] text-muted-foreground num-mono">
                  OOS {fmtMetric(challengerHint.championOos)} → {fmtMetric(challengerHint.challengerOos)}
                  {challengerHint.version?.version_id
                    ? ` · ${challengerHint.version.version_id}`
                    : ''}
                  {challengerHint.alreadyLive ? ' · already live' : ''}
                </p>
              </div>
              <div className="ml-training__challenger-actions">
                {challengerHint.canActivate && challengerHint.version && (
                  <Button
                    type="button"
                    size="sm"
                    className="h-7 text-[0.65rem] gap-1 shrink-0"
                    disabled={Boolean(activatingVersionId)}
                    onClick={() => handleActivateVersion(challengerHint.version)}
                  >
                    {activatingVersionId ? <Loader2 size={12} className="animate-spin" /> : null}
                    Activate
                  </Button>
                )}
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-7 text-[0.65rem] shrink-0"
                  onClick={dismissChallengerHint}
                >
                  Dismiss
                </Button>
              </div>
            </div>
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

      <section className="ml-training__card">
        <div className="ml-training__card-head">
          <h4 className="ml-training__section-title">Recent runs</h4>
          <span className="ml-training__header-meta">
            {trainRuns.length} · {activeSymbol || '—'}
          </span>
        </div>
        {trainRuns.length === 0 ? (
          <p className="text-[0.65rem] text-muted-foreground">
            No train/validate history yet for this symbol/strategy.
          </p>
        ) : (
          <div className="ml-training__runs-scroll" onScroll={onRunsScroll}>
            <table className="ml-training__runs-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Kind</th>
                  <th>TF</th>
                  <th>Result</th>
                  <th className="text-right">Duration</th>
                </tr>
              </thead>
              <tbody>
                <VirtualTablePadding height={runsWindow.topPad} colSpan={5} />
                {runsWindow.slice.map((run) => {
                  const metricHint = run.metrics?.mean_oos_accuracy
                    ?? run.metrics?.mean_accuracy
                    ?? run.metrics?.val_accuracy
                    ?? run.metrics?.pbo;
                  return (
                    <tr key={run.id} title={run.error || run.version_id || ''}>
                      <td className="num-mono">
                        {run.finished_at
                          ? new Date(run.finished_at).toLocaleString(undefined, {
                            month: 'short',
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit',
                          })
                          : '—'}
                      </td>
                      <td>{run.kind || '—'}</td>
                      <td className="num-mono text-muted-foreground">{run.timeframe || '—'}</td>
                      <td className={cn(
                        'num-mono',
                        run.ok ? 'text-emerald-400' : 'text-destructive',
                      )}
                      >
                        {run.ok ? 'ok' : (run.error === 'cancelled' ? 'cancelled' : 'fail')}
                        {metricHint != null
                          ? ` · ${fmtMetric(metricHint, 3, 'mean_oos_accuracy') ?? metricHint}`
                          : ''}
                      </td>
                      <td className="num-mono text-right">
                        {formatDurationMs(run.duration_ms)}
                      </td>
                    </tr>
                  );
                })}
                <VirtualTablePadding height={runsWindow.bottomPad} colSpan={5} />
              </tbody>
            </table>
          </div>
        )}
      </section>

      {(retrainActions.length > 0 || retrainPending.length > 0 || retrainHistory.length > 0) && (
        <section className="ml-training__card ml-training__card--warn">
          <div className="ml-training__card-head">
            <h4 className="ml-training__section-title">Retrain audit</h4>
            <span className="ml-training__header-meta">
              {retrainActions.length} due · {retrainPending.length} pending
            </span>
          </div>

          {retrainActions.length > 0 && (
            <div className="ml-training__retrain-block">
              <p className="ml-training__subsection-label">Recommended</p>
              <ul className="ml-training__retrain-list">
                {retrainActions.slice(0, 8).map((a, i) => {
                  const key = `${String(a.symbol || '').toUpperCase()}:${String(a.strategy || '').toUpperCase()}`;
                  const running = runNowKey === key;
                  return (
                    <li key={`${key}-${i}`} className="ml-training__retrain-row">
                      <div className="ml-training__retrain-meta">
                        <span className="num-mono font-medium">{a.strategy} / {a.symbol}</span>
                        <span className="text-muted-foreground">
                          {a.reason || 'retrain'}
                          {a.model_age_hours != null ? ` · age ${a.model_age_hours}h` : ''}
                        </span>
                      </div>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="h-7 text-[0.65rem] gap-1 shrink-0"
                        disabled={training || validating || busyElsewhere || Boolean(runNowKey)}
                        onClick={() => handleRunNow(a.strategy, a.symbol)}
                      >
                        {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                        Run now
                      </Button>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {retrainPending.length > 0 && (
            <div className="ml-training__retrain-block">
              <p className="ml-training__subsection-label">Queued (auto-drain or Run now)</p>
              <ul className="ml-training__retrain-list">
                {retrainPending.slice(0, 8).map((p) => {
                  const running = runNowKey === p.key;
                  return (
                    <li key={p.key} className="ml-training__retrain-row">
                      <div className="ml-training__retrain-meta">
                        <span className="num-mono font-medium">{p.strategy} / {p.symbol}</span>
                        <span className="text-muted-foreground">
                          {(p.reasons && p.reasons[0]) || 'queued'}
                          {p.requested_at
                            ? ` · ${new Date(p.requested_at).toLocaleString()}`
                            : ''}
                        </span>
                      </div>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="h-7 text-[0.65rem] gap-1 shrink-0"
                        disabled={training || validating || busyElsewhere || Boolean(runNowKey)}
                        onClick={() => handleRunNow(p.strategy, p.symbol)}
                      >
                        {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                        Run now
                      </Button>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {retrainHistory.length > 0 && (
            <div className="ml-training__retrain-block">
              <p className="ml-training__subsection-label">Recent requests</p>
              <ul className="space-y-1 text-[0.65rem] text-muted-foreground">
                {retrainHistory.slice(0, 8).map((h, i) => (
                  <li key={`${h.key || h.source}-${h.requested_at || i}`} className="num-mono">
                    {h.key || '—'}
                    {h.source ? ` · ${h.source}` : ''}
                    {h.reason ? ` — ${h.reason}` : ''}
                    {h.requested_at
                      ? ` · ${new Date(h.requested_at).toLocaleString()}`
                      : ''}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
