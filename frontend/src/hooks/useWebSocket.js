import { useEffect } from 'react';
import { useStore } from '../store/useStore';
import { connectWebSocket, disconnectWebSocket, sendWebSocketAction } from '../services/websocket';

export const useWebSocket = (url) => {
  const {
    setConnectionStatus, updateHistory, updateAccount,
    updateMarketData, setOrderResult, setTradeHistory, addBotLog, setSystemStats
  } = useStore();

  useEffect(() => {
    const storeActions = {
      setConnectionStatus,
      updateHistory,
      updateAccount,
      updateMarketData,
      setOrderResult,
      setTradeHistory,
      addBotLog,
      setSystemStats,
    };

    connectWebSocket(url, storeActions);

    return () => {
      // Keep connection active across component updates.
    };
  }, [url]);

  return { sendAction: sendWebSocketAction };
};
