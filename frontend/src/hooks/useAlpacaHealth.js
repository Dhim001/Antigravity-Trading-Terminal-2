import { useEffect, useSyncExternalStore } from 'react';
import { useStore } from '../store/useStore';
import { fetchAlpacaFeedHealth } from '../api/endpoints';

/** Shared poll interval — one /health/alpaca request for all consumers. */
const POLL_MS = 15_000;

let cachedAlpaca = null;
let pollTimer = null;
let subscriberCount = 0;
const listeners = new Set();

function notify() {
  for (const fn of listeners) fn();
}

function pollOnce() {
  fetchAlpacaFeedHealth()
    .then((body) => {
      cachedAlpaca = body?.alpaca ?? null;
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
  return cachedAlpaca;
}

/**
 * Poll `/health/alpaca` for feed ops (lag, poll fallback, seeded count).
 * Mirrors useMassiveHealth — one shared timer for all consumers.
 */
export function useAlpacaHealth() {
  const terminalMode = useStore((s) => s.terminalMode);

  useEffect(() => {
    if (terminalMode !== 'LIVE_ALPACA') {
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
        cachedAlpaca = null;
        notify();
      }
    };
  }, [terminalMode]);

  const health = useSyncExternalStore(
    subscribe,
    getSnapshot,
    getSnapshot,
  );

  if (terminalMode !== 'LIVE_ALPACA') {
    return null;
  }
  return health;
}

/** @internal test helper */
export function resetAlpacaHealthPollForTests() {
  stopSharedPoll();
  subscriberCount = 0;
  cachedAlpaca = null;
  listeners.clear();
}
