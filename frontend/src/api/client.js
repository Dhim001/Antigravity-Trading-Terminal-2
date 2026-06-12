import { HTTP_BASE_URL } from './config';

const DEFAULT_TIMEOUT_MS = 8000;
const API_KEY = import.meta.env.VITE_HTTP_API_KEY ?? '';

function joinUrl(path) {
  const base = HTTP_BASE_URL.replace(/\/$/, '');
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return base ? `${base}${suffix}` : suffix;
}

/**
 * @param {string} path
 * @param {{ method?: string, body?: unknown, timeoutMs?: number }} [options]
 */
export async function apiRequest(path, options = {}) {
  const { method = 'GET', body, timeoutMs = DEFAULT_TIMEOUT_MS } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const init = {
      method,
      signal: controller.signal,
      headers: {},
    };
    if (API_KEY) {
      init.headers['X-API-Key'] = API_KEY;
    }
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }

    const response = await fetch(joinUrl(path), init);
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new Error(`Invalid JSON from ${path}`);
    }

    if (!response.ok) {
      throw new Error(payload?.error || payload?.message || `HTTP ${response.status}`);
    }
    return payload;
  } finally {
    clearTimeout(timer);
  }
}

/** Action-router envelope: { ok, type, data, messages } */
export async function apiAction(path, options = {}) {
  const body = await apiRequest(path, options);
  if (body.ok === false) {
    throw new Error(body.error || 'Request failed');
  }
  return body;
}
