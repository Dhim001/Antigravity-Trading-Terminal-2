/**
 * Module-level Optuna hyperparam-sweep poller.
 * Survives Model Training / Auto-Tune panel unmounts (tab switches).
 */
import { apiRequest } from '@/api/client';
import { toast } from 'sonner';
import {
  appendMlPollLog,
  finishMlJob,
  getMlTrainingSession,
  setMlServerProgress,
  setMlTuneResult,
} from '@/lib/mlTrainingSession';

let _timer = null;
let _activeJobId = null;
let _jobToken = null;
let _deadlineMs = 0;
let _claimed = false;

export function stopMlHyperparamSweepPolling() {
  if (_timer) {
    clearTimeout(_timer);
    _timer = null;
  }
}

function schedule(fn, delayMs) {
  stopMlHyperparamSweepPolling();
  _timer = setTimeout(fn, delayMs);
}

function applyProgress(job) {
  const prog = job?.progress && typeof job.progress === 'object' ? job.progress : null;
  if (prog) {
    setMlServerProgress({ ...prog, status: job.status });
  } else {
    appendMlPollLog({
      status: job?.status || 'running',
      phase: 'waiting',
      detail: 'no progress payload yet',
      note: 'poll',
    });
  }
}

function finalize(job) {
  if (_claimed) return;
  _claimed = true;
  stopMlHyperparamSweepPolling();
  const token = _jobToken;
  const jobId = _activeJobId;
  _activeJobId = null;
  _jobToken = null;

  const sess = getMlTrainingSession();
  if (token != null && sess.jobToken !== token) return;
  if (jobId && sess.jobId && sess.jobId !== jobId) return;

  const res = (job?.result && typeof job.result === 'object') ? job.result : {};
  const status = String(job?.status || '').toLowerCase();

  if (status === 'cancelled' || res.cancelled) {
    appendMlPollLog({
      status: 'cancelled',
      phase: 'cancelled',
      detail: 'sweep cancelled',
      level: 'warn',
    });
    toast.message('Hyperparam sweep cancelled');
    finishMlJob(token);
    return;
  }

  if (status !== 'done' || res.ok === false) {
    const err = res.error || job?.error || 'Hyperparam sweep failed';
    appendMlPollLog({
      status: 'error',
      phase: 'error',
      detail: err,
      warning: err,
      level: 'warn',
    });
    setMlTuneResult(res);
    toast.error(err);
    finishMlJob(token, { error: err });
    return;
  }

  setMlServerProgress({
    pct: 100,
    phase: 'done',
    detail: `best=${res.best_score ?? '—'} · ${res.trials_completed ?? 0} trials`,
    best_score: res.best_score,
    trial: res.trials_completed,
    max_trials: res.max_trials,
    objective_kind: res.objective_kind,
    fidelity_phase: res.multi_fidelity ? 'full' : 'full',
    status: 'done',
  });
  appendMlPollLog({
    status: 'done',
    pct: 100,
    phase: 'done',
    detail: `best=${res.best_score ?? '—'} · trials=${res.trials_completed ?? 0}`,
    best_score: res.best_score,
    trial: res.trials_completed,
    max_trials: res.max_trials,
    level: 'info',
  });
  setMlTuneResult(res);
  toast.success(
    `Best score ${res.best_score ?? '—'} · ${res.trials_completed ?? 0} trials`,
  );
  finishMlJob(token);
}

/**
 * Start (or replace) background polling for a hyperparam-sweep job.
 * Safe to call again for the same job_id — no duplicate timers.
 */
export function startMlHyperparamSweepPolling(jobId, {
  jobToken = null,
  timeBudgetSec = 600,
} = {}) {
  const id = String(jobId || '').trim();
  if (!id) return;

  if (_activeJobId === id && _timer) {
    if (jobToken != null) _jobToken = jobToken;
    return;
  }

  stopMlHyperparamSweepPolling();
  _activeJobId = id;
  _jobToken = jobToken;
  _claimed = false;
  _deadlineMs = Date.now() + Math.max(Number(timeBudgetSec) * 1000, 120_000) + 60_000;

  const poll = () => {
    if (_activeJobId !== id) return;
    if (Date.now() > _deadlineMs) {
      appendMlPollLog({
        status: 'error',
        phase: 'timeout',
        detail: 'Hyperparam sweep timed out',
        warning: 'timed out',
        level: 'warn',
      });
      toast.error('Hyperparam sweep timed out');
      finishMlJob(_jobToken, { error: 'Hyperparam sweep timed out' });
      stopMlHyperparamSweepPolling();
      _activeJobId = null;
      _jobToken = null;
      return;
    }

    apiRequest(`/api/v1/ml/hyperparam-sweep/${encodeURIComponent(id)}`, {
      method: 'GET',
      timeoutMs: 15_000,
    })
      .then((body) => {
        if (_activeJobId !== id) return;
        const job = body?.job || body;
        if (!job) {
          schedule(poll, 2000);
          return;
        }
        applyProgress(job);
        if (['done', 'error', 'cancelled'].includes(String(job.status || '').toLowerCase())) {
          finalize(job);
          return;
        }
        schedule(poll, 1500);
      })
      .catch((err) => {
        if (_activeJobId !== id) return;
        appendMlPollLog({
          status: 'running',
          phase: 'waiting',
          detail: 'server busy — still polling…',
          note: 'poll_err',
          warning: err?.message || 'poll failed',
          level: 'warn',
        });
        schedule(poll, 2500);
      });
  };

  schedule(poll, 400);
}

/** True when the module poller is watching this job (or any job if jobId omitted). */
export function isMlHyperparamSweepPolling(jobId = null) {
  if (!_activeJobId || !_timer) return false;
  if (jobId == null) return true;
  return _activeJobId === String(jobId);
}

/** Test helper — reset module state. */
export function __resetMlHyperparamSweepPollingForTests() {
  stopMlHyperparamSweepPolling();
  _activeJobId = null;
  _jobToken = null;
  _deadlineMs = 0;
  _claimed = false;
}
