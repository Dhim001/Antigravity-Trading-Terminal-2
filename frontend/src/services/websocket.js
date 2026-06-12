import { useStore } from '../store/useStore';
import { Action, MessageType } from '../api/protocol';
import { applyServerMessage, getStoreActions } from '../api/dispatch';
import { WS_URL } from '../api/config';

let ws = null;
let reconnectTimeout = null;
let isConnecting = false;
let lastUrl = null;

const clearReconnect = () => {
  if (reconnectTimeout) {
    clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
  }
};

export const connectWebSocket = (url = WS_URL) => {
  lastUrl = url;
  const storeActions = getStoreActions();

  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    return;
  }
  if (isConnecting) {
    return;
  }

  clearReconnect();
  isConnecting = true;
  console.log('WebSocket connecting to:', url);
  ws = new WebSocket(url);

  ws.onopen = () => {
    isConnecting = false;
    console.log('WebSocket connected successfully.');
    storeActions.setConnectionStatus('connected');
    const activeSymbol = useStore.getState().activeSymbol;
    ws.send(JSON.stringify({ action: Action.SUBSCRIBE_SYMBOL, symbol: activeSymbol }));
    ws.send(JSON.stringify({ action: Action.BOT_GET_ALL }));
  };

  ws.onclose = () => {
    isConnecting = false;
    ws = null;
    console.log('WebSocket disconnected. Retrying in 3s...');
    storeActions.setConnectionStatus('disconnected');
    clearReconnect();
    reconnectTimeout = setTimeout(() => {
      if (lastUrl) {
        connectWebSocket(lastUrl);
      }
    }, 3000);
  };

  ws.onerror = (error) => {
    isConnecting = false;
    console.error('WebSocket transport error:', error);
  };

  ws.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      const { type, data, message } = payload;

      if (type === MessageType.ERROR) {
        console.error('Server execution error:', message);
        return;
      }

      applyServerMessage(type, data, storeActions);
    } catch (err) {
      console.error('Failed to parse WebSocket message:', err);
    }
  };
};

export const disconnectWebSocket = () => {
  clearReconnect();
  isConnecting = false;
  if (ws) {
    ws.onclose = null;
    ws.close();
    ws = null;
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

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    disconnectWebSocket();
  });
}
