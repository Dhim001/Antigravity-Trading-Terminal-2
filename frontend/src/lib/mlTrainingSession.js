/**
 * Survives Model Training panel unmounts (tab switches, flexlayout remounts).
 * In-flight train/validate HTTP work continues; UI rehydrates from this session.
 */
import { isAbortError } from '@/api/client';

const statusCache = new Map();
const listeners = new Set();

let session = {
  strategy: null,
  symbol: null,
  training: false,
  validating: false,
  jobProgress: null,
  validation: null,
  lastError: null,
  jobToken: 0,
};

function emit() {
  listeners.forEach((fn) => {
    try {
      fn(session);
    } catch {
      /* ignore subscriber errors */
    }
  });
}

function patch(partial) {
  session = { ...session, ...partial };
  emit();
  return session;
}

export function getMlTrainingSession() {
  return session;
}

export function subscribeMlTrainingSession(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function statusCacheKey(symbol, strategy) {
  return `${String(symbol || '').toUpperCase()}|${String(strategy || '').toUpperCase()}`;
}

export function getCachedModelStatus(symbol, strategy) {
  return statusCache.get(statusCacheKey(symbol, strategy)) ?? null;
}

export function setCachedModelStatus(symbol, strategy, body) {
  if (!symbol || !strategy || !body || typeof body !== 'object') return;
  // Don't cache hard failures as the only truth — keep last good if present.
  if (body.error && !body.trained && getCachedModelStatus(symbol, strategy)?.trained) {
    return;
  }
  statusCache.set(statusCacheKey(symbol, strategy), body);
}

export function beginMlJob({ kind, strategy, symbol, jobProgress }) {
  const jobToken = session.jobToken + 1;
  return patch({
    jobToken,
    strategy,
    symbol,
    training: kind === 'train',
    validating: kind === 'validate',
    jobProgress: jobProgress ? { ...jobProgress, token: jobToken, active: true } : null,
    lastError: null,
    ...(kind === 'validate' ? { validation: null } : {}),
  });
}

export function finishMlJob(token, { validation = undefined, error = null } = {}) {
  if (token != null && token !== session.jobToken) return session;
  return patch({
    training: false,
    validating: false,
    jobProgress: session.jobProgress
      ? { ...session.jobProgress, active: false }
      : null,
    lastError: error,
    ...(validation !== undefined ? { validation } : {}),
  });
}

export function clearMlJobProgress(token) {
  if (token != null && token !== session.jobToken) return;
  if (session.jobProgress) {
    patch({ jobProgress: null });
  }
}

export function setMlValidation(validation) {
  return patch({ validation });
}

/** Prefer cached status over transient fetch errors / aborts. */
export function resolveModelStatusFetch(symbol, strategy, { body, error, previous }) {
  if (body && typeof body === 'object') {
    setCachedModelStatus(symbol, strategy, body);
    return body;
  }
  if (error && isAbortError(error)) {
    return previous ?? getCachedModelStatus(symbol, strategy);
  }
  const cached = getCachedModelStatus(symbol, strategy);
  if (cached?.trained) {
    return {
      ...cached,
      stale: true,
      fetch_error: error?.message || 'Status temporarily unavailable',
    };
  }
  if (previous?.trained) {
    return {
      ...previous,
      stale: true,
      fetch_error: error?.message || 'Status temporarily unavailable',
    };
  }
  return {
    trained: false,
    error: error?.message || 'Status unavailable',
    versions: previous?.versions || cached?.versions || [],
    dataset: previous?.dataset || cached?.dataset || null,
  };
}
