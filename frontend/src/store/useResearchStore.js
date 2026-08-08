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
/** Max concurrent backtest job slots tracked in the UI. */
const MAX_BACKTEST_JOB_SLOTS = 12;

/** #42 — analytics dashboard snapshots older than this are rebuilt from the
 * next partial report instead of merged into (bounds merge accumulation). */
const ANALYTICS_REPORT_TTL_MS = 30 * 60 * 1000;

/** Only apply IDB restore when store still holds the same offloaded run AND Lab is open. */
function shouldApplyAsyncBacktestRestore(get, expectedRunId) {
  const state = get();
  if (!state.backtestLabOpen) return false;
  const cur = state.backtestResults;
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
  /** Secondary job slots keyed by job_id — isolates progress from other clients/jobs. */
  backtestJobsById: {},
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
      return {
        analyticsReport: { ...data, _updatedAt: Date.now() },
        analyticsLoading: false,
      };
    }
    // Partial reports (risk, equity, …) merge into the existing dashboard
    // snapshot — do not demote report identity to the partial kind.
    // #42 — bound merge accumulation: if the previous snapshot is older than
    // the TTL, rebuild from the incoming partial instead of merging into it.
    const prev = state.analyticsReport || {};
    const stale = prev._updatedAt && (Date.now() - prev._updatedAt > ANALYTICS_REPORT_TTL_MS);
    const base = stale ? {} : prev;
    const next = { ...base, ...data, _updatedAt: Date.now() };
    if (base.report === 'dashboard' && data?.report && data.report !== 'dashboard') {
      next.report = 'dashboard';
    }
    return { analyticsReport: next, analyticsLoading: false };
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

  setBacktestResults: (results) => set((state) => (
    // #42 — snapshot shares the results lifecycle: clearing results clears it.
    results == null && state.backtestSnapshot != null
      ? { backtestResults: results, backtestSnapshot: null }
      : { backtestResults: results }
  )),
  setBacktestRuns: (runs) => set({
    backtestRuns: Array.isArray(runs) ? runs.slice(0, MAX_BACKTEST_RUNS) : [],
  }),
  setBacktestRunning: (running) => set({ backtestRunning: Boolean(running) }),
  setBacktestProgress: (progress) => set((state) => {
    const next = progress ?? null;
    const jobId = next?.job_id || state.backtestJobId;
    if (!jobId) return { backtestProgress: next };
    const prev = state.backtestJobsById[jobId] || {};
    // Keep slot.status as a lifecycle value so client-timeout guards do not
    // treat missing status as "dead" and wipe a still-running deferred job.
    const prevStatus = String(prev.status || '').toLowerCase();
    const terminal = ['completed', 'failed', 'cancelled', 'timeout', 'error'].includes(prevStatus);
    const lifecycleOk = prevStatus === 'pending' || prevStatus === 'running';
    const slot = {
      ...prev,
      jobId,
      progress: next,
      running: terminal ? Boolean(prev.running) : true,
      status: terminal ? prev.status : (lifecycleOk ? prev.status : 'running'),
      updatedAt: Date.now(),
    };
    const byId = { ...state.backtestJobsById, [jobId]: slot };
    const ids = Object.keys(byId);
    if (ids.length > MAX_BACKTEST_JOB_SLOTS) {
      const ranked = ids
        .map((id) => ({ id, t: byId[id]?.updatedAt || 0 }))
        .sort((a, b) => a.t - b.t);
      for (const { id } of ranked.slice(0, ids.length - MAX_BACKTEST_JOB_SLOTS)) {
        if (id !== state.backtestJobId) delete byId[id];
      }
    }
    const watched = !state.backtestJobId || state.backtestJobId === jobId;
    return {
      backtestJobsById: byId,
      ...(watched ? { backtestProgress: next } : {}),
    };
  }),
  /**
   * Release the watched job slot so the next run's job_id is adopted.
   * Without this a new run's progress looks "foreign" and is filtered out,
   * and Cancel would target the previous run's job_id.
   */
  beginBacktestRun: () => set({ backtestJobId: null }),

  setBacktestJobId: (jobId) => set((state) => {
    const id = jobId ?? null;
    if (!id) return { backtestJobId: null };
    const prev = state.backtestJobsById[id] || {};
    return {
      backtestJobId: id,
      backtestJobsById: {
        ...state.backtestJobsById,
        [id]: { ...prev, jobId: id, updatedAt: Date.now() },
      },
    };
  }),
  /** Upsert a job slot; only mirrors into primary progress when job is watched. */
  upsertBacktestJobSlot: (jobId, patch = {}) => set((state) => {
    if (!jobId) return {};
    const prev = state.backtestJobsById[jobId] || {};
    const slot = {
      ...prev,
      ...patch,
      jobId,
      updatedAt: Date.now(),
    };
    const byId = { ...state.backtestJobsById, [jobId]: slot };
    const watched = !state.backtestJobId || state.backtestJobId === jobId;
    const out = { backtestJobsById: byId };
    if (watched) {
      if (patch.progress !== undefined) out.backtestProgress = patch.progress ?? null;
      if (patch.running !== undefined) out.backtestRunning = Boolean(patch.running);
    }
    return out;
  }),

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

  /**
   * Clear in-memory ML backtest results when a matching model is retrained / activated.
   * Leaves backtestRuns history intact.
   */
  invalidateMlBacktests: ({ strategy, symbol } = {}) => {
    const strat = String(strategy || '').toUpperCase();
    const sym = String(symbol || '').toUpperCase();
    if (!strat || !sym) return false;
    const state = get();
    const meta = state.backtestResults?.meta || {};
    const resultStrat = String(meta.strategy || state.backtestResults?.strategy || '').toUpperCase();
    const resultSym = String(meta.symbol || state.backtestResults?.symbol || '').toUpperCase();
    if (!state.backtestResults) return false;
    if (resultStrat && resultStrat !== strat) return false;
    if (resultSym && resultSym !== sym) return false;
    // If meta lacked identity, only clear when both were empty (avoid wiping TA).
    if (!resultStrat && !resultSym) return false;
    set({
      backtestResults: null,
      backtestSnapshot: null,
      backtestOverlay: null,
      backtestProgress: null,
    });
    return true;
  },

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

  setAgentInsightHistory: (symbol, insights) => set((state) => {
    // MEMORY_CENTRIC_REVIEW #39 — wholesale replace must respect the same caps
    // as setAgentInsight: 20 entries per symbol (newest first), 8 symbols max.
    const sym = String(symbol || '').toUpperCase();
    if (!sym) return {};
    const list = Array.isArray(insights) ? insights.slice(0, 20) : [];
    const nextHistoryMap = { ...state.agentInsightHistory, [sym]: list };
    const hKeys = Object.keys(nextHistoryMap);
    if (hKeys.length > 8) {
      for (const k of hKeys.slice(0, hKeys.length - 8)) delete nextHistoryMap[k];
    }
    return { agentInsightHistory: nextHistoryMap };
  }),

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
