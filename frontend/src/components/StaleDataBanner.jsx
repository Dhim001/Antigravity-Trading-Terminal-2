import { useStore } from '../store/useStore';

function backendHint() {
  const httpPort = import.meta.env.VITE_BACKEND_HTTP_PORT;
  const profile = import.meta.env.VITE_TERMINAL_PROFILE;
  if (httpPort && profile) {
    return ` Start the ${profile} backend (HTTP :${httpPort}) or run .\\scripts\\start-${profile}.ps1`;
  }
  if (httpPort) {
    return ` Start the backend on HTTP port ${httpPort}.`;
  }
  return ' Start the backend (python main.py in backend/).';
}

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
  else if (error) message = `Could not reach backend.${backendHint()}`;

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
