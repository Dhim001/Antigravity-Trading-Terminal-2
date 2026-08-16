/**
 * Batch item observability helpers (ML Lab Phase 3).
 *
 * Pure functions backing the BatchTrainDialog "Details" drawer and the
 * per-item failure toasts: normalize server/local batch items into drawer
 * rows, derive per-item durations, diff polls for newly failed items, and
 * map raw error strings onto concise actionable reasons.
 */

/** Item statuses the drawer knows how to render. */
export const BATCH_ITEM_STATUSES = Object.freeze([
  'pending',
  'running',
  'done',
  'error',
  'cancelled',
  'skipped',
]);

/** Item statuses that count as finished (mirrors the server runner). */
export const BATCH_ITEM_TERMINAL = new Set(['done', 'error', 'cancelled', 'skipped']);

/** Max chars of a raw error snippet shown before truncation. */
export const BATCH_ERROR_SNIPPET_LEN = 90;

const ERROR_REASON_PATTERNS = [
  {
    reason: 'not enough historical data',
    re: /insufficient candles|not enough (?:candles|data|history|bars)|need >=|no candle data|empty (?:dataset|candles)|lack(?:ing)? of data|too few (?:candles|bars|samples)/i,
  },
  {
    reason: 'training queue full',
    re: /\b429\b|rate limit|too many requests|queue (?:is )?full|throttl/i,
  },
  {
    reason: 'out of memory',
    re: /out of memory|\boom\b|rss|memory ?limit|worker (?:was )?killed|killed by oom|memoryerror|cannot allocate/i,
  },
  {
    reason: 'data quality gate rejected',
    re: /dq[_ ]gate|data quality|quality gate|data_quality/i,
  },
  {
    reason: 'missing dependency',
    re: /modulenotfounderror|importerror|no module named|missing (?:dependency|module|package)|not installed|requires .+ package/i,
  },
  {
    reason: 'timed out',
    re: /timed? ?out|timeout|deadline exceeded|etimedout/i,
  },
];

/**
 * Map a raw batch item error onto a concise, actionable reason. Unknown
 * errors fall back to a trimmed raw snippet (never empty).
 */
export function describeBatchItemError(error) {
  const raw = String(error ?? '').trim();
  if (!raw) return 'training failed';
  for (const { reason, re } of ERROR_REASON_PATTERNS) {
    if (re.test(raw)) return reason;
  }
  return truncateBatchError(raw);
}

/** Trim an error string for display (single line, capped length). */
export function truncateBatchError(error, maxLen = BATCH_ERROR_SNIPPET_LEN) {
  const oneLine = String(error ?? '').replace(/\s+/g, ' ').trim();
  if (!oneLine) return '';
  const cap = Math.max(10, Number(maxLen) || BATCH_ERROR_SNIPPET_LEN);
  return oneLine.length > cap ? `${oneLine.slice(0, cap - 1)}…` : oneLine;
}

/**
 * Items that newly transitioned to `error` between two polled batches.
 * Diffing by item_id keeps toasts idempotent across the poll loop and
 * survives seq reuse after a server-side retry.
 */
export function diffNewBatchItemFailures(prevBatch, nextBatch) {
  const nextItems = Array.isArray(nextBatch?.items) ? nextBatch.items : [];
  if (!nextItems.length) return [];
  const prevByKey = new Map();
  for (const it of (Array.isArray(prevBatch?.items) ? prevBatch.items : [])) {
    prevByKey.set(batchItemKey(it), it);
  }
  const fresh = [];
  for (const item of nextItems) {
    if (item?.status !== 'error') continue;
    const prev = prevByKey.get(batchItemKey(item));
    if (prev?.status === 'error') continue;
    fresh.push(item);
  }
  return fresh;
}

/** Stable identity for an item across polls (id first, then position). */
export function batchItemKey(item) {
  return String(item?.item_id || item?.id || `seq-${item?.seq ?? '?'}`);
}

/**
 * Wall-clock duration for an item in ms. Terminal items measure
 * created_at → updated_at; running items measure created_at → `now`.
 * Returns null for pending items or unparsable timestamps.
 */
export function batchItemDurationMs(item, now = Date.now()) {
  const status = String(item?.status || 'pending');
  if (status === 'pending') return null;
  const t0 = Date.parse(item?.created_at || '');
  if (!Number.isFinite(t0)) return null;
  const t1 = BATCH_ITEM_TERMINAL.has(status)
    ? Date.parse(item?.updated_at || '')
    : Number(now);
  if (!Number.isFinite(t1) || t1 < t0) return null;
  return t1 - t0;
}

/**
 * Normalize batch items into drawer rows (rendering-ready view models).
 * Rows keep server order (seq) and carry a mapped error hint plus the raw
 * error for the tooltip.
 */
export function buildBatchDrawerRows(batch, { now = Date.now() } = {}) {
  const items = Array.isArray(batch?.items) ? batch.items : [];
  return items.map((item, idx) => {
    const status = BATCH_ITEM_STATUSES.includes(item?.status) ? item.status : 'pending';
    const error = status === 'error' ? String(item?.error || 'training failed') : (item?.error || null);
    return {
      key: batchItemKey(item),
      seq: Number.isFinite(Number(item?.seq)) ? Number(item.seq) : idx,
      strategy: String(item?.strategy || ''),
      status,
      durationMs: batchItemDurationMs(item, now),
      error: error ? truncateBatchError(error) : null,
      errorFull: error || null,
      errorHint: status === 'error' ? describeBatchItemError(item?.error) : null,
      jobId: item?.job_id || null,
    };
  });
}

/**
 * Synthesize a batch-shaped payload from a finished local-queue summary so
 * the drawer can render the last batch even when the server batch API was
 * unavailable (legacy fallback path).
 */
export function synthesizeLocalBatchSummary({ queue, summary, errors = {}, symbol = null } = {}) {
  const list = Array.isArray(queue) ? queue : [];
  const completed = new Set(Array.isArray(summary?.completed) ? summary.completed : []);
  const failed = new Set(Array.isArray(summary?.failedIds) ? summary.failedIds : []);
  const cancelled = new Set(Array.isArray(summary?.cancelledIds) ? summary.cancelledIds : []);
  const items = list.map((strategy, seq) => ({
    item_id: `local-${seq}`,
    seq,
    strategy,
    status: completed.has(strategy)
      ? 'done'
      : failed.has(strategy)
        ? 'error'
        : cancelled.has(strategy)
          ? 'cancelled'
          : 'pending',
    error: failed.has(strategy) ? (errors[strategy] || 'training failed') : null,
    job_id: null,
    created_at: null,
    updated_at: null,
  }));
  // Mirrors ml_batch_runner._derive_batch_status for terminal local summaries.
  const status = summary?.stoppedEarly
    ? 'cancelled'
    : (failed.size > 0 && completed.size === 0 ? 'failed' : 'done');
  return {
    batch_id: null,
    symbol,
    status,
    total: list.length,
    completed: completed.size,
    failed: failed.size,
    cancelled: cancelled.size,
    items,
    local: true,
  };
}
