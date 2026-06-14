import { useStore } from '../store/useStore';
import { Action, MessageType } from '../api/protocol';
import { applyServerMessage, getStoreActions } from '../api/dispatch';
import { runBootstrap, resubscribeMarketSymbols } from '../api/bootstrap';
import { WS_URL } from '../api/config';
import { getHmrData, markHmrActive, setupHmrAccept } from './hmrState';

const hmr = getHmrData();

let ws = hmr?.ws ?? null;
let reconnectTimeout = hmr?.reconnectTimeout ?? null;
let isConnecting = false;
let lastUrl = hmr?.lastUrl ?? null;

const clearReconnect = () => {
  if (reconnectTimeout) {
    clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
  }
};

function attachWebSocketHandlers(socket) {
  const storeActions = getStoreActions();

  socket.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      const { type, data, message, meta } = payload;

      if (type === MessageType.ERROR) {
        console.error('Server execution error:', message);
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
    console.log('WebSocket disconnected. Retrying in 3s...');
    storeActions.setConnectionStatus('disconnected');
    clearReconnect();
    reconnectTimeout = setTimeout(() => {
      if (lastUrl) connectWebSocket(lastUrl);
    }, 3000);
    if (hmr) hmr.reconnectTimeout = reconnectTimeout;
  };

  socket.onerror = (error) => {
    isConnecting = false;
    console.error('WebSocket transport error:', error);
  };
}

function onSocketOpen(socket) {
  isConnecting = false;
  const storeActions = getStoreActions();
  console.log('WebSocket connected successfully.');
  storeActions.setConnectionStatus('connected');
  const activeSymbol = useStore.getState().activeSymbol;
  socket.send(JSON.stringify({ action: Action.SUBSCRIBE_SYMBOL, symbol: activeSymbol }));
  socket.send(JSON.stringify({ action: Action.BOT_GET_ALL }));
  runBootstrap({ symbol: activeSymbol, light: true, skipCandles: true });
  resubscribeMarketSymbols();
}

function persistHmrSocket() {
  if (!hmr) return;
  hmr.ws = ws;
  hmr.lastUrl = lastUrl;
  hmr.reconnectTimeout = reconnectTimeout;
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
