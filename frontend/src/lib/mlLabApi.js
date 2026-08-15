import { apiRequest, isAbortError } from '@/api/client';
import {
  getCachedModelStatus,
  setCachedModelStatus,
} from '@/lib/mlTrainingSession';
import {
  ML_JOB_STATUS_POLL_TIMEOUT_MS,
  ML_JOB_SUBMIT_TIMEOUT_MS,
} from '@/lib/mlJobTimeouts';

/**
 * Fetch trained/model-status rows for all ML strategies (inventory grid).
 * Mirrors fetchInventory logic in ModelTrainingDashboard.
 */
export async function fetchMlInventory(symbol, strategies, timeframe) {
  if (!symbol) return [];
  const rows = await Promise.all(
    strategies.map(async (id) => {
      try {
        const body = await apiRequest(
          `/api/v1/ml/model-status?symbol=${encodeURIComponent(symbol)}&strategy=${encodeURIComponent(id)}&timeframe=${encodeURIComponent(timeframe)}`,
        );
        if (body) setCachedModelStatus(symbol, id, body, timeframe);
        return {
          strategy: id,
          trained: Boolean(body?.trained),
          trained_at: body?.trained_at,
          metrics: body?.metrics || {},
          error: body?.error,
          timeframe: body?.timeframe || timeframe,
        };
      } catch (err) {
        if (isAbortError(err)) {
          const cached = getCachedModelStatus(symbol, id, timeframe);
          if (cached) {
            return {
              strategy: id,
              trained: Boolean(cached.trained),
              trained_at: cached.trained_at,
              metrics: cached.metrics || {},
              timeframe,
            };
          }
        }
        const cached = getCachedModelStatus(symbol, id, timeframe);
        if (cached?.trained) {
          return {
            strategy: id,
            trained: true,
            trained_at: cached.trained_at,
            metrics: cached.metrics || {},
            stale: true,
            timeframe,
          };
        }
        return { strategy: id, trained: false, error: err.message, timeframe };
      }
    }),
  );
  return rows;
}

export async function fetchMlRetrainQueue() {
  return apiRequest('/api/v1/ml/retrain-status');
}

export async function fetchMlQueueTelemetry() {
  const body = await apiRequest('/api/v1/ml/jobs?limit=5');
  return {
    active: Number(body?.active) || 0,
    queued: Number(body?.queued) || 0,
  };
}

export async function fetchMlTrainRuns(symbol, strategy, timeframe) {
  if (!symbol) return [];
  const qs = new URLSearchParams({
    symbol,
    limit: '15',
    timeframe,
  });
  if (strategy) qs.set('strategy', strategy);
  const body = await apiRequest(`/api/v1/ml/runs?${qs.toString()}`);
  return Array.isArray(body?.runs) ? body.runs : [];
}

export async function fetchMlModelStatus(symbol, strategy, timeframe) {
  return apiRequest(
    `/api/v1/ml/model-status?symbol=${encodeURIComponent(symbol)}&strategy=${encodeURIComponent(strategy)}&timeframe=${encodeURIComponent(timeframe)}`,
  );
}

export async function submitMlTrainJob(params) {
  return apiRequest('/api/v1/ml/train', {
    method: 'POST',
    body: params,
    timeoutMs: ML_JOB_SUBMIT_TIMEOUT_MS,
  });
}

export async function submitMlValidateJob(params) {
  return apiRequest('/api/v1/ml/validate', {
    method: 'POST',
    body: params,
    timeoutMs: ML_JOB_SUBMIT_TIMEOUT_MS,
  });
}

export async function activateMlVersion(params) {
  return apiRequest('/api/v1/ml/activate-version', {
    method: 'POST',
    body: params,
    timeoutMs: 60_000,
  });
}

export async function deleteMlVersion(params) {
  return apiRequest('/api/v1/ml/delete-version', {
    method: 'POST',
    body: params,
    timeoutMs: 60_000,
  });
}

export async function updateMlVersion(params) {
  return apiRequest('/api/v1/ml/update-version', {
    method: 'POST',
    body: params,
    timeoutMs: 60_000,
  });
}

export async function cancelMlJob(jobId) {
  return apiRequest(`/api/v1/ml/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
    timeoutMs: 30_000,
  });
}

export async function pollMlJob(jobId) {
  return apiRequest(`/api/v1/ml/jobs/${encodeURIComponent(jobId)}`, {
    timeoutMs: ML_JOB_STATUS_POLL_TIMEOUT_MS,
  });
}

function jobIdOf(job) {
  return String(job?.job_id || job?.id || '').trim();
}

/**
 * Latest hyperparam-sweep job for symbol+strategy (newest-first job list),
 * then GET the full payload (list rows omit `result`).
 */
export async function fetchLatestMlHyperparamSweep(symbol, strategy) {
  const sym = String(symbol || '').toUpperCase();
  const strat = String(strategy || '').toUpperCase();
  if (!sym || !strat) return null;
  const list = await apiRequest('/api/v1/ml/jobs?limit=50', {
    timeoutMs: ML_JOB_STATUS_POLL_TIMEOUT_MS,
  });
  const jobs = Array.isArray(list?.jobs) ? list.jobs : [];
  const match = jobs.find((j) => {
    const kind = String(j?.kind || '').toLowerCase();
    return kind === 'hyperparam_sweep'
      && String(j?.symbol || '').toUpperCase() === sym
      && String(j?.strategy || '').toUpperCase() === strat;
  });
  if (!match) return null;
  const id = jobIdOf(match);
  if (!id) return match;
  const full = await apiRequest(`/api/v1/ml/hyperparam-sweep/${encodeURIComponent(id)}`, {
    timeoutMs: ML_JOB_STATUS_POLL_TIMEOUT_MS,
  });
  return full?.job || match;
}
