import { useEffect, useState } from 'react';
import { useStore } from '../store/useStore';
import { fetchHealthLive } from '../api/endpoints';

/**
 * LIVE_IB only — explains delayed/disconnected Gateway feed (sim terminal unaffected).
 */
export default function IbFeedStatusBanner() {
  const terminalMode = useStore((s) => s.terminalMode);
  const symbolsList = useStore((s) => s.symbolsList);
  const [health, setHealth] = useState(null);

  useEffect(() => {
    if (terminalMode !== 'LIVE_IB') return undefined;
    let cancelled = false;
    const poll = () => {
      fetchHealthLive()
        .then((body) => {
          if (!cancelled) setHealth(body);
        })
        .catch(() => {});
    };
    poll();
    const id = setInterval(poll, 12_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [terminalMode]);

  if (terminalMode !== 'LIVE_IB' || !health?.ib) return null;

  const ib = health.ib;
  const lagMin = health.feed_lag_sec != null ? Math.round(health.feed_lag_sec / 60) : null;
  const symbolCount = symbolsList?.length ?? 16;

  if (!ib.connected) {
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--down"
        role="status"
      >
        IB Gateway not connected — check Gateway on port 4002 and restart the IB backend.
      </div>
    );
  }

  if (ib.market_data_delayed || (lagMin != null && lagMin >= 10)) {
    const lagText = lagMin != null && lagMin > 0 ? ` (~${lagMin} min behind)` : '';
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--warn"
        role="status"
      >
        IB delayed market data{lagText}. Enable live US equity subscriptions in IB Account Management
        for real-time quotes; until then prices update slowly.
      </div>
    );
  }

  if ((ib.streams_active ?? 0) < symbolCount - 1) {
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--warn"
        role="status"
      >
        IB feed partial — {ib.streams_active ?? 0}/{symbolCount} symbol streams active
        {ib.last_error ? `: ${ib.last_error}` : ''}.
      </div>
    );
  }

  return null;
}
