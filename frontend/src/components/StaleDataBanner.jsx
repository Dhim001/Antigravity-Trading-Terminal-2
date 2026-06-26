import { useStore } from '../store/useStore';

export default function StaleDataBanner({ inline = false }) {
  const connectionStatus = useStore((s) => s.connectionStatus);
  const apiStatus = useStore((s) => s.apiStatus);

  const connected = connectionStatus === 'connected';
  const loading = apiStatus === 'loading';
  const ready = apiStatus === 'ready';
  const error = apiStatus === 'error';

  if (connected) return null;

  let message = 'Backend unreachable — retrying connection…';
  if (loading) message = 'Loading account snapshot via REST…';
  else if (ready) message = 'Live WebSocket disconnected — showing last REST snapshot. Prices may be stale.';
  else if (error) message = 'Could not reach backend. Check that the server is running.';

  if (inline) {
    return (
      <span
        className="terminal-warn-chip stale-data-banner stale-data-banner--inline"
        role="status"
        title={message}
      >
        Stale
      </span>
    );
  }

  return (
    <div
      className="terminal-feed-banner stale-data-banner"
      role="status"
    >
      {message}
    </div>
  );
}
