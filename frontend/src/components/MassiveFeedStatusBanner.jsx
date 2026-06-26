import { useStore } from '../store/useStore';
import { useMassiveHealth } from '../hooks/useMassiveHealth';
import { massiveFeedPlanLabel } from '../lib/massiveMarket';

/**
 * LIVE_MASSIVE only — explains WS disconnect, REST poll fallback, and partial feeds.
 */
export default function MassiveFeedStatusBanner() {
  const terminalMode = useStore((s) => s.terminalMode);
  const symbolsList = useStore((s) => s.symbolsList);
  const health = useMassiveHealth(12_000);

  if (terminalMode !== 'LIVE_MASSIVE' || !health) return null;

  const m = health;
  const feedPlan = massiveFeedPlanLabel(m);
  const planSuffix = feedPlan === 'Delayed' ? ' (delayed plan)' : '';
  const stocksLagMin = m.stocks_lag_sec != null ? Math.round(m.stocks_lag_sec / 60) : null;
  const cryptoLagMin = m.crypto_lag_sec != null ? Math.round(m.crypto_lag_sec / 60) : null;
  const symbolCount = symbolsList?.length ?? 26;
  const equityCount = m.equity_symbols ?? 16;
  const cryptoCount = m.crypto_symbols ?? 10;

  const inPoll =
    m.poll_fallback || m.stocks_mode === 'poll' || m.crypto_mode === 'poll';
  const wsLive = m.stocks_connected || m.crypto_connected;
  const lastErr = m.last_error || '';

  if (!wsLive && !inPoll) {
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--down"
        role="status"
      >
        Massive feed not connected — check MASSIVE_API_KEY and restart the Massive backend.
        {feedPlan ? ` Plan: ${feedPlan}.` : ''}
        {lastErr ? ` (${lastErr})` : ''}
      </div>
    );
  }

  if (inPoll && !wsLive) {
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--warn"
        role="status"
      >
        Massive WebSocket unavailable — REST poll fallback active{planSuffix}
        {lastErr ? `: ${lastErr}` : ''}. Prices refresh every ~15s; upgrade plan for live WS.
      </div>
    );
  }

  if (inPoll && wsLive) {
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--warn"
        role="status"
      >
        Massive partial — some markets on REST poll
        {m.stocks_mode === 'poll' ? ' (stocks)' : ''}
        {m.crypto_mode === 'poll' ? ' (crypto)' : ''}.
        {lastErr ? ` ${lastErr}` : ''}
      </div>
    );
  }

  if (stocksLagMin != null && stocksLagMin >= 10) {
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--warn"
        role="status"
      >
        Massive stocks lag ~{stocksLagMin} min — US equities may be stale{planSuffix}.
      </div>
    );
  }

  if (cryptoLagMin != null && cryptoLagMin >= 5) {
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--warn"
        role="status"
      >
        Massive crypto lag ~{cryptoLagMin} min — check WS connection or poll fallback.
      </div>
    );
  }

  if ((m.seeded_symbols ?? 0) < symbolCount && (m.seeded_symbols ?? 0) > 0) {
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--warn"
        role="status"
      >
        Massive seeding history — {m.seeded_symbols}/{symbolCount} symbols ready.
      </div>
    );
  }

  const stocksPartial = m.stocks_mode === 'websocket' && !m.stocks_connected && equityCount > 0;
  const cryptoPartial = m.crypto_mode === 'websocket' && !m.crypto_connected && cryptoCount > 0;
  if (stocksPartial || cryptoPartial) {
    const parts = [];
    if (stocksPartial) parts.push('stocks');
    if (cryptoPartial) parts.push('crypto');
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--warn"
        role="status"
      >
        Massive feed partial — {parts.join(' + ')} disconnected
        {lastErr ? `: ${lastErr}` : ''}.
      </div>
    );
  }

  if ((m.subscriptions ?? 0) < symbolCount && wsLive) {
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--warn"
        role="status"
      >
        Massive subscriptions warming up — {m.subscriptions ?? 0} channels for {symbolCount} symbols.
      </div>
    );
  }

  return null;
}
