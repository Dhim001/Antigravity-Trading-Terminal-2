/**
 * Client-side budgets for ML Lab train / validate jobs.
 *
 * Async jobs return immediately; the UI polls `/api/v1/ml/jobs/{id}` until done.
 * These caps must cover GPU-era capacity (larger hidden dims, 100+ epochs, PPO 200k steps).
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
  RL_PPO_AGENT: 2_700_000, // 45 min — multi-fold PPO (still CPU-capped folds)
  deep: 1_800_000, // 30 min
  default: 900_000, // 15 min
});

/** Extra headroom after the nominal budget before the UI abandons polling. */
export const ML_JOB_POLL_BUFFER_MS = 10 * 60_000; // 10 min

/** POST /ml/train|validate submit — returns job_id immediately (fetch is async). */
export const ML_JOB_SUBMIT_TIMEOUT_MS = 60_000; // 1 min

/** Per GET /ml/jobs/{id} attempt — short; failures must not stop the progress bar. */
export const ML_JOB_STATUS_POLL_TIMEOUT_MS = 20_000;

/**
 * True when a single poll HTTP call failed but the job may still be running.
 * @param {unknown} err
 */
export function isTransientMlPollError(err) {
  if (isAbortError(err)) return true;
  const msg = String(err?.message || err || '');
  return /timed out|failed to fetch|network|load failed|econnreset|econnrefused/i.test(msg);
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
 * @returns {number}
 */
export function mlJobTimeoutMs(strategy, kind = 'validate') {
  const table = kind === 'train' ? ML_TRAIN_TIMEOUT_MS : ML_VALIDATE_TIMEOUT_MS;
  const id = String(strategy || '').toUpperCase();
  if (id === 'RL_PPO_AGENT') return table.RL_PPO_AGENT;
  if (DEEP.has(id)) return table.deep;
  return table.default;
}

/**
 * How long the client may poll a job before giving up.
 * @param {string} strategy
 * @param {MlJobKind} [kind]
 */
export function mlJobPollDeadlineMs(strategy, kind = 'validate') {
  return mlJobTimeoutMs(strategy, kind) + ML_JOB_POLL_BUFFER_MS;
}

/**
 * Poll interval — slightly slower for long GPU trains to cut request chatter.
 * @param {number} elapsedMs
 * @param {number} budgetMs
 */
export function mlJobPollIntervalMs(elapsedMs, budgetMs) {
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
