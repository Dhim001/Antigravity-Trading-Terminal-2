/**
 * Offload full backtest payloads — sessionStorage L1 + IndexedDB L2.
 */

import { slimBacktestForDock } from '../lib/backtestSlim';
import { idbSaveBacktest, idbLoadBacktest, idbClearBacktest } from './idbBacktest';

const PROFILE = import.meta.env.VITE_TERMINAL_PROFILE || 'default';
const KEY_PREFIX = `terminal_backtest_full_${PROFILE}:`;
const MAX_SESSION_RUNS = 3;

function sessionKey(runId) {
  return `${KEY_PREFIX}${runId}`;
}

/** Persist full results keyed by run_id (sync L1 + async L2). */
export function saveFullBacktestResults(results) {
  const runId = results?.run_id;
  if (!runId) return;
  saveFullBacktestResultsToSession(runId, results);
  idbSaveBacktest(runId, results).catch(() => {});
}

function saveFullBacktestResultsToSession(runId, results) {
  if (typeof sessionStorage === 'undefined') return;
  try {
    sessionStorage.setItem(sessionKey(runId), JSON.stringify(results));
    pruneSessionRuns(runId);
  } catch (_) {
    /* quota — IDB may still succeed */
  }
}

function pruneSessionRuns(keepRunId) {
  const keys = [];
  for (let i = 0; i < sessionStorage.length; i++) {
    const k = sessionStorage.key(i);
    if (k?.startsWith(KEY_PREFIX) && k !== sessionKey(keepRunId)) {
      keys.push(k);
    }
  }
  while (keys.length >= MAX_SESSION_RUNS) {
    sessionStorage.removeItem(keys.shift());
  }
}

/** Load full results from sessionStorage (sync). */
export function loadFullBacktestResults(runId) {
  if (!runId || typeof sessionStorage === 'undefined') return null;
  try {
    const raw = sessionStorage.getItem(sessionKey(runId));
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

/** Load from sessionStorage, then IndexedDB. */
export async function loadFullBacktestResultsAsync(runId) {
  const cached = loadFullBacktestResults(runId);
  if (cached) return cached;
  const fromIdb = await idbLoadBacktest(runId);
  if (fromIdb) {
    saveFullBacktestResultsToSession(runId, fromIdb);
  }
  return fromIdb;
}

export function clearStoredBacktest(runId) {
  if (!runId) return;
  if (typeof sessionStorage !== 'undefined') {
    try {
      sessionStorage.removeItem(sessionKey(runId));
    } catch (_) {
      /* ignore */
    }
  }
  idbClearBacktest(runId).catch(() => {});
}

/** Replace heavy in-memory tree with dock slim + run_id pointer. */
export function offloadBacktestFromMemory(results) {
  if (!results?.run_id) return results;
  saveFullBacktestResults(results);
  return { ...slimBacktestForDock(results), _offloaded: true };
}

/** Restore full payload when Lab opens (sync — session only). */
export function resolveBacktestForLab(results) {
  if (!results) return null;
  if (results._offloaded && results.run_id) {
    const stored = loadFullBacktestResults(results.run_id);
    if (stored) return stored;
  }
  return results;
}

/** Async restore including IndexedDB. */
export async function resolveBacktestForLabAsync(results) {
  if (!results) return null;
  if (results._offloaded && results.run_id) {
    const stored = await loadFullBacktestResultsAsync(results.run_id);
    if (stored) return stored;
  }
  return results;
}
