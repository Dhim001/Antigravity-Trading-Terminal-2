/**
 * Async wrappers that run backtestSlim on a dedicated worker (MEMORY #24).
 * Falls back to sync on the main thread if Worker is unavailable.
 */
import {
  trimBacktestPayload,
  slimBacktestForDock,
  buildBacktestOverlay,
} from './backtestSlim.js';

let worker = null;
let seq = 0;
/** @type {Map<number, { resolve: (v: unknown) => void, reject: (e: Error) => void }>} */
const pending = new Map();

function getWorker() {
  if (typeof Worker === 'undefined') return null;
  if (worker) return worker;
  try {
    worker = new Worker(
      new URL('../workers/backtestSlim.worker.js', import.meta.url),
      { type: 'module' },
    );
    worker.onmessage = (event) => {
      const { id, ok, result, error } = event.data || {};
      const entry = pending.get(id);
      if (!entry) return;
      pending.delete(id);
      if (ok) entry.resolve(result);
      else entry.reject(new Error(error || 'backtestSlim worker failed'));
    };
    worker.onerror = () => {
      // Next call will recreate; pending callers fall through on post failure.
      worker = null;
    };
    return worker;
  } catch {
    worker = null;
    return null;
  }
}

function runOp(op, payload, syncFn) {
  const w = getWorker();
  if (!w) return Promise.resolve(syncFn(payload));

  return new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    try {
      w.postMessage({ id, op, payload });
    } catch (err) {
      pending.delete(id);
      try {
        resolve(syncFn(payload));
      } catch (syncErr) {
        reject(syncErr instanceof Error ? syncErr : new Error(String(syncErr)));
      }
    }
  });
}

export function trimBacktestPayloadAsync(results) {
  return runOp('trim', results, trimBacktestPayload);
}

export function slimBacktestForDockAsync(results) {
  return runOp('slim', results, slimBacktestForDock);
}

export function buildBacktestOverlayAsync(results) {
  return runOp('overlay', results, buildBacktestOverlay);
}

/** @internal */
export function resetBacktestSlimWorkerForTests() {
  if (worker) {
    try {
      worker.terminate();
    } catch {
      /* ignore */
    }
  }
  worker = null;
  pending.clear();
  seq = 0;
}
