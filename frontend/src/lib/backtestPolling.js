/** In-process backtest job poll timer — shared by endpoints + dispatch. */

let _backtestPollTimer = null;

/**
 * Parse server progress freshness (ISO string or epoch seconds/ms).
 * @param {unknown} job
 * @returns {number|null} epoch ms, or null if unavailable
 */
export function backtestJobProgressUpdatedAtMs(job) {
  const raw = job?.progress?.updated_at ?? job?.updated_at ?? null;
  if (raw == null || raw === '') return null;
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    // Heuristic: < 1e12 → seconds, else ms.
    return raw < 1e12 ? raw * 1000 : raw;
  }
  const ms = Date.parse(String(raw));
  return Number.isFinite(ms) ? ms : null;
}

/** Stall-detector fingerprint — any field change counts as forward progress. */
export function backtestJobProgressFingerprint(job) {
  const p = job?.progress || {};
  return [
    job?.status ?? '',
    p.pct ?? '',
    p.bar ?? '',
    p.phase ?? '',
    p.elapsed_sec ?? '',
    p.message ?? '',
    p.updated_at ?? '',
  ].join('|');
}

/**
 * True when a successfully fetched running/pending job has frozen server progress.
 * Prefers ``progress.updated_at`` (authoritative); falls back to fingerprint age.
 */
export function isBacktestJobProgressStalled(job, {
  nowMs = Date.now(),
  stallMs = 15 * 60 * 1000,
  lastFingerprint = '',
  lastAdvanceAtMs = nowMs,
} = {}) {
  const status = job?.status;
  if (!['pending', 'running'].includes(status)) return false;

  const updatedMs = backtestJobProgressUpdatedAtMs(job);
  if (updatedMs != null) {
    return nowMs - updatedMs > stallMs;
  }

  const fp = backtestJobProgressFingerprint(job);
  if (fp !== lastFingerprint) return false;
  return nowMs - lastAdvanceAtMs > stallMs;
}

/** Job IDs already fully handled (WS result or poll) — prevents duplicate toasts/writes. */
const _completedJobIds = new Set();
const _COMPLETED_JOB_CAP = 40;

export function stopBacktestJobPolling() {
  if (_backtestPollTimer) {
    clearTimeout(_backtestPollTimer);
    _backtestPollTimer = null;
  }
}

export function scheduleBacktestJobPoll(fn, delayMs) {
  stopBacktestJobPolling();
  _backtestPollTimer = setTimeout(fn, delayMs);
}

/**
 * Claim sole ownership of a deferred job's completion UX (toast + final apply).
 * Returns false if another path (WS vs poll) already claimed this job_id.
 * Missing job_id always claims (sync / non-deferred path).
 */
export function claimBacktestJobCompletion(jobId) {
  const key = jobId != null && String(jobId).trim() ? String(jobId).trim() : null;
  if (!key) return true;
  if (_completedJobIds.has(key)) return false;
  _completedJobIds.add(key);
  if (_completedJobIds.size > _COMPLETED_JOB_CAP) {
    const oldest = _completedJobIds.values().next().value;
    _completedJobIds.delete(oldest);
  }
  return true;
}

/** Test helper — clear claimed set between unit tests. */
export function resetBacktestJobCompletionClaims() {
  _completedJobIds.clear();
}
