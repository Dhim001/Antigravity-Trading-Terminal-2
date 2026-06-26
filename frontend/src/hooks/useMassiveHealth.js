import { useEffect, useState } from 'react';
import { useStore } from '../store/useStore';
import { fetchHealth } from '../api/endpoints';

/** Poll `/health` for Massive feed ops (per-market lag, NBBO list, feed plan). */
export function useMassiveHealth(intervalMs = 20_000) {
  const terminalMode = useStore((s) => s.terminalMode);
  const [health, setHealth] = useState(null);

  useEffect(() => {
    if (terminalMode !== 'LIVE_MASSIVE') {
      setHealth(null);
      return undefined;
    }
    let cancelled = false;
    const poll = () => {
      fetchHealth(null)
        .then((body) => {
          if (!cancelled) setHealth(body?.massive ?? null);
        })
        .catch(() => {});
    };
    poll();
    const id = setInterval(poll, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [terminalMode, intervalMs]);

  return health;
}
