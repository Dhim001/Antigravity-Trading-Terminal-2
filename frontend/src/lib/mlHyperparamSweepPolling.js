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
import {
  ML_JOB_STATUS_POLL_TIMEOUT_MS,
  mlHyperparamSweepPollDeadlineMs,
} from '@/lib/mlJobTimeouts';

let _timer = null;
let _activeJobId = null;
let _jobToken = null;
let _deadlineMs = 0;
let _claimed = false;
let _softBudgetWarned = false;

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

  const outcome = {
    ...res,
    ok: res.ok !== false,
  };
  setMlServerProgress({
    pct: 100,
    phase: 'done',
    detail: `best=${outcome.best_score ?? '—'} · ${outcome.trials_completed ?? 0} trials`,
    best_score: outcome.best_score,
    trial: outcome.trials_completed,
    max_trials: outcome.max_trials,
    status: 'done',
  });
  appendMlPollLog({
    status: 'done',
    pct: 100,
    phase: 'done',
    detail: `best=${outcome.best_score ?? '—'} · trials=${outcome.trials_completed ?? 0}`,
    best_score: outcome.best_score,
    trial: outcome.trials_completed,
    max_trials: outcome.max_trials,
    level: 'info',
  });
  setMlTuneResult(outcome);
  toast.success(
    `Best score ${outcome.best_score ?? '—'} · ${outcome.trials_completed ?? 0} trials`,
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
  _softBudgetWarned = false;
  _deadlineMs = Date.now() + mlHyperparamSweepPollDeadlineMs(timeBudgetSec);

  const poll = () => {
    if (_activeJobId !== id) return;

    const overBudget = Date.now() > _deadlineMs;

    apiRequest(`/api/v1/ml/hyperparam-sweep/${encodeURIComponent(id)}`, {
      method: 'GET',
      timeoutMs: ML_JOB_STATUS_POLL_TIMEOUT_MS,
    })
      .then((body) => {
        if (_activeJobId !== id) return;
        const job = body?.job || body;
        if (!job) {
          schedule(poll, 2000);
          return;
        }
        applyProgress(job);
        const status = String(job.status || '').toLowerCase();
        if (['done', 'error', 'cancelled'].includes(status)) {
          finalize(job);
          return;
        }
        if (overBudget && !_softBudgetWarned) {
          _softBudgetWarned = true;
          appendMlPollLog({
            status: 'running',
            phase: 'waiting',
            detail: 'past Optuna time budget — still running on the server',
            warning: 'over_budget',
            level: 'warn',
          });
          toast.message('Auto-tune is past the time budget — still running on the server');
        }
        schedule(poll, overBudget ? 4000 : 1500);
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
  _softBudgetWarned = false;
}
