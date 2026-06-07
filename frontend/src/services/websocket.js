let ws = null;
let reconnectTimeout = null;

export const connectWebSocket = (url, storeActions) => {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    return;
  }

  console.log("WebSocket connecting to:", url);
  ws = new WebSocket(url);

  ws.onopen = () => {
    console.log("WebSocket connected successfully.");
    storeActions.setConnectionStatus('connected');
  };

  ws.onclose = () => {
    console.log("WebSocket disconnected. Retrying in 3s...");
    storeActions.setConnectionStatus('disconnected');
    reconnectTimeout = setTimeout(() => connectWebSocket(url, storeActions), 3000);
  };

  ws.onerror = (error) => {
    console.error("WebSocket transport error:", error);
  };

  ws.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      const { type, data } = payload;

      switch (type) {
        case 'history_update':
          storeActions.updateHistory(data);
          break;
        case 'account_update':
          storeActions.updateAccount(data);
          break;
        case 'market_update':
          storeActions.updateMarketData(data);
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
  if (reconnectTimeout) {
    clearTimeout(reconnectTimeout);
  }
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
