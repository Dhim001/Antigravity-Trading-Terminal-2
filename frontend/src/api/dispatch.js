import { toast } from 'sonner';
import { clearBacktestClientTimeout } from '../lib/backtestTimeouts';
import { buildBacktestOverlay } from '../lib/backtestSlim';
import { trimBacktestPayloadAsync } from '../lib/backtestSlimAsync';
import { saveFullBacktestResults, offloadBacktestFromMemory } from '../services/backtestStorage';
import { stopBacktestJobPolling, scheduleBacktestJobPoll, claimBacktestJobCompletion } from '../lib/backtestPolling';
import { MessageType } from './protocol';
import { useStore } from '../store/useStore';
import { useResearchStore } from '../store/useResearchStore';
import { forceMarketSnapshotSave } from '../services/marketSnapshot';
import { preferLiveAlpacaSymbol } from '../lib/massiveMarket';
import { queueMarketUpdate } from '../services/marketUpdateBatch';

let alpacaWsSymbolNudgeDone = false;

/**
 * Persist full payload; keep slim stub in Zustand unless Lab is already open
 * (MEMORY_CENTRIC_REVIEW #11 — Lab restores from session/IDB on open).
 */
export function storeBacktestResultsAware(storeActions, results) {
  if (!results) {
    storeActions.setBacktestResults?.(null);
    return;
  }
  const labOpen = Boolean(useResearchStore.getState().backtestLabOpen);
  if (labOpen) {
    saveFullBacktestResults(results);
    storeActions.setBacktestResults(results);
    return;
  }
  storeActions.setBacktestResults(offloadBacktestFromMemory(results));
}

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

/**
 * The job_id the UI is currently following, or null when the previous run is
 * already finished. A terminal slot must not keep ownership, otherwise a new
 * run's messages are discarded as belonging to a foreign job.
 */
function watchedBacktestJobId() {
  const state = useResearchStore.getState();
  const id = state.backtestJobId;
  if (!id) return null;
  const status = state.backtestJobsById?.[id]?.status;
  if (status && !['pending', 'running'].includes(status)) return null;
  return id;
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
    upsertBacktestJobSlot: r.upsertBacktestJobSlot,
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
    appendCopilotMessage: s.appendCopilotMessage,
    clearCopilotMessages: s.clearCopilotMessages,
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
      if (!alpacaWsSymbolNudgeDone) {
        const state = useStore.getState();
        const nudge = preferLiveAlpacaSymbol(
          state.terminalMode,
          state.activeSymbol,
          state.symbolsList,
        );
        if (nudge) {
          alpacaWsSymbolNudgeDone = true;
          state.setActiveSymbol(nudge);
        } else if (state.terminalMode === 'LIVE_ALPACA') {
          alpacaWsSymbolNudgeDone = true;
        }
      }
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
      // Risk handlers reuse ORDER_RESULT as a generic reply envelope. Do not
      // push those into orderResult or OrderEntryWidget will toast every
      // config load / entry preview / basket-correlation check.
      if (
        data?.risk_config != null
        || data?.risk_preview != null
        || data?.basket_correlation != null
      ) {
        break;
      }
      if (data?.kill_switch_reset) {
        // Clear stale portfolio alert immediately — full dashboard analytics
        // often times out and would leave kill_switch_tripped stuck in the UI.
        const report = useResearchStore.getState().analyticsReport;
        if (report?.risk) {
          useResearchStore.getState().setAnalyticsReport({
            ...report,
            risk: {
              ...report.risk,
              kill_switch_tripped: false,
              kill_switch_tripped_at: null,
              kill_switch_trip_drawdown_pct: null,
              ...(data.equity_peak != null ? { equity_peak: data.equity_peak } : {}),
            },
          });
        }
        toast.success(data.message || 'Kill switch reset');
        break;
      }
      storeActions.setOrderResult(data);
      // Toast here so Positions quick-trade works even when Order Entry is unmounted.
      if (data?.status === 'success') {
        toast.success(data.message || 'Order filled');
      } else if (data?.status === 'error') {
        toast.error(data.message || 'Order failed');
      } else if (data?.status === 'ambiguous') {
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
      // HTTP callers (previewOrder / OrderEntryWidget) read the frame from the
      // invokeHttpAction envelope. WS dual-transport may still emit this type;
      // ignore here so we do not toast or overwrite local preview state.
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
    case MessageType.BACKTEST_PROGRESS: {
      const progressJobId = data?.job_id || null;
      const watchedId = watchedBacktestJobId();
      // Multi-job isolation: ignore foreign progress for the focused UI slot.
      if (progressJobId && watchedId && progressJobId !== watchedId) {
        storeActions.upsertBacktestJobSlot?.(progressJobId, {
          progress: data,
          status: data?.phase || 'running',
        });
        break;
      }
      if (progressJobId) storeActions.setBacktestJobId(progressJobId);
      storeActions.setBacktestProgress(data);
      if (data?.phase === 'queued' && progressJobId) {
        storeActions.setBacktestRunning(true);
        import('./endpoints').then(({ startBacktestJobPolling }) => {
          startBacktestJobPolling(progressJobId, storeActions);
        });
      }
      break;
    }
    case MessageType.ML_JOB_PROGRESS:
      import('@/lib/mlTrainingSession').then(({ applyMlJobProgressMessage }) => {
        applyMlJobProgressMessage(data);
      });
      break;
    case MessageType.BACKTEST_RESULT: {
      const resultJobId = data?.job_id || null;
      const watchedId = watchedBacktestJobId();
      if (resultJobId && watchedId && resultJobId !== watchedId) {
        storeActions.upsertBacktestJobSlot?.(resultJobId, {
          status: data?.status || 'completed',
          running: false,
        });
        break;
      }
      stopBacktestJobPolling();
      clearBacktestClientTimeout();
      storeActions.setBacktestRunning(false);
      storeActions.setBacktestProgress(null);
      if (resultJobId) {
        storeActions.setBacktestJobId(resultJobId);
        storeActions.upsertBacktestJobSlot?.(resultJobId, {
          status: data?.status || 'completed',
          running: false,
        });
      }
      if (data?.status === 'cancelled') {
        toast.info(data?.message || 'Backtest cancelled');
        break;
      }
      if (data?.status === 'success' && data?.results && !data.results.error) {
        storeActions.clearBacktestLastError?.();
        // Deferred jobs also complete via HTTP poll — claim once to avoid double toasts.
        const claimed = claimBacktestJobCompletion(data?.job_id);
        // MEMORY #24 — trim on worker thread when available.
        void trimBacktestPayloadAsync(data.results).then((results) => {
          storeBacktestResultsAware(storeActions, results);
          const overlay = buildBacktestOverlay(results);
          if (overlay) {
            storeActions.setBacktestOverlay(overlay);
          }
          const sym = results?.meta?.symbol;
          import('./endpoints').then(({ fetchBacktestRuns }) => {
            fetchBacktestRuns(storeActions, sym);
          });
          if (!claimed) return;

          const pnl = results?.total_pnl;
          const trades = results?.trade_count ?? 0;
          const explained = results?.reasoning?.trade_count
            ?? results?.reasoning?.trades?.length
            ?? 0;
          const pnlLabel = pnl != null
            ? `${pnl >= 0 ? '+' : ''}$${Number(pnl).toFixed(2)}`
            : '—';
          const explainSuffix = explained > 0 ? ` · ${explained} LLM explained` : '';
          const readiness = results?.strategy_readiness;
          const readinessBad = readiness && readiness.ok === false;
          const readinessMsg = readiness?.message
            || (Array.isArray(readiness?.warnings) ? readiness.warnings[0] : null);
          const rejectHint = Array.isArray(readiness?.warnings)
            ? readiness.warnings.find((w) => /Top reject reasons/i.test(String(w || '')))
            : null;
          const openLab = {
            label: 'Open Lab',
            onClick: () => useResearchStore.getState().openBacktestLab('results'),
          };
          if (readinessBad && readinessMsg) {
            toast.warning(`Backtest · 0 actionable trades — ${readinessMsg}`, {
              description: rejectHint || undefined,
              duration: 12_000,
              action: openLab,
            });
          } else if (results?.sweep) {
            const comboCount = results?.sweep?.configs?.length
              || results?.sweep?.sweep_rows?.length
              || '?';
            toast.success(
              `Sweep complete · best of ${comboCount} combos · ${pnlLabel} · ${trades} trade${trades !== 1 ? 's' : ''}`,
              { action: openLab },
            );
          } else {
            toast.success(`Backtest complete · ${pnlLabel} · ${trades} trade${trades !== 1 ? 's' : ''}${explainSuffix}`, {
              action: openLab,
            });
          }
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
    }
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
    case MessageType.COPILOT_AGENT_MESSAGE:
      if (data?.message) {
        const append =
          storeActions.appendCopilotMessage ||
          useStore.getState().appendCopilotMessage;
        append?.(data.message);
      }
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
