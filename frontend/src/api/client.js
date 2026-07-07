import { HTTP_BASE_URL } from './config';

const DEFAULT_TIMEOUT_MS = 8000;
const API_KEY = import.meta.env.VITE_HTTP_API_KEY ?? '';

export function isAbortError(err) {
  return err?.name === 'AbortError' || /aborted/i.test(String(err?.message || ''));
}

function joinUrl(path) {
  const base = HTTP_BASE_URL.replace(/\/$/, '');
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return base ? `${base}${suffix}` : suffix;
}

/**
 * @param {string} path
 * @param {{ method?: string, body?: unknown, timeoutMs?: number, signal?: AbortSignal }} [options]
 */
export async function apiRequest(path, options = {}) {
  const { method = 'GET', body, timeoutMs = DEFAULT_TIMEOUT_MS, signal: externalSignal } = options;
  const controller = new AbortController();

  const abort = (reason) => {
    if (!controller.signal.aborted) controller.abort(reason);
  };

  const timer = setTimeout(() => abort('timeout'), timeoutMs);

  let onExternalAbort;
  if (externalSignal) {
    if (externalSignal.aborted) {
      clearTimeout(timer);
      throw new DOMException('Aborted', 'AbortError');
    }
    onExternalAbort = () => abort('cancelled');
    externalSignal.addEventListener('abort', onExternalAbort, { once: true });
  }

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
    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        if (response.status === 404 && path.startsWith('/api/v1/news/')) {
          throw new Error('News API not found — restart the backend to load the latest server code');
        }
        const preview = text.trim().slice(0, 100).replace(/\s+/g, ' ');
        throw new Error(
          `Invalid JSON from ${path} (HTTP ${response.status}${preview ? `: ${preview}` : ''})`,
        );
      }
    }

    if (!response.ok) {
      throw new Error(payload?.error || payload?.message || `HTTP ${response.status}`);
    }
    if (payload == null && response.ok) {
      throw new Error(`Empty response from ${path} (HTTP ${response.status})`);
    }
    return payload;
  } catch (err) {
    if (isAbortError(err) && controller.signal.reason === 'timeout') {
      throw new Error(`Request timed out after ${timeoutMs}ms: ${path}`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
    if (externalSignal && onExternalAbort) {
      externalSignal.removeEventListener('abort', onExternalAbort);
    }
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
