import { toast } from 'sonner';
import { Action } from './protocol';
import { apiAction } from './client';
import { applyHttpEnvelope, getStoreActions } from './dispatch';
import { sendWebSocketAction } from '../services/websocket';
import { useStore } from '../store/useStore';

/** @typedef {{ method: string, path: (p: object) => string, body?: (p: object) => object | undefined }} HttpRoute */

/** Maps WS actions to REST endpoints (mirrors backend/app/api/http/bindings.py). */
export const HTTP_ROUTES = Object.freeze({
  [Action.GET_ACCOUNT]: { method: 'GET', path: () => '/api/v1/account' },
  [Action.GET_HISTORY]: { method: 'GET', path: () => '/api/v1/history' },
  [Action.SUBSCRIBE_SYMBOL]: {
    method: 'GET',
    path: (p) => {
      const qs = new URLSearchParams();
      if (p.limit != null && p.limit !== '') qs.set('limit', String(p.limit));
      const q = qs.toString();
      return `/api/v1/market/${encodeURIComponent(p.symbol)}/candles${q ? `?${q}` : ''}`;
    },
  },
  [Action.GET_MARKET_HISTORY]: {
    method: 'GET',
    path: (p) => {
      const qs = new URLSearchParams();
      if (p.from != null) qs.set('from', String(p.from));
      if (p.to != null) qs.set('to', String(p.to));
      if (p.interval) qs.set('interval', p.interval);
      const q = qs.toString();
      return `/api/v1/market/${encodeURIComponent(p.symbol)}/history${q ? `?${q}` : ''}`;
    },
  },
  [Action.PLACE_ORDER]: { method: 'POST', path: () => '/api/v1/orders', body: (p) => p },
  [Action.PREVIEW_ORDER]: { method: 'POST', path: () => '/api/v1/orders/preview', body: (p) => p },
  [Action.CANCEL_ORDER]: {
    method: 'DELETE',
    path: (p) => `/api/v1/orders/${encodeURIComponent(p.order_id)}`,
  },
  [Action.UPDATE_POSITION_SL_TP]: {
    method: 'PATCH',
    path: (p) => `/api/v1/positions/${encodeURIComponent(p.symbol)}/sl-tp`,
    body: (p) => {
      const { symbol: _symbol, ...rest } = p;
      return rest;
    },
  },
  [Action.BOT_GET_ALL]: { method: 'GET', path: () => '/api/v1/bots' },
  [Action.BOT_LIST_ALL]: {
    method: 'GET',
    path: (p) => {
      const qs = p.limit != null ? `?limit=${encodeURIComponent(p.limit)}` : '';
      return `/api/v1/bots/all${qs}`;
    },
  },
  [Action.BOT_CREATE]: { method: 'POST', path: () => '/api/v1/bots', body: (p) => p },
  [Action.BOT_GET_DETAIL]: {
    method: 'GET',
    path: (p) => `/api/v1/bots/${encodeURIComponent(p.bot_id)}`,
  },
  [Action.BOT_UPDATE_CONFIG]: {
    method: 'PATCH',
    path: (p) => `/api/v1/bots/${encodeURIComponent(p.bot_id)}/config`,
    body: (p) => {
      const { bot_id: _botId, ...rest } = p;
      return rest;
    },
  },
  [Action.BOT_STOP]: {
    method: 'POST',
    path: (p) => `/api/v1/bots/${encodeURIComponent(p.bot_id)}/stop`,
  },
  [Action.BOT_PAUSE]: {
    method: 'POST',
    path: (p) => `/api/v1/bots/${encodeURIComponent(p.bot_id)}/pause`,
  },
  [Action.BOT_RESUME]: {
    method: 'POST',
    path: (p) => `/api/v1/bots/${encodeURIComponent(p.bot_id)}/resume`,
  },
  [Action.BOT_STOP_ALL]: { method: 'POST', path: () => '/api/v1/bots/stop-all' },
  [Action.RUN_BACKTEST]: { method: 'POST', path: () => '/api/v1/backtest', body: (p) => p },
  [Action.RUN_BACKTEST_SWEEP]: { method: 'POST', path: () => '/api/v1/backtest/sweep', body: (p) => p },
  [Action.CANCEL_BACKTEST]: { method: 'POST', path: () => '/api/v1/backtest/cancel', body: (p) => p },
  [Action.ADMIN_GET_STATS]: { method: 'GET', path: () => '/api/v1/admin/stats' },
  [Action.ADMIN_ARCHIVE_BACKFILL]: {
    method: 'POST',
    path: () => '/api/v1/admin/archive/backfill',
    body: (p) => p,
  },
  [Action.ADMIN_ARCHIVE_EXPORT]: {
    method: 'POST',
    path: () => '/api/v1/admin/archive/export',
    body: (p) => p,
  },
  [Action.ADMIN_GET_RECONCILIATION]: {
    method: 'GET',
    path: () => '/api/v1/admin/reconciliation',
  },
  [Action.ADMIN_RECONCILE]: {
    method: 'POST',
    path: () => '/api/v1/admin/reconciliation/reconcile',
    body: (p) => p,
  },
  [Action.ADMIN_RESOLVE_AMBIGUOUS]: {
    method: 'POST',
    path: () => '/api/v1/admin/reconciliation/resolve',
    body: (p) => p,
  },
  [Action.GET_MARKET_TICKS]: {
    method: 'GET',
    path: (p) => {
      const qs = new URLSearchParams();
      if (p.from != null) qs.set('from', String(p.from));
      if (p.to != null) qs.set('to', String(p.to));
      const q = qs.toString();
      return `/api/v1/market/${encodeURIComponent(p.symbol)}/ticks${q ? `?${q}` : ''}`;
    },
  },
  [Action.ADMIN_SET_SIMULATION]: {
    method: 'POST',
    path: () => '/api/v1/admin/simulation',
    body: (p) => p,
  },
  [Action.ADMIN_SEED_BALANCE]: {
    method: 'POST',
    path: () => '/api/v1/admin/seed-balance',
    body: (p) => p,
  },
  [Action.ADMIN_RESET_SYSTEM]: { method: 'POST', path: () => '/api/v1/admin/reset' },
  [Action.ADMIN_EMERGENCY_STOP]: { method: 'POST', path: () => '/api/v1/admin/emergency-stop' },
  [Action.CHART_ANALYZE]: { method: 'POST', path: () => '/api/v1/agent/analyze', body: (p) => p },
  [Action.CHART_DEEP_REASON]: { method: 'POST', path: () => '/api/v1/agent/deep-reason', body: (p) => p },
  [Action.EXPLAIN_TRADE]: { method: 'POST', path: () => '/api/v1/agent/explain-trade', body: (p) => p },
  [Action.MARKET_SCAN]: { method: 'POST', path: () => '/api/v1/scanner/scan', body: (p) => p },
  [Action.CHART_VISION]: { method: 'POST', path: () => '/api/v1/agent/vision', body: (p) => p },
});

export async function invokeHttpAction(action, payload = {}) {
  const route = HTTP_ROUTES[action];
  if (!route) {
    throw new Error(`No HTTP route for action: ${action}`);
  }

  const options = { method: route.method };
  if (route.body) {
    const body = route.body(payload);
    if (body != null && Object.keys(body).length > 0) {
      options.body = body;
    }
  }

  const envelope = await apiAction(route.path(payload), options);
  applyHttpEnvelope(envelope, getStoreActions());
  return envelope;
}

const STALE_SCANNER_MSG =
  'Scanner API not found — restart the backend (python main.py in backend/) to load the latest routes.';

function isNotFoundError(err) {
  const msg = String(err?.message || err || '');
  return /404|not found/i.test(msg);
}

/** Wait for SCAN_RESULTS after a WebSocket market_scan dispatch. */
export function waitForScanResults({ timeoutMs = 30000, previousAt = null } = {}) {
  return new Promise((resolve, reject) => {
    const current = useStore.getState().scanResults;
    if (current?.scanned_at && current.scanned_at !== previousAt) {
      resolve(current);
      return;
    }

    const timer = setTimeout(() => {
      unsub();
      reject(new Error('Scan timed out waiting for results'));
    }, timeoutMs);

    const unsub = useStore.subscribe((state) => {
      const next = state.scanResults;
      if (next?.scanned_at && next.scanned_at !== previousAt) {
        clearTimeout(timer);
        unsub();
        resolve(next);
      }
    });
  });
}

/**
 * Run market scan — HTTP first (reliable response), WebSocket fallback if HTTP route is missing.
 */
export async function runMarketScan(payload) {
  const previousAt = useStore.getState().scanResults?.scanned_at ?? null;

  try {
    await invokeHttpAction(Action.MARKET_SCAN, payload);
    return useStore.getState().scanResults;
  } catch (err) {
    if (!isNotFoundError(err)) throw err;
  }

  if (!sendWebSocketAction(Action.MARKET_SCAN, payload)) {
    throw new Error(STALE_SCANNER_MSG);
  }

  try {
    return await waitForScanResults({ previousAt });
  } catch {
    throw new Error(STALE_SCANNER_MSG);
  }
}

/**
 * Send a WS action — WebSocket first, HTTP fallback when offline.
 * @returns {Promise<{ ok: boolean, transport?: 'ws' | 'http', error?: string }>}
 */
export function sendAction(action, payload = {}) {
  if (sendWebSocketAction(action, payload)) {
    return Promise.resolve({ ok: true, transport: 'ws' });
  }

  if (!HTTP_ROUTES[action]) {
    const message = `Cannot send ${action}: WebSocket offline and no HTTP fallback.`;
    console.warn(message);
    return Promise.resolve({ ok: false, error: message });
  }

  return invokeHttpAction(action, payload)
    .then(() => ({ ok: true, transport: 'http' }))
    .catch((err) => {
      const message = err?.message || 'Request failed';
      console.error(`HTTP fallback failed for ${action}:`, err);
      toast.error(message);
      return { ok: false, error: message };
    });
}
