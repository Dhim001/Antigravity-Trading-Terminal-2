/**
 * Store-level ML batch tracker (ML Lab Phase 2 follow-up).
 *
 * The Batch Train dialog used to own polling in local React state, so any
 * remount (ErrorBoundary catch, dock detach, HMR/full reload) made the panel
 * vanish mid-run with no way to follow the still-running server batch. This
 * module owns the batch-id + polling instead: it survives component remounts,
 * persists the tracked batch in sessionStorage for full reloads, re-attaches
 * via GET /ml/batch-train/active when nothing was persisted, and keeps
 * polling through transient network errors (the backend self-heals dead
 * runners on each status poll — giving up early would strand the UI).
 *
 * Follows the mlTrainingSession external-store pattern (subscribe/getSnapshot
 * for useSyncExternalStore). Pure + dependency-injected fetch/sleep for node
 * tests.
 */

import {
  deriveServerProgress,
  isServerBatchTerminal,
  summarizeServerBatch,
} from '@/components/ml-lab/batchTrainServerRunner';
import { isTransientMlPollError } from '@/lib/mlJobTimeouts';
import {
  fetchActiveMlBatch,
  fetchMlBatch,
  pollMlJob,
} from '@/lib/mlLabApi';

export const ML_BATCH_TRACKER_STORAGE_KEY = 'mlBatchTracker.v1';
export const ML_BATCH_TRACKER_POLL_INTERVAL_MS = 2500;

const listeners = new Set();

let state = {
  batchId: null,
  symbol: null,
  /** { queue, configSnapshot, startedAt, autoValidate } frozen at submit. */
  meta: null,
  /** Last polled batch payload (includes items + stalled flag). */
  batch: null,
  /** True while the batch is non-terminal and the poll loop runs. */
  active: false,
  /** summarizeServerBatch payload once terminal; { status: 'lost' } when the
   *  batch disappears server-side (e.g. backend DB reset). */
  terminal: null,
  /** Consecutive transient poll failures — surfaced as "reconnecting". */
  pollErrors: 0,
  /** Running item's job id + live progress (per-item % inside the batch). */
  activeJobId: null,
  activeJobProgress: null,
  trackingSince: null,
};

// Incremented on every start/stop so a stale loop iteration exits instead of
// clobbering newer state (double-start, stop-while-awaiting, etc.).
let loopToken = 0;

function emit() {
  listeners.forEach((fn) => {
    try {
      fn();
    } catch {
      /* ignore subscriber errors */
    }
  });
}

function patch(partial) {
  state = { ...state, ...partial };
  emit();
  return state;
}

export function getMlBatchTracker() {
  return state;
}

export function subscribeMlBatchTracker(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getStorage() {
  if (storageOverride) return storageOverride;
  try {
    return typeof sessionStorage !== 'undefined' ? sessionStorage : null;
  } catch {
    return null;
  }
}

// Test hook — node-env tests inject an in-memory sessionStorage stand-in.
let storageOverride = null;
export function setMlBatchTrackerStorageForTests(store) {
  storageOverride = store || null;
}

function persistTracking() {
  const store = getStorage();
  if (!store) return;
  try {
    if (!state.batchId || !state.active) {
      store.removeItem(ML_BATCH_TRACKER_STORAGE_KEY);
      return;
    }
    store.setItem(ML_BATCH_TRACKER_STORAGE_KEY, JSON.stringify({
      batchId: state.batchId,
      symbol: state.symbol,
      meta: state.meta,
      trackingSince: state.trackingSince,
    }));
  } catch {
    /* storage full / blocked — tracking still works in-memory */
  }
}

function readPersistedTracking() {
  const store = getStorage();
  if (!store) return null;
  try {
    const raw = store.getItem(ML_BATCH_TRACKER_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && parsed.batchId ? parsed : null;
  } catch {
    return null;
  }
}

function clearPersistedTracking() {
  const store = getStorage();
  if (!store) return;
  try {
    store.removeItem(ML_BATCH_TRACKER_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

const defaultSleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/** Running item's job id changes as the batch advances — track the current one. */
function runningItemJobId(batch) {
  const items = Array.isArray(batch?.items) ? batch.items : [];
  const running = items.find((it) => it?.status === 'running');
  return running?.job_id || null;
}

async function pollLoop(token, deps) {
  const { fetchBatch, pollJob, sleep, pollIntervalMs } = deps;
  for (;;) {
    if (token !== loopToken || !state.active || !state.batchId) return;
    let batch = null;
    try {
      batch = await fetchBatch(state.batchId);
      if (batch?.ok === false) throw new Error(batch?.error || 'Batch status unavailable');
    } catch (err) {
      if (token !== loopToken || !state.active) return;
      if (isTransientMlPollError(err)) {
        // Never abandon: the backend may be restarting; the status endpoint
        // respawns dead runners, so the next successful poll re-syncs us.
        patch({ pollErrors: state.pollErrors + 1 });
        await sleep(pollIntervalMs);
        continue;
      }
      // Non-transient (e.g. batch 404 after a DB reset) — stop with a clear
      // terminal marker instead of spinning forever.
      clearPersistedTracking();
      patch({
        active: false,
        terminal: {
          status: 'lost',
          error: err?.message || 'batch unavailable',
          batchId: state.batchId,
          server: true,
        },
        activeJobId: null,
        activeJobProgress: null,
      });
      return;
    }
    if (token !== loopToken || !state.active) return;

    const jobId = runningItemJobId(batch);
    patch({
      batch,
      pollErrors: 0,
      activeJobId: jobId,
      ...(jobId !== state.activeJobId ? { activeJobProgress: null } : {}),
    });

    // Best-effort per-item live progress (the batch payload only carries
    // item-level status; the running item's job has the % complete).
    if (jobId && typeof pollJob === 'function') {
      try {
        const body = await pollJob(jobId);
        if (token !== loopToken || !state.active) return;
        const job = body?.job;
        if (job && state.activeJobId === jobId) {
          patch({
            activeJobProgress: {
              ...(job.progress && typeof job.progress === 'object' ? job.progress : {}),
              status: job.status,
            },
          });
        }
      } catch {
        /* progress is best-effort — the batch poll is the source of truth */
      }
    }

    if (isServerBatchTerminal(batch)) {
      clearPersistedTracking();
      patch({
        active: false,
        terminal: summarizeServerBatch(batch),
        activeJobId: null,
        activeJobProgress: null,
      });
      return;
    }
    await sleep(pollIntervalMs);
  }
}

/**
 * Track a server batch until terminal. Re-starting with the same batchId is a
 * no-op; a different batchId replaces the current tracking.
 */
export function startMlBatchTracking({
  batchId,
  symbol,
  meta = null,
  fetchBatch = fetchMlBatch,
  pollJob = pollMlJob,
  sleep = defaultSleep,
  pollIntervalMs = ML_BATCH_TRACKER_POLL_INTERVAL_MS,
} = {}) {
  const id = String(batchId || '').trim();
  if (!id) return getMlBatchTracker();
  if (state.batchId === id && state.active) return getMlBatchTracker();
  loopToken += 1;
  patch({
    batchId: id,
    symbol: symbol ? String(symbol).toUpperCase() : null,
    meta,
    batch: null,
    active: true,
    terminal: null,
    pollErrors: 0,
    activeJobId: null,
    activeJobProgress: null,
    trackingSince: Date.now(),
  });
  persistTracking();
  pollLoop(loopToken, { fetchBatch, pollJob, sleep, pollIntervalMs });
  return getMlBatchTracker();
}

/** Stop tracking entirely (user dismissed an active batch from the UI). */
export function stopMlBatchTracking() {
  loopToken += 1;
  clearPersistedTracking();
  patch({
    batchId: null,
    symbol: null,
    meta: null,
    batch: null,
    active: false,
    terminal: null,
    pollErrors: 0,
    activeJobId: null,
    activeJobProgress: null,
    trackingSince: null,
  });
}

/** Clear a terminal/lost summary (strip dismiss) without touching a live run. */
export function dismissMlBatchTerminal() {
  if (state.active) return;
  loopToken += 1;
  clearPersistedTracking();
  patch({
    batchId: null,
    symbol: null,
    meta: null,
    batch: null,
    terminal: null,
    pollErrors: 0,
    activeJobId: null,
    activeJobProgress: null,
    trackingSince: null,
  });
}

/**
 * Re-attach after a remount/reload. No-op while tracking is live. Order:
 * persisted sessionStorage entry first, then the server's latest non-terminal
 * batch for the symbol (covers "reloaded before ever persisting").
 */
export async function rehydrateMlBatchTracker({
  symbol,
  fetchActive = fetchActiveMlBatch,
  ...deps
} = {}) {
  if (state.active) return getMlBatchTracker();
  const saved = readPersistedTracking();
  if (saved?.batchId) {
    return startMlBatchTracking({
      batchId: saved.batchId,
      symbol: saved.symbol || symbol,
      meta: saved.meta || null,
      ...deps,
    });
  }
  if (symbol && typeof fetchActive === 'function') {
    try {
      const body = await fetchActive(symbol);
      const batch = body?.batch;
      if (batch?.batch_id && !isServerBatchTerminal(batch)) {
        return startMlBatchTracking({
          batchId: batch.batch_id,
          symbol: batch.symbol || symbol,
          meta: null,
          ...deps,
        });
      }
    } catch {
      /* offline / older backend — nothing to re-attach to */
    }
  }
  return getMlBatchTracker();
}

/** Progress line for strips/dialogs: deriveServerProgress over the last poll. */
export function mlBatchTrackerProgress(trackerState = state) {
  return deriveServerProgress(trackerState?.batch);
}

/** Test hook — reset module state between node test runs. */
export function resetMlBatchTrackerForTests() {
  stopMlBatchTracking();
  listeners.clear();
  storageOverride = null;
}
