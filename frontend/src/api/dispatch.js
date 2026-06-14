import { toast } from 'sonner';
import { MessageType } from './protocol';
import { useStore } from '../store/useStore';
import { forceMarketSnapshotSave } from '../services/marketSnapshot';

/** Snapshot of Zustand actions for WS / HTTP message dispatch. */
export function getStoreActions() {
  const s = useStore.getState();
  return {
    setConnectionStatus: s.setConnectionStatus,
    updateHistory: s.updateHistory,
    prependHistory: s.prependHistory,
    updateAccount: s.updateAccount,
    updateMarketData: s.updateMarketData,
    updateOrderBooks: s.updateOrderBooks,
    setOrderResult: s.setOrderResult,
    setTradeHistory: s.setTradeHistory,
    addBotLog: s.addBotLog,
    setSystemStats: s.setSystemStats,
    setTerminalConfig: s.setTerminalConfig,
    setBots: s.setBots,
    setBotLogs: s.setBotLogs,
    setBacktestResults: s.setBacktestResults,
    setBotDetail: s.setBotDetail,
    setAmbiguousOrders: s.setAmbiguousOrders,
    setTickData: s.setTickData,
    setBotHistory: s.setBotHistory,
  };
}

/**
 * Apply a server → client wire frame to the store.
 * Shared by WebSocket onmessage and HTTP bootstrap.
 */
export function applyServerMessage(type, data, storeActions, meta) {
  switch (type) {
    case MessageType.TERMINAL_CONFIG:
      storeActions.setTerminalConfig(data);
      break;
    case MessageType.HISTORY_UPDATE:
      storeActions.updateHistory(data);
      break;
    case MessageType.ACCOUNT_UPDATE:
      storeActions.updateAccount(data);
      break;
    case MessageType.MARKET_UPDATE:
      storeActions.updateMarketData(data);
      break;
    case MessageType.ORDERBOOK_UPDATE:
      storeActions.updateOrderBooks(data);
      break;
    case MessageType.ORDER_RESULT:
      storeActions.setOrderResult(data);
      if (data?.status === 'ambiguous') {
        toast.warning(data.message || 'Order outcome unknown — reconcile before retrying.');
      }
      if (data?.reconciliation?.pending) {
        storeActions.setAmbiguousOrders(data.reconciliation.pending);
      }
      if (data?.status === 'success' && /market prices preserved/i.test(data?.message ?? '')) {
        forceMarketSnapshotSave(() => useStore.getState());
      }
      break;
    case MessageType.TRADE_HISTORY:
      storeActions.setTradeHistory(data);
      break;
    case MessageType.BOT_LOG:
      storeActions.addBotLog(data);
      if (data && typeof data === 'object' && data.message) {
        if (data.level === 'ERROR') toast.error(data.message);
        else if (data.level === 'SUCCESS') toast.success(data.message);
        else if (data.level === 'WARN' && /daily loss|blocked/i.test(data.message)) {
          toast.warning(data.message);
        }
      }
      break;
    case MessageType.BOT_LOGS_HISTORY:
      storeActions.setBotLogs(data);
      break;
    case MessageType.BOTS_UPDATE:
      storeActions.setBots(data);
      break;
    case MessageType.BOT_DETAIL:
      storeActions.setBotDetail(data);
      break;
    case MessageType.SYSTEM_STATS:
      storeActions.setSystemStats(data);
      break;
    case MessageType.BACKTEST_RESULT:
      if (data?.status === 'success') {
        storeActions.setBacktestResults(data.results);
      } else {
        console.error('Backtest failed:', data?.message);
      }
      break;
    case MessageType.TICKS_UPDATE:
      storeActions.setTickData(data, meta);
      break;
    case MessageType.BOTS_HISTORY:
      storeActions.setBotHistory(data);
      break;
    case MessageType.ERROR:
      console.error('Server execution error:', data?.message ?? data);
      break;
    default:
      console.warn('Unrecognized server message type:', type);
  }
}

/** Map an HTTP action-router envelope onto the store. */
export function applyHttpEnvelope(body, storeActions) {
  if (Array.isArray(body.messages)) {
    for (const msg of body.messages) {
      if (msg?.type) {
        applyServerMessage(msg.type, msg.data, storeActions, msg.meta);
      }
    }
    return;
  }
  if (body.type) {
    applyServerMessage(body.type, body.data, storeActions, body.meta);
  }
}
