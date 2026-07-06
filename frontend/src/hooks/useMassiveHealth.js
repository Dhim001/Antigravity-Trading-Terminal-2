import { useEffect, useState, useSyncExternalStore } from 'react';
import { useStore } from '../store/useStore';
import { fetchMassiveFeedHealth } from '../api/endpoints';

/** Shared poll interval — one /health request for all consumers. */
const POLL_MS = 15_000;

let cachedMassive = null;
let pollTimer = null;
let subscriberCount = 0;
const listeners = new Set();

function notify() {
  for (const fn of listeners) fn();
}

function pollOnce() {
  fetchMassiveFeedHealth()
    .then((body) => {
      cachedMassive = body?.massive ?? null;
      notify();
    })
    .catch(() => {});
}

function startSharedPoll() {
  if (pollTimer != null) return;
  pollOnce();
  pollTimer = setInterval(pollOnce, POLL_MS);
}

function stopSharedPoll() {
  if (pollTimer != null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return cachedMassive;
}

/**
 * Poll `/health` for Massive feed ops (per-market lag, NBBO list, feed plan).
 * All hook instances share one timer and cached payload.
 */
export function useMassiveHealth() {
  const terminalMode = useStore((s) => s.terminalMode);

  useEffect(() => {
    if (terminalMode !== 'LIVE_MASSIVE') {
      return undefined;
    }
    subscriberCount += 1;
    if (subscriberCount === 1) {
      startSharedPoll();
    }
    return () => {
      subscriberCount = Math.max(0, subscriberCount - 1);
      if (subscriberCount === 0) {
        stopSharedPoll();
        cachedMassive = null;
        notify();
      }
    };
  }, [terminalMode]);

  const health = useSyncExternalStore(
    subscribe,
    getSnapshot,
    getSnapshot,
  );

  if (terminalMode !== 'LIVE_MASSIVE') {
    return null;
  }
  return health;
}

/** @internal test helper */
export function resetMassiveHealthPollForTests() {
  stopSharedPoll();
  subscriberCount = 0;
  cachedMassive = null;
  listeners.clear();
}
