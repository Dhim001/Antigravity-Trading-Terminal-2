/**
 * Hot-path candle revision counters — outside Zustand to avoid object spreads on every tick.
 */

import { useSyncExternalStore } from 'react';

const MAX_KEYS = 30;
const liveRevisions = new Map();
const historyRevisions = new Map();
const listeners = new Set();
let notifyScheduled = false;

function pruneMap(map) {
  if (map.size <= MAX_KEYS) return;
  const excess = map.size - MAX_KEYS;
  const keys = [...map.keys()];
  for (let i = 0; i < excess; i++) {
    map.delete(keys[i]);
  }
}

function scheduleNotify() {
  if (notifyScheduled) return;
  notifyScheduled = true;
  queueMicrotask(() => {
    notifyScheduled = false;
    for (const fn of listeners) {
      try {
        fn();
      } catch (_) {
        /* ignore */
      }
    }
  });
}

/** @param {string} key */
export function bumpLiveRevision(key) {
  if (!key) return;
  liveRevisions.set(key, (liveRevisions.get(key) || 0) + 1);
  pruneMap(liveRevisions);
  scheduleNotify();
}

/** @param {string} key */
export function bumpHistoryRevision(key) {
  if (!key) return;
  historyRevisions.set(key, (historyRevisions.get(key) || 0) + 1);
  pruneMap(historyRevisions);
  scheduleNotify();
}

/** @param {string} key */
export function getLiveRevision(key) {
  return liveRevisions.get(key) || 0;
}

/** @param {string} key */
export function getHistoryRevision(key) {
  return historyRevisions.get(key) || 0;
}

/** Drop revision keys when candle buffers are LRU-evicted. */
export function clearRevisionsForKey(key) {
  if (!key) return;
  let changed = false;
  if (liveRevisions.delete(key)) changed = true;
  if (historyRevisions.delete(key)) changed = true;
  if (changed) scheduleNotify();
}

/** Hydrate from session snapshot or HMR dispose payload. */
export function seedRevisions(live = {}, history = {}) {
  liveRevisions.clear();
  historyRevisions.clear();
  for (const [k, v] of Object.entries(live)) {
    if (v) liveRevisions.set(k, Number(v) || 1);
  }
  for (const [k, v] of Object.entries(history)) {
    if (v) historyRevisions.set(k, Number(v) || 1);
  }
  scheduleNotify();
}

/** For HMR / debugging. */
export function snapshotRevisions() {
  return {
    candleRevision: Object.fromEntries(liveRevisions),
    candleHistoryRevision: Object.fromEntries(historyRevisions),
  };
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** @param {string} key */
export function useLiveCandleRevision(key) {
  return useSyncExternalStore(
    subscribe,
    () => getLiveRevision(key),
    () => 0,
  );
}

/** @param {string} key */
export function useHistoryCandleRevision(key) {
  return useSyncExternalStore(
    subscribe,
    () => getHistoryRevision(key),
    () => 0,
  );
}

/**
 * Subscribe to live revision changes for one or two keys (chart + HT buffer).
 * @param {string} keyA
 * @param {string} [keyB]
 * @param {() => void} callback
 */
export function subscribeLiveRevisions(keyA, keyB, callback) {
  let last = `${getLiveRevision(keyA)}:${getLiveRevision(keyB || '')}`;
  const fn = () => {
    const next = `${getLiveRevision(keyA)}:${getLiveRevision(keyB || '')}`;
    if (next === last) return;
    last = next;
    callback();
  };
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/**
 * Subscribe to history revision changes (full chart rebuild).
 * @param {string} key
 * @param {() => void} callback
 */
export function subscribeHistoryRevision(key, callback) {
  let last = getHistoryRevision(key);
  const fn = () => {
    const next = getHistoryRevision(key);
    if (next === last) return;
    last = next;
    callback();
  };
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** @internal */
export function resetCandleRevisionsForTests() {
  liveRevisions.clear();
  historyRevisions.clear();
  listeners.clear();
  notifyScheduled = false;
}
