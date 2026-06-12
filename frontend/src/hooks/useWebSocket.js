import { useEffect } from 'react';
import { useStore } from '../store/useStore';
import { connectWebSocket, disconnectWebSocket, sendWebSocketAction } from '../services/websocket';

export const useWebSocket = (url) => {
  const {
    setConnectionStatus, updateHistory, updateAccount,
    updateMarketData, updateOrderBooks, setOrderResult, setTradeHistory, addBotLog, setSystemStats,
    setTerminalConfig, setBots, setBotLogs, setBacktestResults, setBotDetail
  } = useStore();

  useEffect(() => {
    const storeActions = {
      setConnectionStatus,
      updateHistory,
      updateAccount,
      updateMarketData,
      updateOrderBooks,
      setOrderResult,
      setTradeHistory,
      addBotLog,
      setSystemStats,
      setTerminalConfig,
      setBots,
      setBotLogs,
      setBacktestResults,
      setBotDetail,
    };

    connectWebSocket(url, storeActions);

    return () => {
      // Keep connection active across component updates.
    };
  }, [url]);

  return { sendAction: sendWebSocketAction };
};
