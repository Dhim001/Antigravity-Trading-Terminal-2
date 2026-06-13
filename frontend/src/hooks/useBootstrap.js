import { useEffect } from 'react';
import { runBootstrap } from '../api/bootstrap';
import { isHmrReload } from '../services/hmrState';
import { useStore } from '../store/useStore';

/**
 * Read-only HTTP bootstrap — hydrates store before / while WebSocket connects.
 * Skips full reload on Vite HMR when session snapshot + WS are still warm.
 */
export function useBootstrap() {
  useEffect(() => {
    let cancelled = false;

    const hmrWarm =
      isHmrReload()
      && useStore.getState().apiStatus === 'ready'
      && Object.keys(useStore.getState().tickerData).length > 0;

    if (hmrWarm) {
      return () => { cancelled = true; };
    }

    (async () => {
      await runBootstrap();
      if (cancelled) return;
    })();

    return () => {
      cancelled = true;
    };
  }, []);
}
