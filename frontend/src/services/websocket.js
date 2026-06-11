import { useStore } from '../store/useStore';

let ws = null;
let reconnectTimeout = null;
let isConnecting = false;
let lastUrl = null;
let lastStoreActions = null;

const clearReconnect = () => {
  if (reconnectTimeout) {
    clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
  }
};

export const connectWebSocket = (url, storeActions) => {
  lastUrl = url;
  lastStoreActions = storeActions;

  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    return;
  }
  if (isConnecting) {
    return;
  }

  clearReconnect();
  isConnecting = true;
  console.log("WebSocket connecting to:", url);
  ws = new WebSocket(url);

  ws.onopen = () => {
    isConnecting = false;
    console.log("WebSocket connected successfully.");
    storeActions.setConnectionStatus('connected');
    const activeSymbol = useStore.getState().activeSymbol;
    ws.send(JSON.stringify({ action: "subscribe_symbol", symbol: activeSymbol }));
    ws.send(JSON.stringify({ action: "bot_get_all" }));
  };

  ws.onclose = () => {
    isConnecting = false;
    ws = null;
    console.log("WebSocket disconnected. Retrying in 3s...");
    storeActions.setConnectionStatus('disconnected');
    clearReconnect();
    reconnectTimeout = setTimeout(() => {
      if (lastUrl && lastStoreActions) {
        connectWebSocket(lastUrl, lastStoreActions);
      }
    }, 3000);
  };

  ws.onerror = (error) => {
    isConnecting = false;
    console.error("WebSocket transport error:", error);
  };

  ws.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      const { type, data } = payload;

      switch (type) {
        case 'terminal_config':
          storeActions.setTerminalMode(data.terminalMode);
          if (data.symbols) {
            storeActions.setSymbolsList(data.symbols);
          }
          break;
        case 'history_update':
          storeActions.updateHistory(data);
          break;
        case 'account_update':
          storeActions.updateAccount(data);
          break;
        case 'market_update':
          storeActions.updateMarketData(data);
          break;
        case 'orderbook_update':
          storeActions.updateOrderBooks(data);
          break;
        case 'order_result':
          storeActions.setOrderResult(data);
          break;
        case 'trade_history':
          storeActions.setTradeHistory(data);
          break;
        case 'bot_log':
          storeActions.addBotLog(data);
          break;
        case 'bot_logs_history':
          storeActions.setBotLogs(data);
          break;
        case 'bots_update':
          storeActions.setBots(data);
          break;
        case 'system_stats':
          storeActions.setSystemStats(data);
          break;
        case 'backtest_result':
          if (data.status === 'success') {
            storeActions.setBacktestResults(data.results);
          } else {
            console.error("Backtest failed:", data.message);
          }
          break;
        case 'error':
          console.error("Server execution error:", payload.message);
          break;
        default:
          console.warn("Unrecognized WebSocket frame type:", type);
      }
    } catch (err) {
      console.error("Failed to parse WebSocket message:", err);
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
  console.warn("Cannot transmit message. WebSocket is offline.");
  return false;
};

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    disconnectWebSocket();
  });
}
