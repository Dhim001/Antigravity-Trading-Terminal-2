/**
 * Client-side budgets for ML Lab train / validate jobs.
 *
 * Async jobs return immediately; the UI polls `/api/v1/ml/jobs/{id}` until done.
 * These caps must cover GPU-era capacity (larger hidden dims, 100+ epochs, PPO 200k steps)
 * and longer Lab training windows (18–36 months of deep REST + folds).
 * Live inference stays CPU ONNX — only training/validate wall-clock grows.
 */

import { DEEP_ML_STRATEGY_IDS } from '@/config/strategies';
import { isAbortError } from '@/api/client';

const DEEP = new Set(DEEP_ML_STRATEGY_IDS);

/** @typedef {'train' | 'validate'} MlJobKind */

export const ML_TRAIN_TIMEOUT_MS = Object.freeze({
  RL_PPO_AGENT: 5_400_000, // 90 min — 200k+ PPO steps on GPU
  deep: 3_600_000, // 60 min — LSTM/TCN/Transformer/VAE/GNN
  default: 1_200_000, // 20 min — larger HistGBM
});

export const ML_VALIDATE_TIMEOUT_MS = Object.freeze({
  // Capacity-parity folds ≈ full Train × n_folds — give multi-hour headroom.
  RL_PPO_AGENT: 10_800_000, // 180 min — multi-fold PPO at train timesteps
  deep: 7_200_000, // 120 min — LSTM/TCN/Transformer/VAE/GNN at train epochs
  default: 3_600_000, // 60 min — HistGBM + PBO
});

/** Extra headroom after the nominal budget before the UI soft-budget toast. */
export const ML_JOB_POLL_BUFFER_MS = 15 * 60_000; // 15 min

/** POST /ml/train|validate submit — returns job_id immediately (fetch is async). */
export const ML_JOB_SUBMIT_TIMEOUT_MS = 120_000; // 2 min (queue + first ack)

/** Per GET /ml/jobs/{id} attempt — short; failures must not stop the progress bar. */
export const ML_JOB_STATUS_POLL_TIMEOUT_MS = 30_000;

/**
 * Scale factor for Lab training_window_months (3mo baseline = 1×).
 * Longer windows mean deeper REST + more FIT bars → more wall-clock.
 * @param {number|string|null|undefined} months
 * @returns {number}
 */
export function mlJobWindowScale(months) {
  const m = Number(months);
  if (!Number.isFinite(m) || m <= 3) return 1;
  if (m <= 6) return 1.25;
  if (m <= 12) return 1.6;
  if (m <= 18) return 2.0;
  if (m <= 24) return 2.5;
  return 3.0; // 36mo
}

/**
 * True when a single poll HTTP call failed but the job may still be running.
 * @param {unknown} err
 */
export function isTransientMlPollError(err) {
  if (isAbortError(err)) return true;
  const msg = String(err?.message || err || '');
  // During LSTM sequence build / CUDA train the event loop can starve; treat
  // gateway and overload responses as retryable so the UI does not abandon
  // a still-running server job.
  return /timed out|failed to fetch|network|load failed|econnreset|econnrefused|http 429|http 502|http 503|http 504|http 500|too many requests|server busy|invalid json from \/api\/v1\/ml\/jobs/i.test(msg);
}

/**
 * Soft overall-budget expiry — job may still be running; keep UI progress open.
 */
export class MlJobPollBudgetError extends Error {
  /**
   * @param {string} message
   * @param {{ jobId?: string, budgetMs?: number }} [opts]
   */
  constructor(message, opts = {}) {
    super(message);
    this.name = 'MlJobPollBudgetError';
    this.code = 'ML_JOB_POLL_BUDGET';
    this.jobId = opts.jobId || null;
    this.budgetMs = opts.budgetMs || 0;
  }
}

/**
 * @param {string} strategy
 * @param {MlJobKind} [kind]
 * @param {{ months?: number|string|null }} [opts]
 * @returns {number}
 */
export function mlJobTimeoutMs(strategy, kind = 'validate', opts = {}) {
  const table = kind === 'train' ? ML_TRAIN_TIMEOUT_MS : ML_VALIDATE_TIMEOUT_MS;
  const id = String(strategy || '').toUpperCase();
  let base = table.default;
  if (id === 'RL_PPO_AGENT') base = table.RL_PPO_AGENT;
  else if (DEEP.has(id)) base = table.deep;
  const scale = mlJobWindowScale(opts?.months);
  // Cap absolute wall-clock so a 36mo deep train doesn't claim multi-day UI.
  const hardCap = kind === 'train' ? 8 * 3_600_000 : 12 * 3_600_000; // 8h train / 12h validate
  return Math.min(hardCap, Math.round(base * scale));
}

/**
 * How long the client may poll a job before the soft-budget toast.
 * @param {string} strategy
 * @param {MlJobKind} [kind]
 * @param {{ months?: number|string|null }} [opts]
 */
export function mlJobPollDeadlineMs(strategy, kind = 'validate', opts = {}) {
  const scale = mlJobWindowScale(opts?.months);
  const buffer = Math.round(ML_JOB_POLL_BUFFER_MS * Math.max(1, Math.min(scale, 2.5)));
  return mlJobTimeoutMs(strategy, kind, opts) + buffer;
}

/**
 * Poll interval — slightly slower for long GPU / long-window jobs to cut chatter.
 * @param {number} elapsedMs
 * @param {number} budgetMs
 */
export function mlJobPollIntervalMs(elapsedMs, budgetMs) {
  if (budgetMs >= 5_400_000 && elapsedMs > 180_000) return 8_000;
  if (budgetMs >= 3_600_000 && elapsedMs > 120_000) return 5_000;
  if (elapsedMs > 60_000) return 4_000;
  return 2_500;
}

/**
 * Human label for toasts / progress ("up to 90 min").
 * @param {number} ms
 */
export function formatMlJobBudgetLabel(ms) {
  const mins = Math.max(1, Math.round(Number(ms || 0) / 60_000));
  if (mins >= 120) {
    const hrs = (mins / 60).toFixed(mins % 60 === 0 ? 0 : 1);
    return `${hrs} h`;
  }
  return `${mins} min`;
}
