import { lazy } from 'react';

/** @type {Map<string, () => Promise<unknown>>} */
const IMPORT_FNS = new Map();
/** @type {Map<string, Promise<unknown>>} */
const INFLIGHT = new Map();

/**
 * React.lazy wrapper that retries once on Vite chunk/HMR fetch failures.
 * After a recycle, the open renderer can hold a stale module graph; a single
 * reload usually restores dynamic imports.
 *
 * Import fns are registered by ``label`` so dock tabs can prefetch on idle /
 * hover before the user clicks (avoids blank Suspense on first switch).
 */
export function lazyImport(importFn, label = 'panel') {
  const key = String(label || 'panel');
  IMPORT_FNS.set(key, importFn);

  return lazy(async () => {
    try {
      const mod = await importFn();
      if (typeof sessionStorage !== 'undefined') {
        sessionStorage.removeItem(`lazy-import-retry:${key}`);
      }
      return mod;
    } catch (err) {
      const message = String(err?.message || err || '');
      const isChunkLoad = /fetch dynamically imported module|Loading chunk|Failed to fetch/i.test(message);
      if (!isChunkLoad) throw err;

      const retryKey = `lazy-import-retry:${key}`;
      if (typeof sessionStorage !== 'undefined' && !sessionStorage.getItem(retryKey)) {
        sessionStorage.setItem(retryKey, '1');
        window.location.reload();
        return new Promise(() => {});
      }
      if (typeof sessionStorage !== 'undefined') {
        sessionStorage.removeItem(retryKey);
      }
      throw err;
    }
  });
}

/**
 * Start loading a registered lazy panel chunk (no-op if unknown / already warm).
 * @param {string} label
 * @returns {Promise<unknown>|null}
 */
export function prefetchLazyImport(label) {
  const key = String(label || '');
  if (!key || !IMPORT_FNS.has(key)) return null;
  if (INFLIGHT.has(key)) return INFLIGHT.get(key);
  const run = Promise.resolve()
    .then(() => IMPORT_FNS.get(key)())
    .catch(() => {
      INFLIGHT.delete(key);
    });
  INFLIGHT.set(key, run);
  return run;
}

/** Dock / trading panel labels registered via lazyImport in WorkspaceGrid + flex panels. */
export const DOCK_PREFETCH_LABELS = Object.freeze([
  'positions',
  'orders',
  'balances',
  'history',
  'bots',
  'reconcile',
  'scanner',
  'analyst',
  'copilot',
  'ml-training',
  'algo',
  'ticks',
  'equity',
  'order-entry',
  'order-book',
  'depth-chart',
  'footprint',
]);

/**
 * Warm dock panel chunks during idle time so the first tab click is instant.
 * @param {string[]} [labels]
 */
export function prefetchDockPanels(labels = DOCK_PREFETCH_LABELS) {
  const run = () => {
    for (const label of labels) prefetchLazyImport(label);
  };
  // Prefer idle callback when available; always schedule a timeout fallback so
  // warm-up cannot be skipped (some test / Electron shells omit ric).
  let idleScheduled = false;
  try {
    if (typeof window !== 'undefined' && typeof window.requestIdleCallback === 'function') {
      window.requestIdleCallback(run, { timeout: 2500 });
      idleScheduled = true;
    }
  } catch {
    idleScheduled = false;
  }
  if (!idleScheduled) {
    setTimeout(run, 400);
  } else {
    // Safety net if idle never fires under load.
    setTimeout(run, 3000);
  }
}
