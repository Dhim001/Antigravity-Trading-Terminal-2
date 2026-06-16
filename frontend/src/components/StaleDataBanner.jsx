import { useStore } from '../store/useStore';

export default function StaleDataBanner() {
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

  return (
    <div
      className="stale-data-banner border-b border-trading-warn/30 bg-trading-warn/10 px-3 py-1.5 text-center text-xs text-trading-warn"
      role="status"
    >
      {message}
    </div>
  );
}
