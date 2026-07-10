import { toast } from 'sonner';
import { clearBacktestClientTimeout } from '../lib/backtestTimeouts';
import { trimBacktestPayload, buildBacktestOverlay } from '../lib/backtestSlim';
import { saveFullBacktestResults } from '../services/backtestStorage';
import { stopBacktestJobPolling, scheduleBacktestJobPoll } from '../lib/backtestPolling';
import { MessageType } from './protocol';
import { useStore } from '../store/useStore';
import { useResearchStore } from '../store/useResearchStore';
import { forceMarketSnapshotSave } from '../services/marketSnapshot';
import { queueMarketUpdate } from '../services/marketUpdateBatch';

/** Background feature errors that must not cancel an in-flight backtest. */
const BACKTEST_UNRELATED_ERROR_PATTERNS = [
  /rate limited.*analyz/i,
  /rate limited.*deep reason/i,
  /rate limited.*scan/i,
  /rate limited.*vision/i,
  /rate limited.*trade action/i,
  /chart analyst is disabled/i,
  /not enough candle data for analysis/i,
];

/** True when a server ERROR should end the current backtest run. */
export function errorAffectsBacktestRun(message) {
  const msg = String(message || '').trim();
  if (!msg) return false;
  return !BACKTEST_UNRELATED_ERROR_PATTERNS.some((re) => re.test(msg));
}

/** Clear running backtest UI state after error, cancel, timeout, or completion. */
export function resetBacktestRunState(storeActions, { errorMessage = null, request = null } = {}) {
  stopBacktestJobPolling();
  clearBacktestClientTimeout();
  storeActions.setBacktestRunning(false);
  storeActions.setBacktestProgress(null);
  if (errorMessage) {
    storeActions.setBacktestLastError?.(errorMessage, request);
  }
}

/** Snapshot of Zustand actions for WS / HTTP message dispatch. */
export function getStoreActions() {
  const s = useStore.getState();
  const r = useResearchStore.getState();
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
    setSelectedLlmModel: s.setSelectedLlmModel,
    setBots: s.setBots,
    setBotLogs: s.setBotLogs,
    setBacktestResults: r.setBacktestResults,
    setBacktestRuns: r.setBacktestRuns,
    setBacktestRunning: r.setBacktestRunning,
    setBacktestProgress: r.setBacktestProgress,
    setBacktestJobId: r.setBacktestJobId,
    setBacktestLabOpen: r.setBacktestLabOpen,
    setBacktestSnapshot: r.setBacktestSnapshot,
    setBacktestLastError: r.setBacktestLastError,
    clearBacktestLastError: r.clearBacktestLastError,
    setBacktestOverlay: r.setBacktestOverlay,
    clearBacktestOverlay: r.clearBacktestOverlay,
    setStrategyCatalog: s.setStrategyCatalog,
    setBotDetail: s.setBotDetail,
    setAmbiguousOrders: s.setAmbiguousOrders,
    setTickData: s.setTickData,
    setBotHistory: s.setBotHistory,
    setAgentInsight: r.setAgentInsight,
    setAgentInsightHistory: r.setAgentInsightHistory,
    setAgentDeepReasoning: r.setAgentDeepReasoning,
    setTradeExplain: r.setTradeExplain,
    setScanResults: r.setScanResults,
    setVisionReport: r.setVisionReport,
    setChartDrawings: s.setChartDrawings,
    setAnalyticsReport: r.setAnalyticsReport,
    setAnalyticsLoading: r.setAnalyticsLoading,
    setJournalEntries: r.setJournalEntries,
    upsertJournalEntry: r.upsertJournalEntry,
    removeJournalEntry: r.removeJournalEntry,
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
      storeActions.updateHistory(data, meta);
      break;
    case MessageType.ACCOUNT_UPDATE:
      storeActions.updateAccount(data);
      break;
    case MessageType.MARKET_UPDATE:
      queueMarketUpdate(data, storeActions.updateMarketData);
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
    case MessageType.ORDER_PREVIEW:
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
          // Cooloff/streak holds are shown on the Active Bots panel — skip repeat toasts.
          if (!/Cooling-off|Consecutive-loss streak|Auto-paused after loss streak|Max drawdown circuit breaker|Auto-paused at max drawdown/i.test(data.message)) {
            toast.warning(data.message);
          }
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
    case MessageType.BACKTEST_PROGRESS:
      if (data?.job_id) storeActions.setBacktestJobId(data.job_id);
      storeActions.setBacktestProgress(data);
      if (data?.phase === 'queued' && data?.job_id) {
        storeActions.setBacktestRunning(true);
        import('./endpoints').then(({ startBacktestJobPolling }) => {
          startBacktestJobPolling(data.job_id, storeActions);
        });
      }
      break;
    case MessageType.BACKTEST_RESULT:
      stopBacktestJobPolling();
      clearBacktestClientTimeout();
      storeActions.setBacktestRunning(false);
      storeActions.setBacktestProgress(null);
      if (data?.job_id) storeActions.setBacktestJobId(data.job_id);
      if (data?.status === 'cancelled') {
        toast.info(data?.message || 'Backtest cancelled');
        break;
      }
      if (data?.status === 'success' && data?.results && !data.results.error) {
        storeActions.clearBacktestLastError?.();
        const results = trimBacktestPayload(data.results);
        saveFullBacktestResults(results);
        storeActions.setBacktestResults(results);
        const sym = results?.meta?.symbol;
        const pnl = results?.total_pnl;
        const trades = results?.trade_count ?? 0;
        const explained = results?.reasoning?.trade_count
          ?? results?.reasoning?.trades?.length
          ?? 0;
        const pnlLabel = pnl != null
          ? `${pnl >= 0 ? '+' : ''}$${Number(pnl).toFixed(2)}`
          : '—';
        const explainSuffix = explained > 0 ? ` · ${explained} LLM explained` : '';
        toast.success(`Backtest complete · ${pnlLabel} · ${trades} trade${trades !== 1 ? 's' : ''}${explainSuffix}`, {
          action: {
            label: 'Open Lab',
            onClick: () => useResearchStore.getState().openBacktestLab('results'),
          },
        });
        const overlay = buildBacktestOverlay(results);
        if (overlay) {
          storeActions.setBacktestOverlay(overlay);
        }
        if (results?.sweep) {
          const comboCount = results?.sweep?.configs?.length || results?.sweep?.sweep_rows?.length || '?';
          toast.success(`Sweep complete · best of ${comboCount} combos · $${Number(results.total_pnl ?? 0).toFixed(2)}`);
        }
        import('./endpoints').then(({ fetchBacktestRuns }) => {
          fetchBacktestRuns(storeActions, sym);
        });
      } else {
        const msg = data?.results?.error || data?.message || 'Backtest failed';
        console.error('Backtest failed:', msg);
        storeActions.setBacktestLastError?.(msg, data?.request ?? null);
        toast.error(msg, {
          action: {
            label: 'Recovery',
            onClick: () => useResearchStore.getState().openBacktestLab('results'),
          },
        });
      }
      break;
    case MessageType.TICKS_UPDATE:
      storeActions.setTickData(data, meta);
      break;
    case MessageType.BOTS_HISTORY:
      storeActions.setBotHistory(data);
      break;
    case MessageType.AGENT_INSIGHT:
      if (data?.symbol) {
        storeActions.setAgentInsight(data.symbol, data);
      }
      break;
    case MessageType.AGENT_DEEP_REASON:
      if (data?.insight_id) {
        storeActions.setAgentDeepReasoning(data.insight_id, data);
        toast.success('Deep reasoning ready');
      }
      break;
    case MessageType.TRADE_EXPLAIN:
      if (data?.trade_id != null) {
        storeActions.setTradeExplain(String(data.trade_id), data);
      }
      break;
    case MessageType.SCAN_RESULTS:
      storeActions.setScanResults(data);
      break;
    case MessageType.CHART_DRAWINGS:
      if (data?.symbol) {
        storeActions.setChartDrawings(data.symbol, Array.isArray(data.drawings) ? data.drawings : []);
      }
      break;
    case MessageType.ANALYTICS_REPORT:
      storeActions.setAnalyticsReport(data);
      break;
    case MessageType.JOURNAL_ENTRIES:
      storeActions.setJournalEntries(data?.entries);
      break;
    case MessageType.JOURNAL_ENTRY:
      if (data?.id) storeActions.upsertJournalEntry(data);
      break;
    case MessageType.JOURNAL_DELETED:
      if (data?.id) storeActions.removeJournalEntry(data.id);
      break;
    case MessageType.VISION_REPORT:
      if (data?.symbol && data?.timeframe) {
        storeActions.setVisionReport(`${data.symbol}:${data.timeframe}`, data);
      }
      break;
    case MessageType.ERROR: {
      storeActions.setAnalyticsLoading(false);
      const errMsg = data?.message ?? (typeof data === 'string' ? data : null) ?? 'Server error';
      console.error('Server execution error:', errMsg);
      if (useResearchStore.getState().backtestRunning) {
        if (errorAffectsBacktestRun(errMsg)) {
          resetBacktestRunState(storeActions, { errorMessage: errMsg });
          toast.error(errMsg);
        } else {
          toast.message(errMsg);
        }
      } else {
        toast.error(errMsg);
      }
      break;
    }
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
