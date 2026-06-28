import { useCallback, useEffect, useRef, useState } from 'react';
import { useStore } from '../store/useStore';
import { invokeHttpAction, sendAction } from '../api/transport';
import { Action } from '../api/protocol';

const LOAD_TIMEOUT_MS = 20_000;
const INVALIDATE_DEBOUNCE_MS = 650;

/**
 * Fetch portfolio analytics — prefers HTTP (reliable) with WS fallback.
 * Pass `invalidateKey` to debounce-refresh when live portfolio data changes.
 */
export function useAnalytics(report = 'dashboard', params = {}, { enabled = true, invalidateKey } = {}) {
  const analyticsReport = useStore((s) => s.analyticsReport);
  const loading = useStore((s) => s.analyticsLoading);
  const setAnalyticsLoading = useStore((s) => s.setAnalyticsLoading);
  const [error, setError] = useState(null);
  const paramsKey = JSON.stringify(params);
  const prevInvalidateKey = useRef(invalidateKey);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    setError(null);
    setAnalyticsLoading(true);
    const payload = { report, ...params };

    try {
      await invokeHttpAction(Action.ANALYTICS_GET, payload, { timeoutMs: 30_000 });
      return;
    } catch (httpErr) {
      const ws = await sendAction(Action.ANALYTICS_GET, payload);
      if (!ws.ok) {
        setAnalyticsLoading(false);
        setError(httpErr?.message || ws.error || 'Analytics request failed');
      }
    }
  }, [report, paramsKey, enabled, setAnalyticsLoading]);

  useEffect(() => {
    if (!enabled) return undefined;
    refresh();
    const timer = setTimeout(() => {
      if (useStore.getState().analyticsLoading) {
        setAnalyticsLoading(false);
        setError((prev) => prev || 'Analytics timed out — restart the backend to load new routes.');
      }
    }, LOAD_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [refresh, enabled, setAnalyticsLoading]);

  useEffect(() => {
    if (!enabled || invalidateKey == null) return undefined;
    if (prevInvalidateKey.current === invalidateKey) return undefined;
    prevInvalidateKey.current = invalidateKey;
    const timer = setTimeout(() => refresh(), INVALIDATE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [invalidateKey, enabled, refresh]);

  useEffect(() => {
    if (!enabled) prevInvalidateKey.current = invalidateKey;
  }, [enabled, invalidateKey]);

  return { data: analyticsReport, loading, error, refresh };
}

export function useJournal(options = {}, { enabled = true } = {}) {
  const entries = useStore((s) => s.journalEntries);
  const optionsKey = JSON.stringify(options);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    try {
      await invokeHttpAction(Action.JOURNAL_LIST, options);
    } catch {
      await sendAction(Action.JOURNAL_LIST, options);
    }
  }, [optionsKey, enabled]);

  const saveEntry = useCallback(async (entry) => {
    try {
      await invokeHttpAction(Action.JOURNAL_UPSERT, { entry });
    } catch {
      await sendAction(Action.JOURNAL_UPSERT, { entry });
    }
  }, []);

  const deleteEntry = useCallback(async (id) => {
    try {
      await invokeHttpAction(Action.JOURNAL_DELETE, { id });
    } catch {
      await sendAction(Action.JOURNAL_DELETE, { id });
    }
  }, []);

  useEffect(() => {
    if (enabled) refresh();
  }, [refresh, enabled]);

  return { entries, refresh, saveEntry, deleteEntry };
}

/** Fetch benchmark overlay series (HTTP-first). */
export async function fetchBenchmarks(period = '3mo', symbols = ['SPY', 'BTC']) {
  try {
    await invokeHttpAction(Action.ANALYTICS_GET, { report: 'benchmarks', period, symbols });
  } catch {
    await sendAction(Action.ANALYTICS_GET, { report: 'benchmarks', period, symbols });
  }
}
