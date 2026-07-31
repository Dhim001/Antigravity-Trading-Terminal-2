/**
 * Survives Model Training panel unmounts (tab switches, flexlayout remounts).
 * In-flight train/validate HTTP work continues; UI rehydrates from this session.
 */
import { isAbortError } from '@/api/client';
import { useResearchStore } from '@/store/useResearchStore';

const statusCache = new Map();
const listeners = new Set();

// MEMORY_CENTRIC_REVIEW #40 — bound the module-level status cache: LRU cap +
// idle TTL (no timers; expiry is checked on access). Entries are stored as
// { body, t } wrappers; the public getters return the body unchanged.
const STATUS_CACHE_MAX = 12;
const STATUS_CACHE_TTL_MS = 30 * 60 * 1000;

function statusCacheTrim() {
  while (statusCache.size > STATUS_CACHE_MAX) {
    const oldest = statusCache.keys().next().value;
    statusCache.delete(oldest);
  }
}

let session = {
  strategy: null,
  symbol: null,
  training: false,
  validating: false,
  jobProgress: null,
  validation: null,
  lastError: null,
  jobToken: 0,
  // Phase 1 async jobs (additive).
  jobId: null,
  serverProgress: null,
  /** Ring buffer of poll snapshots for optional Lab inspection. */
  pollLog: [],
};

const ML_POLL_LOG_MAX = 250;

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

function nextPollLog(prev, entry) {
  const line = {
    t: typeof entry?.t === 'number' ? entry.t : Date.now(),
    status: entry?.status != null ? String(entry.status) : '',
    pct: entry?.pct != null && Number.isFinite(Number(entry.pct)) ? Number(entry.pct) : null,
    phase: entry?.phase != null ? String(entry.phase) : '',
    detail: entry?.detail != null ? String(entry.detail) : '',
    note: entry?.note != null ? String(entry.note) : '',
  };
  const list = Array.isArray(prev) ? prev : [];
  const last = list[list.length - 1];
  if (
    last
    && last.status === line.status
    && last.pct === line.pct
    && last.phase === line.phase
    && last.detail === line.detail
    && last.note === line.note
  ) {
    // Refresh timestamp on identical snapshot (still one row).
    return [...list.slice(0, -1), { ...last, t: line.t }];
  }
  return [...list, line].slice(-ML_POLL_LOG_MAX);
}

export function getMlTrainingSession() {
  return session;
}

export function subscribeMlTrainingSession(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function statusCacheKey(symbol, strategy, timeframe = '1m') {
  const tf = String(timeframe || '1m').toLowerCase();
  return `${String(symbol || '').toUpperCase()}|${String(strategy || '').toUpperCase()}|${tf}`;
}

export function getCachedModelStatus(symbol, strategy, timeframe = '1m') {
  const key = statusCacheKey(symbol, strategy, timeframe);
  const entry = statusCache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.t > STATUS_CACHE_TTL_MS) {
    statusCache.delete(key);
    return null;
  }
  // LRU touch — most-recently-read keys survive the cap.
  statusCache.delete(key);
  statusCache.set(key, entry);
  return entry.body;
}

export function setCachedModelStatus(symbol, strategy, body, timeframe = '1m') {
  if (!symbol || !strategy || !body || typeof body !== 'object') return;
  const tf = body.timeframe || timeframe || '1m';
  // Don't cache hard failures as the only truth — keep last good if present.
  if (body.error && !body.trained && getCachedModelStatus(symbol, strategy, tf)?.trained) {
    return;
  }
  const key = statusCacheKey(symbol, strategy, tf);
  statusCache.delete(key);
  statusCache.set(key, { body, t: Date.now() });
  statusCacheTrim();
}

export function beginMlJob({ kind, strategy, symbol, jobProgress, jobId = null }) {
  const jobToken = session.jobToken + 1;
  return patch({
    jobToken,
    strategy,
    symbol,
    training: kind === 'train',
    validating: kind === 'validate',
    jobProgress: jobProgress ? { ...jobProgress, token: jobToken, active: true } : null,
    lastError: null,
    jobId: jobId || null,
    serverProgress: null,
    pollLog: [],
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
    jobId: null,
    serverProgress: null,
    ...(validation !== undefined ? { validation } : {}),
  });
}

export function clearMlJobProgress(token) {
  if (token != null && token !== session.jobToken) return;
  if (session.jobProgress) {
    patch({ jobProgress: null, serverProgress: null });
  }
}

export function setMlValidation(validation) {
  return patch({ validation });
}

export function setMlJobId(jobId) {
  return patch({ jobId: jobId || null });
}

export function setMlServerProgress(progress) {
  if (!progress || typeof progress !== 'object') {
    return patch({ serverProgress: null });
  }
  const serverProgress = {
    pct: Number(progress.pct) || 0,
    phase: progress.phase || '',
    detail: progress.detail || '',
    status: progress.status,
    updatedAt: Date.now(),
  };
  return patch({
    serverProgress,
    pollLog: nextPollLog(session.pollLog, {
      status: serverProgress.status,
      pct: serverProgress.pct,
      phase: serverProgress.phase,
      detail: serverProgress.detail,
      note: progress.note || '',
    }),
  });
}

/** Explicit poll-log row (timeouts / notes that are not a progress snapshot). */
export function appendMlPollLog(entry) {
  return patch({ pollLog: nextPollLog(session.pollLog, entry) });
}

export function clearMlPollLog() {
  return patch({ pollLog: [] });
}

/** Apply WS `ml_job_progress` if it matches the active session job. */
export function applyMlJobProgressMessage(data) {
  if (!data || typeof data !== 'object') return session;
  const jobId = data.job_id || data.jobId;
  if (!jobId || !session.jobId || jobId !== session.jobId) return session;
  const status = String(data.status || '').toLowerCase();
  const wasTraining = Boolean(session.training);
  const kind = String(data.kind || '').toLowerCase();
  const strat = session.strategy;
  const sym = session.symbol;
  const next = setMlServerProgress(data);
  if (status === 'done' && (wasTraining || kind === 'train') && strat && sym) {
    invalidateMatchingMlBacktests(strat, sym);
  }
  return next;
}

/** Clear matching Algo/Lab backtest results after train / activate. */
export function invalidateMatchingMlBacktests(strategy, symbol) {
  try {
    useResearchStore.getState().invalidateMlBacktests?.({ strategy, symbol });
  } catch {
    /* ignore */
  }
}

/** Prefer cached status over transient fetch errors / aborts. */
export function resolveModelStatusFetch(symbol, strategy, { body, error, previous, timeframe = '1m' }) {
  const tf = (body && body.timeframe) || timeframe || '1m';
  if (body && typeof body === 'object') {
    setCachedModelStatus(symbol, strategy, body, tf);
    return body;
  }
  if (error && isAbortError(error)) {
    return previous ?? getCachedModelStatus(symbol, strategy, tf);
  }
  const cached = getCachedModelStatus(symbol, strategy, tf);
  if (cached?.trained) {
    return {
      ...cached,
      stale: true,
      fetch_error: error?.message || 'Status temporarily unavailable',
    };
  }
  if (previous?.trained && (previous.timeframe || '1m') === String(tf).toLowerCase()) {
    return {
      ...previous,
      stale: true,
      fetch_error: error?.message || 'Status temporarily unavailable',
    };
  }
  return {
    trained: false,
    timeframe: tf,
    error: error?.message || 'Status unavailable',
    versions: previous?.versions || cached?.versions || [],
    dataset: previous?.dataset || cached?.dataset || null,
  };
}
