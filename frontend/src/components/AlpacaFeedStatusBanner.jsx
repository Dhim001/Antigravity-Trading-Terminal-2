import { useStore } from '../store/useStore';
import { useAlpacaHealth } from '../hooks/useAlpacaHealth';
import { usEquitySessionOpen } from '../lib/massiveMarket';

/**
 * LIVE_ALPACA only — explains WS disconnect, REST poll fallback, lag, and seeding.
 * Mirrors MassiveFeedStatusBanner thresholds/behavior.
 */
export default function AlpacaFeedStatusBanner() {
  const terminalMode = useStore((s) => s.terminalMode);
  const symbolsList = useStore((s) => s.symbolsList);
  const health = useAlpacaHealth();

  if (terminalMode !== 'LIVE_ALPACA' || !health) return null;

  const m = health;
  const stocksLagMin = m.stocks_lag_sec != null ? Math.round(m.stocks_lag_sec / 60) : null;
  const cryptoLagMin = m.crypto_lag_sec != null ? Math.round(m.crypto_lag_sec / 60) : null;
  const symbolCount = symbolsList?.length ?? 26;
  const equityCount = m.equity_symbols ?? 0;
  const cryptoCount = m.crypto_symbols ?? 0;

  const inPoll =
    m.poll_fallback || m.stocks_mode === 'poll' || m.crypto_mode === 'poll';
  const wsLive = m.stocks_connected || m.crypto_connected;
  const lastErr = m.last_error || '';
  const sessionOpen = usEquitySessionOpen();

  if (!wsLive && !inPoll) {
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--down"
        role="status"
      >
        Alpaca feed not connected — check ALPACA_API_KEY / ALPACA_SECRET_KEY and restart the Alpaca backend.
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
        Alpaca WebSocket unavailable — REST poll fallback active
        {lastErr ? `: ${lastErr}` : ''}. Crypto prices refresh every ~0.75s; equities need a live equity stream.
      </div>
    );
  }

  if (inPoll && wsLive) {
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--warn"
        role="status"
      >
        Alpaca partial — some markets on REST poll
        {m.stocks_mode === 'poll' ? ' (stocks)' : ''}
        {m.crypto_mode === 'poll' ? ' (crypto)' : ''}.
        {lastErr ? ` ${lastErr}` : ''}
      </div>
    );
  }

  // Outside RTH, equity candle lag is expected — don't spam "stale stocks".
  if (sessionOpen && stocksLagMin != null && stocksLagMin >= 10) {
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--warn"
        role="status"
      >
        Alpaca stocks lag ~{stocksLagMin} min — US equities may be stale.
      </div>
    );
  }

  if (cryptoLagMin != null && cryptoLagMin >= 5) {
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--warn"
        role="status"
      >
        Alpaca crypto lag ~{cryptoLagMin} min — check crypto WS or REST poll.
      </div>
    );
  }

  if ((m.seeded_symbols ?? 0) > 0 && m.seeding) {
    const expected = m.seed_expected
      ?? ((m.equity_symbols ?? 0) + (m.crypto_symbols ?? 0))
      ?? symbolCount;
    if ((m.seeded_symbols ?? 0) < expected) {
      return (
        <div
          className="terminal-feed-banner terminal-feed-banner--warn"
          role="status"
        >
          Alpaca seeding history — {m.seeded_symbols}/{expected} symbols ready.
        </div>
      );
    }
  }

  const stocksPartial =
    m.stocks_mode === 'websocket' && !m.stocks_connected && equityCount > 0;
  const cryptoPartial =
    (m.crypto_mode === 'websocket' || m.crypto_mode === 'poll')
    && !m.crypto_connected
    && !m.poll_fallback
    && cryptoCount > 0;
  if (stocksPartial || cryptoPartial) {
    const parts = [];
    if (stocksPartial) parts.push('stocks');
    if (cryptoPartial) parts.push('crypto');
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--warn"
        role="status"
      >
        Alpaca feed partial — {parts.join(' + ')} disconnected
        {lastErr ? `: ${lastErr}` : ''}.
      </div>
    );
  }

  if ((m.subscriptions ?? 0) < Math.min(symbolCount, 1) && wsLive) {
    return (
      <div
        className="terminal-feed-banner terminal-feed-banner--warn"
        role="status"
      >
        Alpaca subscriptions warming up — {m.subscriptions ?? 0} channels for {symbolCount} symbols.
      </div>
    );
  }

  if (!sessionOpen && equityCount > 0 && cryptoCount > 0 && m.crypto_connected) {
    // Soft info only when stocks look "stale" solely because the cash session is closed.
    if (stocksLagMin != null && stocksLagMin >= 10) {
      return (
        <div
          className="terminal-feed-banner terminal-feed-banner--warn"
          role="status"
        >
          US equities closed — crypto stream live. Equity charts may look frozen until the next session.
        </div>
      );
    }
  }

  return null;
}
