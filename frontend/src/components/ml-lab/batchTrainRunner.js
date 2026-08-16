/**
 * Sequential batch-train queue runner (pure async helper for BatchTrainDialog + tests).
 */

/** Storage key for the in-progress batch queue (sessionStorage). */
export const BATCH_QUEUE_STORAGE_KEY = 'ml_batch_queue';
/** Saved queues older than this are ignored (stale session leftovers). */
export const BATCH_QUEUE_MAX_AGE_MS = 24 * 60 * 60 * 1000;

/**
 * Throw this (or any Error with `cancelled: true` / `code: 'cancelled'`) from
 * onTrainStrategy / onValidateStrategy when the underlying ML job ended with
 * status `cancelled`. Cancelled items are counted separately from failures and
 * abort the rest of the queue.
 */
export class MlJobCancelledError extends Error {
  constructor(strategyId) {
    super(`Training cancelled for ${strategyId}`);
    this.name = 'MlJobCancelledError';
    this.cancelled = true;
    this.strategyId = strategyId;
  }
}

export function isMlJobCancelledError(err) {
  return Boolean(
    err?.cancelled
    || err?.name === 'MlJobCancelledError'
    || err?.code === 'cancelled',
  );
}

/**
 * Best-effort true-cancel of the in-flight ML job. The caller keeps its
 * soft-stop flag either way; this just tells the server to abort the job.
 *
 * @param {object} opts
 * @param {string|null} [opts.jobId] active ML job id (from the training session)
 * @param {(jobId: string) => Promise<unknown>} [opts.cancelJob] e.g. cancelMlJob
 * @returns {Promise<{ requested: boolean, jobId: string|null }>}
 */
export async function requestBatchCancel({ jobId, cancelJob } = {}) {
  const id = String(jobId || '').trim();
  if (!id || typeof cancelJob !== 'function') return { requested: false, jobId: id || null };
  try {
    await cancelJob(id);
    return { requested: true, jobId: id };
  } catch {
    return { requested: false, jobId: id };
  }
}

/**
 * Read a saved in-progress batch queue. Returns null when nothing usable is
 * stored, the payload is corrupt, there are no remaining strategies, or the
 * entry is older than {@link BATCH_QUEUE_MAX_AGE_MS}.
 *
 * @param {Pick<Storage, 'getItem'>|null|undefined} storage
 * @param {number} [now]
 */
export function readSavedBatchQueue(storage, now = Date.now()) {
  if (!storage) return null;
  let parsed;
  try {
    const raw = storage.getItem(BATCH_QUEUE_STORAGE_KEY);
    if (!raw) return null;
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  const startedAt = Number(parsed?.startedAt ?? parsed?.savedAt);
  if (!Number.isFinite(startedAt) || now - startedAt > BATCH_QUEUE_MAX_AGE_MS) return null;
  const asIds = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []);
  const queue = asIds(parsed?.queue);
  const remaining = asIds(parsed?.remaining);
  if (!remaining.length) return null;
  return {
    symbol: parsed?.symbol || null,
    timeframe: parsed?.timeframe || null,
    trainingWindow: parsed?.trainingWindow || null,
    scope: parsed?.scope || null,
    queue,
    remaining,
    completed: asIds(parsed?.completed),
    failedIds: asIds(parsed?.failedIds),
    autoValidate: Boolean(parsed?.autoValidate),
    configOverrides: (parsed?.configOverrides && typeof parsed.configOverrides === 'object')
      ? parsed.configOverrides
      : null,
    startedAt,
    savedAt: Number(parsed?.savedAt) || startedAt,
  };
}

/**
 * Persist the in-progress batch queue. An empty `remaining` list clears the
 * entry instead (nothing left to resume).
 *
 * @param {Pick<Storage, 'setItem', 'removeItem'>|null|undefined} storage
 * @param {object} state
 * @returns {boolean} whether a queue entry is stored afterwards
 */
export function writeBatchQueueState(storage, state = {}) {
  if (!storage) return false;
  const remaining = Array.isArray(state.remaining) ? state.remaining : [];
  if (!remaining.length) {
    clearSavedBatchQueue(storage);
    return false;
  }
  const startedAt = Number(state.startedAt);
  const payload = {
    version: 1,
    symbol: state.symbol || null,
    timeframe: state.timeframe || null,
    trainingWindow: state.trainingWindow || null,
    scope: state.scope || null,
    queue: Array.isArray(state.queue) ? state.queue : [],
    remaining,
    completed: Array.isArray(state.completed) ? state.completed : [],
    failedIds: Array.isArray(state.failedIds) ? state.failedIds : [],
    autoValidate: Boolean(state.autoValidate),
    configOverrides: (state.configOverrides && typeof state.configOverrides === 'object')
      ? state.configOverrides
      : null,
    startedAt: Number.isFinite(startedAt) ? startedAt : Date.now(),
    savedAt: Date.now(),
  };
  try {
    storage.setItem(BATCH_QUEUE_STORAGE_KEY, JSON.stringify(payload));
    return true;
  } catch {
    return false;
  }
}

/** @param {Pick<Storage, 'removeItem'>|null|undefined} storage */
export function clearSavedBatchQueue(storage) {
  try {
    storage?.removeItem(BATCH_QUEUE_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * Strategies a resume should re-run after a summary: everything not yet
 * attempted, plus a cancelled item (it never finished). Failed items are
 * intentionally left out — the "Retry failed" action owns those.
 */
export function remainingAfterSummary(queue, summary) {
  const list = Array.isArray(queue) ? queue : [];
  const attemptedBeforeCancel = (Number(summary?.ok) || 0) + (Number(summary?.failed) || 0);
  return list.slice(attemptedBeforeCancel);
}

/**
 * Run strategies one-by-one. Failures are isolated — the queue continues.
 * A cancelled item (MlJobCancelledError) is counted separately and stops the queue.
 *
 * @param {object} opts
 * @param {string[]} opts.queue
 * @param {(strategyId: string, config?: object) => Promise<unknown>} opts.onTrainStrategy
 * @param {(strategyId: string, config?: object) => Promise<unknown>} [opts.onValidateStrategy]
 * @param {boolean} [opts.autoValidate]
 * @param {() => boolean} [opts.shouldCancel]
 * @param {(p: { index: number, total: number, strategy: string }) => void} [opts.onProgress]
 * @param {(strategyId: string, err: Error) => void} [opts.onStrategyError]
 * @param {(strategyId: string, err: Error) => void} [opts.onStrategyCancelled]
 * @param {Object<string, object>} [opts.configOverrides] per-strategy knob snapshot
 * @returns {Promise<{
 *   ok: number, failed: number, cancelled: number, stoppedEarly: boolean,
 *   total: number, completed: string[], failedIds: string[], cancelledIds: string[],
 * }>}
 */
export async function runBatchTrainQueue({
  queue,
  onTrainStrategy,
  onValidateStrategy,
  autoValidate = false,
  shouldCancel,
  onProgress,
  onStrategyError,
  onStrategyCancelled,
  configOverrides,
} = {}) {
  const list = Array.isArray(queue) ? queue : [];
  let ok = 0;
  let failed = 0;
  let cancelled = 0;
  const completed = [];
  const failedIds = [];
  const cancelledIds = [];

  const summary = (stoppedEarly) => ({
    ok,
    failed,
    cancelled,
    stoppedEarly,
    total: list.length,
    completed,
    failedIds,
    cancelledIds,
  });

  for (let i = 0; i < list.length; i += 1) {
    if (shouldCancel?.()) {
      return summary(true);
    }
    const strategyId = list[i];
    const config = configOverrides?.[strategyId];
    onProgress?.({ index: i + 1, total: list.length, strategy: strategyId });
    try {
      await onTrainStrategy(strategyId, config);
      if (autoValidate && typeof onValidateStrategy === 'function' && !shouldCancel?.()) {
        await onValidateStrategy(strategyId, config);
      }
      ok += 1;
      completed.push(strategyId);
    } catch (err) {
      const error = err instanceof Error ? err : new Error(String(err?.message || err));
      if (isMlJobCancelledError(error)) {
        cancelled += 1;
        cancelledIds.push(strategyId);
        onStrategyCancelled?.(strategyId, error);
        return summary(true);
      }
      failed += 1;
      failedIds.push(strategyId);
      onStrategyError?.(strategyId, error);
    }
  }

  return summary(Boolean(shouldCancel?.()));
}

/**
 * Format the end-of-batch toast summary.
 * Accepts both the current shape (`cancelled` count + `stoppedEarly` flag) and
 * the legacy shape (`cancelled` boolean) for the stopped-early case.
 *
 * @param {{ ok: number, failed: number, cancelled?: number|boolean, stoppedEarly?: boolean, total: number }} summary
 */
export function formatBatchTrainSummary({ ok, failed, cancelled = 0, stoppedEarly = false, total }) {
  const cancelledCount = typeof cancelled === 'number' ? cancelled : (cancelled ? 1 : 0);
  const stopped = stoppedEarly || cancelledCount > 0;
  if (stopped) {
    return `Stopped early. Trained ${ok}/${total}. ${failed} failed. ${cancelledCount} cancelled.`;
  }
  const failBit = failed ? ` ${failed} failed.` : '';
  const cancelBit = cancelledCount ? ` ${cancelledCount} cancelled.` : '';
  return `Trained ${ok}/${total} strategies.${failBit}${cancelBit}`;
}
