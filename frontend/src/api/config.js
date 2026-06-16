/** API base URLs — empty HTTP base uses same-origin (Vite dev proxy → :8766). */

function resolveWsUrl() {
  const env = import.meta.env.VITE_WS_URL;
  if (env && String(env).trim()) {
    return String(env).trim();
  }
  if (typeof window !== 'undefined' && window.location?.host) {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}/ws`;
  }
  return 'ws://127.0.0.1:8765';
}

export const HTTP_BASE_URL = import.meta.env.VITE_HTTP_BASE_URL ?? '';
export const WS_URL = resolveWsUrl();
