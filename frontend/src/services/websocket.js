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
let isConnecting = false;
let lastUrl = hmr?.lastUrl ?? null;

const clearReconnect = () => {
  if (reconnectTimeout) {
    clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
  }
};

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
    try {
      const payload = await parseWirePayload(event.data);
      const { type, data, message, meta } = payload;

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

  socket.onclose = () => {
    isConnecting = false;
    ws = null;
    if (hmr) hmr.ws = null;
    console.log(`WebSocket disconnected. Retrying in ${Math.round(reconnectDelayMs / 1000)}s...`);
    storeActions.setConnectionStatus('disconnected');
    clearReconnect();
    const delay = reconnectDelayMs;
    reconnectDelayMs = Math.min(Math.round(reconnectDelayMs * 1.6), RECONNECT_MAX_MS);
    if (hmr) hmr.reconnectDelayMs = reconnectDelayMs;
    reconnectTimeout = setTimeout(() => {
      if (lastUrl) connectWebSocket(lastUrl);
    }, delay);
    if (hmr) hmr.reconnectTimeout = reconnectTimeout;
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
  lastUrl = url;
  persistHmrSocket();

  if (ws && ws.readyState === WebSocket.OPEN) {
    attachWebSocketHandlers(ws);
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

export const sendWebSocketAction = (action, payload = {}) => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action, ...payload }));
    return true;
  }
  console.warn('Cannot transmit message. WebSocket is offline.');
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
  attachWebSocketHandlers(ws);
  getStoreActions().setConnectionStatus('connected');
}
