/**
 * Survives Model Training panel unmounts (tab switches, flexlayout remounts).
 * In-flight train/validate HTTP work continues; UI rehydrates from this session.
 */
import { isAbortError } from '@/api/client';
import { useResearchStore } from '@/store/useResearchStore';

const statusCache = new Map();
const listeners = new Set();
const statusCacheListeners = new Set();
/** Survives invalidate briefly so late untrained fetches cannot undo Fresh. */
const trainRaceGuards = new Map();

// MEMORY_CENTRIC_REVIEW #40 — bound the module-level status cache: LRU cap +
// idle TTL (no timers; expiry is checked on access). Entries are stored as
// { body, t } wrappers; the public getters return the body unchanged.
// Cap must cover Lab inventory (7 strategies) × several symbols/TFs plus Algo
// template/bot badges — 12 was small enough that trained rows were evicted and
// the UI looked "untrained" until a refetch completed (or forever on abort).
export const STATUS_CACHE_MAX = 64;
const STATUS_CACHE_TTL_MS = 30 * 60 * 1000;
/** How long post-train to reject trained=false cache writes (ms). */
export const TRAIN_RACE_GUARD_MS = 45_000;

function emitStatusCache() {
  statusCacheListeners.forEach((fn) => {
    try {
      fn();
    } catch {
      /* ignore subscriber errors */
    }
  });
}

function statusCacheTrim() {
  let trimmed = false;
  while (statusCache.size > STATUS_CACHE_MAX) {
    const oldest = statusCache.keys().next().value;
    statusCache.delete(oldest);
    trimmed = true;
  }
  if (trimmed) emitStatusCache();
}

/** Mirror backend normalize_model_timeframe for cache keys (tick → 1m). */
export function normalizeStatusTimeframe(timeframe = '1m') {
  const tf = String(timeframe || '1m').trim().toLowerCase();
  if (!tf || tf === 'tick') return '1m';
  return tf;
}

let session = {
  strategy: null,
  symbol: null,
  training: false,
  validating: false,
  tuning: false,
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
  const tf = normalizeStatusTimeframe(timeframe);
  return `${String(symbol || '').toUpperCase()}|${String(strategy || '').toUpperCase()}|${tf}`;
}

export function subscribeModelStatusCache(listener) {
  statusCacheListeners.add(listener);
  return () => statusCacheListeners.delete(listener);
}

export function getCachedModelStatus(symbol, strategy, timeframe = '1m') {
  const key = statusCacheKey(symbol, strategy, timeframe);
  const entry = statusCache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.t > STATUS_CACHE_TTL_MS) {
    statusCache.delete(key);
    // Defer emit — calling subscribers during React getSnapshot/render is unsafe.
    queueMicrotask(() => emitStatusCache());
    return null;
  }
  // LRU touch — most-recently-read keys survive the cap.
  statusCache.delete(key);
  statusCache.set(key, entry);
  return entry.body;
}

function armTrainRaceGuard(key) {
  trainRaceGuards.set(key, Date.now() + TRAIN_RACE_GUARD_MS);
}

function trainRaceGuardActive(key) {
  const until = trainRaceGuards.get(key) || 0;
  if (!until) return false;
  if (Date.now() >= until) {
    trainRaceGuards.delete(key);
    return false;
  }
  return true;
}

/**
 * Drop cached status so the next fetch can write a fresh body (including
 * untrained). Clears the post-train race guard — use only for intentional
 * untrained (delete / force clear). After a successful train prefer
 * {@link markModelFreshAfterTrain} so late untrained fetches cannot clobber Fresh.
 */
export function invalidateModelStatusCache(symbol, strategy, timeframe = '1m') {
  if (!symbol || !strategy) return;
  const key = statusCacheKey(symbol, strategy, timeframe);
  trainRaceGuards.delete(key);
  if (!statusCache.has(key)) {
    emitStatusCache();
    return;
  }
  statusCache.delete(key);
  emitStatusCache();
}

/**
 * Post-train: clear cached body (badges refetch) but keep a short race guard so
 * in-flight pre-train `trained=false` responses cannot undo Fresh.
 */
export function markModelFreshAfterTrain(symbol, strategy, timeframe = '1m', seed = null) {
  if (!symbol || !strategy) return;
  const tf = normalizeStatusTimeframe(
    (seed && seed.timeframe) || timeframe || '1m',
  );
  const key = statusCacheKey(symbol, strategy, tf);
  armTrainRaceGuard(key);
  if (seed && typeof seed === 'object') {
    setCachedModelStatus(symbol, strategy, { ...seed, trained: true }, tf);
    return;
  }
  if (statusCache.has(key)) statusCache.delete(key);
  emitStatusCache();
}

export function setCachedModelStatus(symbol, strategy, body, timeframe = '1m') {
  if (!symbol || !strategy || !body || typeof body !== 'object') return;
  const tf = normalizeStatusTimeframe(body.timeframe || timeframe || '1m');
  const key = statusCacheKey(symbol, strategy, tf);
  const existing = getCachedModelStatus(symbol, strategy, tf);
  // Don't cache hard failures as the only truth — keep last good if present.
  if (body.error && !body.trained && existing?.trained) {
    return;
  }
  // Race guard: an in-flight pre-train status fetch must not clobber a post-train
  // trained=true with trained=false. Survives markModelFreshAfterTrain invalidate
  // window; use invalidateModelStatusCache to intentionally allow untrained.
  if (!body.trained && (existing?.trained || trainRaceGuardActive(key))) {
    return;
  }
  // Prefer the newer artifact when two trained payloads race.
  if (existing?.trained && body.trained && existing.trained_at && body.trained_at) {
    const prevTs = Date.parse(existing.trained_at);
    const nextTs = Date.parse(body.trained_at);
    if (Number.isFinite(prevTs) && Number.isFinite(nextTs) && nextTs < prevTs) {
      return;
    }
  }
  if (body.trained) armTrainRaceGuard(key);
  statusCache.delete(key);
  statusCache.set(key, { body, t: Date.now() });
  statusCacheTrim();
  emitStatusCache();
}

export function beginMlJob({ kind, strategy, symbol, jobProgress, jobId = null }) {
  const jobToken = session.jobToken + 1;
  const kindN = String(kind || 'train').toLowerCase();
  return patch({
    jobToken,
    strategy,
    symbol,
    training: kindN === 'train',
    validating: kindN === 'validate',
    tuning: kindN === 'hyperparam_sweep' || kindN === 'tune' || kindN === 'autotune',
    jobProgress: jobProgress ? { ...jobProgress, token: jobToken, active: true } : null,
    lastError: null,
    jobId: jobId || null,
    serverProgress: null,
    pollLog: [],
    ...(kindN === 'validate' ? { validation: null } : {}),
  });
}

export function finishMlJob(token, { validation = undefined, error = null } = {}) {
  if (token != null && token !== session.jobToken) return session;
  return patch({
    training: false,
    validating: false,
    tuning: false,
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
  if (['done', 'error', 'cancelled'].includes(status)) {
    if (status === 'done' && (wasTraining || kind === 'train') && strat && sym) {
      invalidateMatchingMlBacktests(strat, sym);
      const tf = normalizeStatusTimeframe(
        data.timeframe || session.jobProgress?.timeframe || '1m',
      );
      // Keep post-train race guard — bare invalidate would let late untrained land.
      markModelFreshAfterTrain(sym, strat, tf);
    }
    // Keep jobId until the Lab panel finishes its own poll cleanup; only clear
    // active flags so remounts don't look "busy forever".
    if (status !== 'done' || kind === 'hyperparam_sweep') {
      patch({
        training: false,
        validating: false,
        tuning: status === 'done' ? false : session.tuning,
      });
    }
  }
  return next;
}

/**
 * Rehydrate Lab session from bootstrap /api/v1/session active ML jobs.
 * Picks the newest queued/running job so tab refresh can reattach polling.
 */
export function resumeActiveMlJobs(jobs) {
  const list = Array.isArray(jobs) ? jobs : [];
  const active = list.filter((j) => ['queued', 'running'].includes(String(j?.status || '').toLowerCase()));
  if (!active.length) return session;
  // Prefer an already-tracked job if still active.
  if (session.jobId && active.some((j) => j.job_id === session.jobId || j.id === session.jobId)) {
    return session;
  }
  const job = active[0];
  const kind = String(job.kind || 'train').toLowerCase();
  const progress = job.progress && typeof job.progress === 'object'
    ? {
      active: true,
      kind,
      startedAt: Date.now(),
      label: `Resuming ${kind} · ${job.strategy || ''}`.trim(),
      phases: [],
    }
    : null;
  beginMlJob({
    kind,
    strategy: job.strategy,
    symbol: job.symbol,
    jobProgress: progress,
    jobId: job.job_id || job.id,
  });
  if (job.progress) setMlServerProgress({ ...job.progress, status: job.status });
  return session;
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
  const tf = normalizeStatusTimeframe((body && body.timeframe) || timeframe || '1m');
  const key = statusCacheKey(symbol, strategy, tf);
  if (body && typeof body === 'object') {
    setCachedModelStatus(symbol, strategy, body, tf);
    const cachedAfter = getCachedModelStatus(symbol, strategy, tf);
    // If race guard rejected an untrained write, never surface trained=false to UI.
    if (!body.trained && trainRaceGuardActive(key)) {
      if (cachedAfter?.trained) return { ...cachedAfter, timeframe: cachedAfter.timeframe || tf };
      if (previous?.trained && normalizeStatusTimeframe(previous.timeframe || '1m') === tf) {
        return { ...previous, timeframe: tf };
      }
      return null;
    }
    return cachedAfter
      ? { ...cachedAfter, timeframe: cachedAfter.timeframe || tf }
      : { ...body, timeframe: body.timeframe || tf };
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
  if (previous?.trained && normalizeStatusTimeframe(previous.timeframe || '1m') === tf) {
    return {
      ...previous,
      stale: true,
      fetch_error: error?.message || 'Status temporarily unavailable',
    };
  }
  // Post-train refetch window: prefer "checking" over false Untrained.
  if (trainRaceGuardActive(key)) {
    return null;
  }
  return {
    trained: false,
    timeframe: tf,
    error: error?.message || 'Status unavailable',
    versions: previous?.versions || cached?.versions || [],
    dataset: previous?.dataset || cached?.dataset || null,
  };
}
