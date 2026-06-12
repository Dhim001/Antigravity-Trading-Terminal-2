import { useEffect } from 'react';
import { useStore } from '../store/useStore';
import { getStoreActions } from '../api/dispatch';
import {
  fetchAccount,
  fetchBots,
  fetchCandles,
  fetchHealth,
  fetchHistory,
} from '../api/endpoints';

/**
 * Read-only HTTP bootstrap — hydrates store before / while WebSocket connects.
 * Idempotent: duplicate WS pushes simply overwrite the same fields.
 */
export function useBootstrap() {
  const setApiStatus = useStore((s) => s.setApiStatus);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      setApiStatus('loading');
      const storeActions = getStoreActions();
      const symbol = useStore.getState().activeSymbol;

      const results = await Promise.allSettled([
        fetchHealth(storeActions),
        fetchAccount(storeActions),
        fetchHistory(storeActions),
        fetchBots(storeActions),
        fetchCandles(symbol, storeActions),
      ]);

      if (cancelled) return;

      const succeeded = results.filter((r) => r.status === 'fulfilled').length;
      if (succeeded === 0) {
        setApiStatus('error');
        console.warn('[bootstrap] All HTTP snapshot requests failed — waiting for WebSocket.');
        return;
      }

      if (succeeded < results.length) {
        results.forEach((r, i) => {
          if (r.status === 'rejected') {
            const labels = ['health', 'account', 'history', 'bots', 'candles'];
            console.warn(`[bootstrap] ${labels[i]} failed:`, r.reason?.message ?? r.reason);
          }
        });
      }

      setApiStatus('ready');
    })();

    return () => {
      cancelled = true;
    };
  }, [setApiStatus]);
}
