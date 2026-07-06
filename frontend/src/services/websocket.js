import { useStore } from '../store/useStore';
import { Action, MessageType } from '../api/protocol';
import { applyServerMessage, getStoreActions, resetBacktestRunState, errorAffectsBacktestRun } from '../api/dispatch';
import { toast } from 'sonner';
import { runBootstrap, resubscribeMarketSymbols } from '../api/bootstrap';
import { WS_URL } from '../api/config';
import { CHART_SNAPSHOT_BARS } from './candleBuffer';
import { getHmrData, markHmrActive, setupHmrAccept } from './hmrState';
import { decode as decodeMsgpack } from '@msgpack/msgpack';

const MSGPACK_MARKER = 0x01;

const hmr = getHmrData();

let ws = hmr?.ws ?? null;
let reconnectTimeout = hmr?.reconnectTimeout ?? null;
let reconnectDelayMs = hmr?.reconnectDelayMs ?? 3000;
const RECONNECT_BASE_MS = 3000;
const RECONNECT_MAX_MS = 30000;
/** No inbound frame for this long while OPEN → force reconnect (half-open / throttled tab). */
const STALE_CONNECTION_MS = 90000;
const STALE_CHECK_INTERVAL_MS = 15000;
let isConnecting = false;
let lastUrl = hmr?.lastUrl ?? null;
let lastMessageAt = hmr?.lastMessageAt ?? 0;
let staleWatchdog = hmr?.staleWatchdog ?? null;
let lifecycleBound = hmr?.lifecycleBound ?? false;

const clearReconnect = () => {
  if (reconnectTimeout) {
    clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
  }
};

const touchLastMessage = () => {
  lastMessageAt = Date.now();
  if (hmr) hmr.lastMessageAt = lastMessageAt;
};

const clearStaleWatchdog = () => {
  if (staleWatchdog) {
    clearInterval(staleWatchdog);
    staleWatchdog = null;
  }
  if (hmr) hmr.staleWatchdog = null;
};

const startStaleWatchdog = () => {
  clearStaleWatchdog();
  touchLastMessage();
  staleWatchdog = setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (Date.now() - lastMessageAt < STALE_CONNECTION_MS) return;
    console.warn(
      `WebSocket stale (${Math.round((Date.now() - lastMessageAt) / 1000)}s idle) — forcing reconnect.`,
    );
    try {
      ws.close(4000, 'stale connection watchdog');
    } catch {
      connectWebSocket(lastUrl || WS_URL);
    }
  }, STALE_CHECK_INTERVAL_MS);
  if (hmr) hmr.staleWatchdog = staleWatchdog;
};

const scheduleReconnect = (immediate = false) => {
  clearReconnect();
  if (immediate) {
    reconnectDelayMs = RECONNECT_BASE_MS;
    if (hmr) hmr.reconnectDelayMs = reconnectDelayMs;
    if (lastUrl) connectWebSocket(lastUrl);
    return;
  }
  const delay = reconnectDelayMs;
  reconnectDelayMs = Math.min(Math.round(reconnectDelayMs * 1.6), RECONNECT_MAX_MS);
  if (hmr) hmr.reconnectDelayMs = reconnectDelayMs;
  reconnectTimeout = setTimeout(() => {
    if (lastUrl) connectWebSocket(lastUrl);
  }, delay);
  if (hmr) hmr.reconnectTimeout = reconnectTimeout;
};

function bindConnectionLifecycle() {
  if (lifecycleBound || typeof window === 'undefined') return;
  lifecycleBound = true;
  if (hmr) hmr.lifecycleBound = true;

  window.addEventListener('online', () => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      scheduleReconnect(true);
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      scheduleReconnect(true);
    }
  });
}

async function parseWirePayload(raw) {
  if (typeof raw === 'string') {
    return JSON.parse(raw);
  }
  const buffer = raw instanceof ArrayBuffer ? raw : await raw.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  if (bytes.length > 0 && bytes[0] === MSGPACK_MARKER) {
    return decodeMsgpack(bytes.subarray(1));
  }
  return JSON.parse(new TextDecoder().decode(bytes));
}

function attachWebSocketHandlers(socket) {
  const storeActions = getStoreActions();

  socket.onmessage = async (event) => {
    touchLastMessage();
    try {
      const payload = await parseWirePayload(event.data);
      const { type, data, message, meta } = payload;

      if (type === MessageType.KEEPALIVE) {
        return;
      }

      if (type === MessageType.ERROR) {
        const errMsg = message || data?.message || 'Server error';
        console.error('Server execution error:', errMsg);
        if (useStore.getState().backtestRunning) {
          if (errorAffectsBacktestRun(errMsg)) {
            resetBacktestRunState(storeActions, { errorMessage: errMsg });
            toast.error(errMsg);
          } else {
            toast.message(errMsg);
          }
        } else {
          toast.error(errMsg);
        }
        return;
      }

      applyServerMessage(type, data, storeActions, meta);
    } catch (err) {
      console.error('Failed to parse WebSocket message:', err);
    }
  };

  socket.onclose = (event) => {
    isConnecting = false;
    ws = null;
    clearStaleWatchdog();
    if (hmr) hmr.ws = null;
    const code = event?.code ?? 'unknown';
    const reason = event?.reason || '';
    console.log(
      `WebSocket disconnected (code=${code}${reason ? ` reason=${reason}` : ''}). `
      + `Retrying in ${Math.round(reconnectDelayMs / 1000)}s…`,
    );
    storeActions.setConnectionStatus('disconnected');
    scheduleReconnect();
  };

  socket.onerror = (error) => {
    isConnecting = false;
    console.error('WebSocket transport error:', error);
  };
}

function onSocketOpen(socket) {
  isConnecting = false;
  reconnectDelayMs = RECONNECT_BASE_MS;
  if (hmr) hmr.reconnectDelayMs = reconnectDelayMs;
  const storeActions = getStoreActions();
  console.log('WebSocket connected successfully.');
  storeActions.setConnectionStatus('connected');
  startStaleWatchdog();
  const activeSymbol = useStore.getState().activeSymbol;
  socket.send(JSON.stringify({
    action: Action.SUBSCRIBE_SYMBOL,
    symbol: activeSymbol,
    limit: CHART_SNAPSHOT_BARS,
  }));
  socket.send(JSON.stringify({ action: Action.BOT_GET_ALL }));
  runBootstrap({ symbol: activeSymbol, light: true, skipCandles: true });
  resubscribeMarketSymbols();
}

function persistHmrSocket() {
  if (!hmr) return;
  hmr.ws = ws;
  hmr.lastUrl = lastUrl;
  hmr.reconnectTimeout = reconnectTimeout;
  hmr.reconnectDelayMs = reconnectDelayMs;
}

export const connectWebSocket = (url = WS_URL) => {
  bindConnectionLifecycle();
  lastUrl = url;
  persistHmrSocket();

  if (ws && ws.readyState === WebSocket.OPEN) {
    attachWebSocketHandlers(ws);
    startStaleWatchdog();
    getStoreActions().setConnectionStatus('connected');
    return;
  }

  if (ws && ws.readyState === WebSocket.CONNECTING) {
    attachWebSocketHandlers(ws);
    return;
  }

  if (isConnecting) return;

  clearReconnect();
  isConnecting = true;
  console.log('WebSocket connecting to:', url);
  ws = new WebSocket(url);
  persistHmrSocket();

  ws.onopen = () => onSocketOpen(ws);
  attachWebSocketHandlers(ws);
};

export const disconnectWebSocket = () => {
  clearReconnect();
  clearStaleWatchdog();
  isConnecting = false;
  if (ws) {
    ws.onclose = null;
    ws.close();
    ws = null;
  }
  if (hmr) {
    hmr.ws = null;
    hmr.reconnectTimeout = null;
  }
};

export const sendWebSocketAction = (action, payload = {}, opts = {}) => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action, ...payload }));
    return true;
  }
  if (!opts.silent) {
    console.warn('Cannot transmit message. WebSocket is offline.');
  }
  return false;
};

setupHmrAccept();

if (import.meta.hot) {
  import.meta.hot.dispose((data) => {
    markHmrActive();
    data.ws = ws;
    data.lastUrl = lastUrl;
    data.reconnectTimeout = reconnectTimeout;
    if (ws) {
      ws.onopen = null;
      ws.onmessage = null;
      ws.onclose = null;
      ws.onerror = null;
    }
  });
}

if (ws && ws.readyState === WebSocket.OPEN) {
  bindConnectionLifecycle();
  attachWebSocketHandlers(ws);
  startStaleWatchdog();
  getStoreActions().setConnectionStatus('connected');
}
