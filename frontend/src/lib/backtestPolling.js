/** In-process backtest job poll timer — shared by endpoints + dispatch. */

let _backtestPollTimer = null;

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
