/**
 * Server-side durable batch train runner (ML Lab Phase 2).
 *
 * Thin orchestration over the /api/v1/ml/batch-train API: submit, poll until
 * terminal, and map the server payload onto the same summary shape that
 * batchTrainRunner produces so BatchTrainDialog renders both paths
 * identically. Pure + dependency-injected (fetch/cancel/sleep) for node tests.
 */

import { isTransientMlPollError } from '@/lib/mlJobTimeouts';

/** Batch statuses after which polling stops. */
export const SERVER_BATCH_TERMINAL_STATUSES = new Set(['done', 'failed', 'cancelled']);
/** Item statuses that count as finished for progress display. */
export const SERVER_BATCH_ITEM_TERMINAL_STATUSES = new Set(['done', 'error', 'cancelled', 'skipped']);
/** Poll cadence while a server batch is active. */
export const SERVER_BATCH_POLL_INTERVAL_MS = 2500;
/** Consecutive poll failures tolerated before abandoning tracking. */
export const SERVER_BATCH_MAX_POLL_ERRORS = 10;

const defaultSleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/**
 * True when the submit/poll failure means the backend predates the batch API
 * (unknown route, or the server is unreachable) — the caller should fall back
 * to the local frontend queue. Genuine API errors (400 validation, 500s) and
 * a "batch not found" 404 from a *new* backend return false.
 */
export function isBatchApiUnavailableError(err) {
  if (!err) return false;
  // fetch() rejects with TypeError on connection refused / DNS / offline.
  if (err instanceof TypeError) return true;
  const msg = String(err?.message || err || '');
  return /route not found|http 404|^not found$|failed to fetch|fetch failed|network ?error|load failed|network request failed|econnrefused|econnreset/i.test(msg);
}

/** Unique key so a retried submit never double-creates a batch. */
export function makeBatchIdempotencyKey() {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
  } catch {
    /* non-secure context — fall through */
  }
  return `ml-batch-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * Build the POST /ml/batch-train items list. Each item carries the shared
 * timeframe/window merged with the per-strategy knob snapshot frozen at
 * batch start (overrides win on key conflicts).
 */
export function buildBatchItems(queue, { configOverrides, timeframe, trainingWindow, autoValidate } = {}) {
  const months = Number(trainingWindow);
  const base = {
    ...(timeframe ? { timeframe } : {}),
    ...(Number.isFinite(months) && months > 0 ? { training_window_months: months } : {}),
  };
  const list = Array.isArray(queue) ? queue : [];
  return list.map((id) => ({
    strategy: id,
    config: { ...base, ...((configOverrides && configOverrides[id]) || {}) },
    validate_after: Boolean(autoValidate),
  }));
}

/**
 * Try the durable server batch API.
 * Resolves `{ batchId, idempotent }` on success, `{ unavailable: true }` when
 * the backend predates the API (caller falls back to the local queue), and
 * rethrows real errors (400/500, missing batch_id).
 */
export async function trySubmitServerBatch({
  symbol,
  items,
  submit,
  idempotencyKey,
  concurrency = 1,
  failFast = false,
  schedule,
} = {}) {
  const list = Array.isArray(items) ? items : [];
  if (!symbol || !list.length || typeof submit !== 'function') {
    return { unavailable: true, reason: !symbol ? 'no-symbol' : 'no-items' };
  }
  let body;
  try {
    body = await submit({
      symbol,
      items: list,
      concurrency,
      fail_fast: failFast,
      ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
      ...(schedule ? { schedule } : {}),
    });
  } catch (err) {
    if (isBatchApiUnavailableError(err)) return { unavailable: true, error: err };
    throw err;
  }
  const batchId = String(body?.batch_id || '').trim();
  if (!batchId) {
    throw new Error(body?.error || 'Server did not return a batch_id');
  }
  return { batchId, idempotent: Boolean(body?.idempotent) };
}

export function isServerBatchTerminal(batch) {
  return SERVER_BATCH_TERMINAL_STATUSES.has(String(batch?.status || '').toLowerCase());
}

/** Map a server batch payload onto the dialog's { index, total, strategy } progress. */
export function deriveServerProgress(batch) {
  const items = Array.isArray(batch?.items) ? batch.items : [];
  const total = Number(batch?.total) || items.length;
  const runningItem = items.find((it) => it?.status === 'running');
  let finished = 0;
  for (const it of items) {
    if (SERVER_BATCH_ITEM_TERMINAL_STATUSES.has(it?.status)) finished += 1;
  }
  return {
    index: runningItem ? finished + 1 : finished,
    total,
    strategy: runningItem?.strategy || null,
  };
}

/**
 * Map a terminal server batch onto the local runner's summary shape so the
 * dialog can reuse formatBatchTrainSummary / remainingAfterSummary untouched.
 */
export function summarizeServerBatch(batch) {
  const items = Array.isArray(batch?.items) ? batch.items : [];
  const completed = items.filter((it) => it?.status === 'done').map((it) => it.strategy);
  const failedIds = items.filter((it) => it?.status === 'error').map((it) => it.strategy);
  const cancelledIds = items
    .filter((it) => it?.status === 'cancelled' || it?.status === 'skipped')
    .map((it) => it.strategy);
  const status = String(batch?.status || '').toLowerCase();
  const stoppedEarly = status === 'cancelled' || Boolean(batch?.cancel_requested);
  return {
    ok: Number.isFinite(Number(batch?.completed)) ? Number(batch.completed) : completed.length,
    failed: Number.isFinite(Number(batch?.failed)) ? Number(batch.failed) : failedIds.length,
    cancelled: Number.isFinite(Number(batch?.cancelled)) ? Number(batch.cancelled) : cancelledIds.length,
    stoppedEarly,
    total: Number(batch?.total) || items.length,
    completed,
    failedIds,
    cancelledIds,
    status,
    batchId: batch?.batch_id || null,
    server: true,
  };
}

/**
 * Poll a server batch until it reaches a terminal status.
 *
 * @param {object} opts
 * @param {string} opts.batchId
 * @param {(batchId: string) => Promise<object>} opts.fetchBatch e.g. fetchMlBatch
 * @param {(batchId: string) => Promise<unknown>} [opts.cancelBatch] e.g. cancelMlBatch —
 *   invoked once as a backstop when shouldCancel flips (the dialog's Cancel
 *   button also calls it directly for responsiveness; server cancel is idempotent).
 * @param {() => boolean} [opts.shouldCancel]
 * @param {(p: { index: number, total: number, strategy: string|null }) => void} [opts.onProgress]
 * @param {(batch: object) => void} [opts.onBatchUpdate] raw payload per poll (status line)
 * @param {number} [opts.pollIntervalMs]
 * @param {(ms: number) => Promise<void>} [opts.sleep]
 * @param {number} [opts.maxConsecutivePollErrors]
 * @returns {Promise<ReturnType<typeof summarizeServerBatch>>}
 */
export async function runServerBatchTrain({
  batchId,
  fetchBatch,
  cancelBatch,
  shouldCancel,
  onProgress,
  onBatchUpdate,
  pollIntervalMs = SERVER_BATCH_POLL_INTERVAL_MS,
  sleep = defaultSleep,
  maxConsecutivePollErrors = SERVER_BATCH_MAX_POLL_ERRORS,
} = {}) {
  const id = String(batchId || '').trim();
  if (!id) throw new Error('batchId required');
  if (typeof fetchBatch !== 'function') throw new Error('fetchBatch fn required');

  let cancelSent = false;
  let consecutiveErrors = 0;
  for (;;) {
    try {
      const batch = await fetchBatch(id);
      if (batch?.ok === false) {
        throw new Error(batch?.error || 'Batch status unavailable');
      }
      consecutiveErrors = 0;
      onBatchUpdate?.(batch);
      onProgress?.(deriveServerProgress(batch));
      if (isServerBatchTerminal(batch)) {
        return summarizeServerBatch(batch);
      }
    } catch (err) {
      consecutiveErrors += 1;
      if (!isTransientMlPollError(err) || consecutiveErrors >= maxConsecutivePollErrors) {
        throw err;
      }
    }
    if (!cancelSent && shouldCancel?.()) {
      cancelSent = true;
      if (typeof cancelBatch === 'function') {
        try {
          await cancelBatch(id);
        } catch {
          /* the next status poll still settles the batch */
        }
      }
    }
    await sleep(pollIntervalMs);
  }
}

/**
 * Re-queue error/cancelled items of an existing server batch.
 * Returns the normalized `{ batchId, status, requeued }`; throws on failure.
 */
export async function retryServerBatch({ batchId, retry } = {}) {
  const id = String(batchId || '').trim();
  if (!id) throw new Error('batchId required');
  if (typeof retry !== 'function') throw new Error('retry fn required');
  const body = await retry(id);
  if (body?.ok === false) {
    throw new Error(body?.error || 'Batch retry failed');
  }
  return {
    batchId: String(body?.batch_id || id),
    status: body?.status || null,
    requeued: Number(body?.requeued) || 0,
  };
}
