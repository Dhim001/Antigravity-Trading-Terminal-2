import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { agentInsightKey, normalizeAnalystTimeframe } from '../lib/agentInsights';
import {
  offloadBacktestFromMemory,
  resolveBacktestForLab,
  resolveBacktestForLabAsync,
} from '../services/backtestStorage';

/** Max scanner rows retained client-side. */
const MAX_SCAN_ROWS = 200;
/** Max backtest run list entries. */
const MAX_BACKTEST_RUNS = 20;
/** Max journal entries retained client-side. */
const MAX_JOURNAL_ENTRIES = 200;

/** Only apply IDB restore when store still holds the same offloaded run. */
function shouldApplyAsyncBacktestRestore(get, expectedRunId) {
  const cur = get().backtestResults;
  return Boolean(expectedRunId && cur?._offloaded && cur.run_id === expectedRunId);
}

function applyAsyncBacktestRestore(get, set, expectedRunId, restored) {
  if (!restored || !shouldApplyAsyncBacktestRestore(get, expectedRunId)) return;
  set({ backtestResults: restored });
}

/** Session miss returns the slim stub — async IDB restore is still required. */
function needsAsyncBacktestRestore(state, sync) {
  return Boolean(
    state.backtestResults?._offloaded
    && state.backtestResults.run_id
    && !(sync && sync !== state.backtestResults),
  );
}

function scheduleAsyncBacktestRestore(get, set, state, sync) {
  if (!needsAsyncBacktestRestore(state, sync)) return;
  const expectedRunId = state.backtestResults.run_id;
  resolveBacktestForLabAsync(state.backtestResults).then((restored) => {
    applyAsyncBacktestRestore(get, set, expectedRunId, restored);
  }).catch(() => {});
}

/**
 * Cold-path research state — backtests, agent insights, analytics, scanner.
 * Split from useStore so hot market tick subscriptions don't retain large trees.
 */
export const useResearchStore = create(subscribeWithSelector((set, get) => ({
  backtestResults: null,
  backtestRuns: [],
  backtestRunning: false,
  backtestProgress: null,
  backtestJobId: null,
  backtestLabOpen: false,
  backtestLabTab: 'results',
  backtestDays: '7',
  backtestOos: false,
  pendingDeploy: false,
  backtestSnapshot: null,
  backtestOverlay: null,
  backtestLastError: null,
  backtestLastRequest: null,
  optimizerPreset: null,

  agentInsights: {},
  agentInsightHistory: {},
  agentDeepReasoning: {},
  tradeExplains: {},
  scanResults: null,
  visionReports: {},
  analyticsReport: null,
  analyticsBenchmarks: null,
  analyticsLoading: false,
  journalEntries: [],

  setScanResults: (data) => set(() => {
    if (!data) return { scanResults: null };
    const rows = Array.isArray(data.rows) ? data.rows : [];
    if (rows.length <= MAX_SCAN_ROWS) return { scanResults: data };
    return {
      scanResults: {
        ...data,
        rows: rows.slice(0, MAX_SCAN_ROWS),
        rows_truncated: rows.length,
      },
    };
  }),

  setVisionReport: (key, report) => set((state) => {
    const slim = report && typeof report === 'object'
      ? (({ image_base64, image, ...rest }) => rest)(report)
      : report;
    const next = { ...state.visionReports, [key]: slim };
    const keys = Object.keys(next);
    if (keys.length > 10) {
      for (const k of keys.slice(0, keys.length - 10)) delete next[k];
    }
    return { visionReports: next };
  }),

  setAnalyticsReport: (data) => set((state) => {
    if (data?.report === 'benchmarks') {
      return { analyticsBenchmarks: data.benchmarks, analyticsLoading: false };
    }
    if (data?.report === 'dashboard') {
      return { analyticsReport: data, analyticsLoading: false };
    }
    return {
      analyticsReport: { ...(state.analyticsReport || {}), ...data },
      analyticsLoading: false,
    };
  }),
  setAnalyticsLoading: (loading) => set({ analyticsLoading: loading }),

  setJournalEntries: (entries) => set({
    journalEntries: Array.isArray(entries) ? entries.slice(0, MAX_JOURNAL_ENTRIES) : [],
  }),
  upsertJournalEntry: (entry) => set((state) => {
    const list = state.journalEntries.filter((e) => e.id !== entry.id);
    return { journalEntries: [entry, ...list].slice(0, MAX_JOURNAL_ENTRIES) };
  }),
  removeJournalEntry: (id) => set((state) => ({
    journalEntries: state.journalEntries.filter((e) => e.id !== id),
  })),

  setAgentDeepReasoning: (insightId, data) => set((state) => {
    const next = { ...state.agentDeepReasoning, [insightId]: data };
    const keys = Object.keys(next);
    if (keys.length > 20) {
      for (const k of keys.slice(0, keys.length - 20)) delete next[k];
    }
    return { agentDeepReasoning: next };
  }),

  setBacktestResults: (results) => set({ backtestResults: results }),
  setBacktestRuns: (runs) => set({
    backtestRuns: Array.isArray(runs) ? runs.slice(0, MAX_BACKTEST_RUNS) : [],
  }),
  setBacktestRunning: (running) => set({ backtestRunning: Boolean(running) }),
  setBacktestProgress: (progress) => set({ backtestProgress: progress ?? null }),
  setBacktestJobId: (jobId) => set({ backtestJobId: jobId ?? null }),

  setBacktestLabOpen: (open) => {
    const nextOpen = Boolean(open);
    const state = get();

    if (nextOpen && state.backtestResults?._offloaded && state.backtestResults.run_id) {
      const sync = resolveBacktestForLab(state.backtestResults);
      if (sync && sync !== state.backtestResults) {
        set({ backtestLabOpen: true, backtestResults: sync });
        return;
      }
      set({ backtestLabOpen: true });
      scheduleAsyncBacktestRestore(get, set, state, sync);
      return;
    }

    if (!nextOpen && state.backtestResults && !state.backtestResults._offloaded) {
      const slim = offloadBacktestFromMemory(state.backtestResults);
      set({
        backtestLabOpen: false,
        backtestResults: slim ?? state.backtestResults,
      });
      return;
    }

    set({ backtestLabOpen: nextOpen });
  },

  setBacktestLabTab: (tab) => set({
    backtestLabTab: ['results', 'optimizer', 'jobs'].includes(tab) ? tab : 'results',
  }),

  openBacktestLab: (tab = 'results') => {
    const validTab = ['results', 'optimizer', 'jobs'].includes(tab) ? tab : 'results';
    const state = get();
    const sync = state.backtestResults?._offloaded
      ? resolveBacktestForLab(state.backtestResults)
      : state.backtestResults;

    const patch = {
      backtestLabOpen: true,
      backtestLabTab: validTab,
    };
    if (sync && sync !== state.backtestResults) {
      patch.backtestResults = sync;
    }
    set(patch);
    scheduleAsyncBacktestRestore(get, set, state, sync);
  },

  setBacktestDays: (days) => set({ backtestDays: String(days ?? '7') }),
  setBacktestOos: (oos) => set({ backtestOos: Boolean(oos) }),
  setPendingDeploy: (pending) => set({ pendingDeploy: Boolean(pending) }),
  setBacktestSnapshot: (snapshot) => set({ backtestSnapshot: snapshot }),
  setBacktestLastError: (error, request) => set({
    backtestLastError: error ?? null,
    backtestLastRequest: request ?? null,
  }),
  clearBacktestLastError: () => set({ backtestLastError: null, backtestLastRequest: null }),
  setBacktestOverlay: (overlay) => set({ backtestOverlay: overlay }),
  setOptimizerPreset: (preset) => set({ optimizerPreset: preset ?? null }),
  clearOptimizerPreset: () => set({ optimizerPreset: null }),
  clearBacktestOverlay: () => set({ backtestOverlay: null }),

  setAgentInsight: (symbol, insight) => set((state) => {
    const sym = String(symbol || insight?.symbol || '').toUpperCase();
    const key = agentInsightKey(sym, insight?.timeframe || '1m');
    const history = state.agentInsightHistory[sym] ?? [];
    const id = insight?.insight_id;
    const nextHistory = id && history.some((h) => h.insight_id === id)
      ? history
      : insight
        ? [insight, ...history].slice(0, 20)
        : history;
    const nextInsights = { ...state.agentInsights, [key]: insight };
    if (normalizeAnalystTimeframe(insight?.timeframe) === '1m') {
      nextInsights[sym] = insight;
    }
    const iKeys = Object.keys(nextInsights);
    if (iKeys.length > 20) {
      for (const k of iKeys.slice(0, iKeys.length - 20)) delete nextInsights[k];
    }
    const nextHistoryMap = { ...state.agentInsightHistory, [sym]: nextHistory };
    const hKeys = Object.keys(nextHistoryMap);
    if (hKeys.length > 8) {
      for (const k of hKeys.slice(0, hKeys.length - 8)) delete nextHistoryMap[k];
    }
    return {
      agentInsights: nextInsights,
      agentInsightHistory: nextHistoryMap,
    };
  }),

  setAgentInsightHistory: (symbol, insights) => set((state) => ({
    agentInsightHistory: {
      ...state.agentInsightHistory,
      [symbol]: Array.isArray(insights) ? insights : [],
    },
  })),

  setTradeExplain: (tradeId, data) => set((state) => {
    const key = String(tradeId);
    const next = { ...state.tradeExplains, [key]: data };
    const keys = Object.keys(next);
    if (keys.length > 100) {
      for (const k of keys.slice(0, keys.length - 100)) delete next[k];
    }
    return { tradeExplains: next };
  }),
})));
