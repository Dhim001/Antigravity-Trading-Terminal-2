import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { Action } from '../api/protocol';
import {
  setCandleHistory, applyLiveCandle, hasCandleHistory, mergeCandleHistory,
  applyLivePrice,
  prependCandleHistory, CHART_SNAPSHOT_BARS, candleBufferKey, chartTimeframeSecs,
  resolveHistoryTimeframe, setPinnedCandleSymbol, initCandleBufferCache,
  onCandleBufferEvict,
} from '../services/candleBuffer';
import { isLiveMassiveMode } from '../lib/massiveMarket';
import {
  hydrateFromSnapshot, scheduleMarketSnapshotSave, forceMarketSnapshotSave,
} from '../services/marketSnapshot';
import { emitLivePrice } from '../services/livePriceChannel';
import { getHmrData } from '../services/hmrState';
import { agentInsightKey, normalizeAnalystTimeframe } from '../lib/agentInsights';
import { normalizeBotLogEntry } from '../lib/botLogInsight';

const initialSnapshot = hydrateFromSnapshot();
const hmrStore = getHmrData()?.zustandSnapshot;

const getLocal = (key, fallback) => {
  try {
    const val = localStorage.getItem(key);
    return val !== null ? JSON.parse(val) : fallback;
  } catch (_) {
    return fallback;
  }
};

const setLocal = (key, val) => {
  try {
    localStorage.setItem(key, JSON.stringify(val));
  } catch (_) {}
};

/** Memory-efficient revision bump — mutates a shallow clone only when needed.
 *  Prunes keys that exceed MAX_REVISION_KEYS to prevent unbounded growth. */
const MAX_REVISION_KEYS = 30;
export function bumpRevision(revisions, symbol) {
  const next = { ...revisions, [symbol]: (revisions[symbol] || 0) + 1 };
  const keys = Object.keys(next);
  if (keys.length > MAX_REVISION_KEYS) {
    // Drop oldest keys (first inserted — JS object key order is insertion-order)
    for (const k of keys.slice(0, keys.length - MAX_REVISION_KEYS)) {
      delete next[k];
    }
  }
  return next;
}

export const useStore = create(subscribeWithSelector((set, get) => ({
  connectionStatus: 'disconnected',
  /** HTTP bootstrap: idle | loading | ready | error */
  apiStatus: hmrStore?.apiStatus ?? 'idle',
  activeSymbol: getLocal('terminal_active_symbol', 'BTCUSDT'),
  viewMode: getLocal('terminal_view_mode', 'single'),
  terminalMode: 'SIMULATED',
  isOperator: false,
  orderCapabilities: null,
  terminalRole: 'all',
  distributed: false,
  executionMode: 'broker',
  allowLiveBots: false,
  allowCustomStrategies: false,
  archiveParquetEnabled: false,
  archiveBackend: 'db',
  workerAlive: null,
  workerHeartbeatAge: null,
  botMinCandles: 200,
  archiveTicksEnabled: false,
  ambiguousOrders: [],
  agentLlmEnabled: false,
  agentLlmAvailable: false,
  agentLlmProvider: 'off',
  agentLlmModel: null,
  agentLlmModels: [],
  agentVisionEnabled: false,
  agentEnabled: true,
  scannerEnabled: true,
  selectedLlmModel: getLocal('terminal_llm_model', null),
  symbolsList: ["BTCUSDT", "ETHUSDT", "AAPL", "TSLA", "MSFT"],

  // Market data states
  tickerData: hmrStore?.tickerData ?? initialSnapshot.tickerData ?? {},
  priceDirections: hmrStore?.priceDirections ?? initialSnapshot.priceDirections ?? {},
  orderBooks: hmrStore?.orderBooks ?? {},
  /** Bumped on live ticks — drives incremental chart patches */
  candleRevision: hmrStore?.candleRevision ?? initialSnapshot.candleRevision ?? {},
  /** Bumped only on history load — drives full chart rebuild */
  candleHistoryRevision: hmrStore?.candleHistoryRevision ?? initialSnapshot.candleHistoryRevision ?? {},

  // Account states
  balances: {},
  positions: {},
  orders: [],
  orderResult: null,

  tradeHistory: [],
  tradeStats: null,

  systemStats: { clients: 1, positions_count: 0, pending_orders_count: 0, filled_trades_count: 0, tick_interval: 0.25, volatility_multiplier: 1.0 },

  activeBots: [],
  isBotRunning: false,
  botStrategy: getLocal('terminal_bot_strategy', 'MACD_RSI'),
  botExecutionMode: getLocal('terminal_bot_execution_mode', 'BAR_CLOSE'),
  botTimeframe: getLocal('terminal_bot_timeframe', '1m'),
  botConfig: getLocal('terminal_bot_config', {
    allocation: 1000,
    trailing_stop_percent: 2,
    take_profit_percent: 3,
    tp_mode: 'percent',
    direction_mode: 'LONG_ONLY',
  }),
  botLogs: [],

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
  /** Fingerprint of params used for the last completed backtest run */
  backtestSnapshot: null,
  backtestOverlay: null,
  backtestLastError: null,
  backtestLastRequest: null,
  /** One-shot optimizer UI preset from workflow chips (Tier 4) */
  optimizerPreset: null,
  chartInteractionMode: 'normal',
  strategyTemplates: [
    { id: 't1', name: 'Bull Market Scalper', strategy: 'MACD_RSI', execution_mode: 'BAR_CLOSE', allocation: 2000, config: { rsi_length: 14, macd_fast: 12, macd_slow: 26, trailing_stop_percent: 1.5, take_profit_percent: 3, tp_mode: 'percent' } },
    { id: 't2', name: 'Trend Follower', strategy: 'SUPERTREND_ADX', execution_mode: 'BAR_CLOSE', allocation: 5000, config: { st_length: 14, st_multiplier: 3, trailing_stop_percent: 3, take_profit_percent: 4, tp_mode: 'percent' } },
    { id: 't3', name: 'Mean Reversion Scalp', strategy: 'BRS_SCALPING', execution_mode: 'BAR_CLOSE', allocation: 1000, config: { bb_length: 20, bb_std: 2, trailing_stop_percent: 1, tp_mode: 'strategy' } },
    { id: 't4', name: 'VWAP Pullback', strategy: 'VWAP_PULLBACK', execution_mode: 'BAR_CLOSE', allocation: 1500, config: { trailing_stop_percent: 2, take_profit_percent: 2.5, tp_mode: 'percent' } },
    { id: 't5', name: 'Tick Momentum', strategy: 'TICK_MOMENTUM', execution_mode: 'TICK', allocation: 1000, config: { lookback_ticks: 20, tick_cooldown_sec: 10, take_profit_percent: 0.2, tp_mode: 'percent' } },
    { id: 't6', name: 'Tick Mean Revert', strategy: 'TICK_MEAN_REVERT', execution_mode: 'TICK', allocation: 1000, config: { lookback_ticks: 30, tick_cooldown_sec: 15, take_profit_percent: 0.15, tp_mode: 'percent' } },
    { id: 't7', name: 'Chart Analyst Agent', strategy: 'CHART_AGENT', execution_mode: 'BAR_CLOSE', allocation: 2000, config: { min_confidence: 0.55, use_llm: false, trailing_stop_percent: 2, take_profit_percent: 3, tp_mode: 'percent', direction_mode: 'BOTH' } },
    { id: 't8', name: 'ICT Smart Money', strategy: 'ICT_SMC', execution_mode: 'BAR_CLOSE', allocation: 2000, config: { ob_lookback: 10, fvg_min_gap_pct: 0.0005, sweep_lookback: 20, trailing_stop_percent: 2, take_profit_percent: 3, tp_mode: 'percent', direction_mode: 'BOTH' } },
    { id: 't9', name: 'Donchian Breakout', strategy: 'DONCHIAN_BREAKOUT', execution_mode: 'BAR_CLOSE', allocation: 3000, config: { breakout_length: 20, exit_length: 10, atr_confirm_mult: 1.0, trailing_stop_percent: 3, take_profit_percent: 4, tp_mode: 'percent', direction_mode: 'BOTH' } },
    { id: 't10', name: 'Market Maker', strategy: 'MARKET_MAKING', execution_mode: 'BAR_CLOSE', allocation: 5000, config: { spread_pct: 0.002, max_skew: 0.5, vol_shutdown_mult: 2.5, inventory_target: 0, trailing_stop_percent: 1, tp_mode: 'none', direction_mode: 'BOTH' } },
  ],
  selectedBotId: null,
  botDetail: null,
  botDrawerOpen: false,
  botHistory: [],
  tickData: {},
  tickMeta: null,
  agentInsights: {},
  agentInsightHistory: {},
  tradeExplains: {},
  scanResults: null,
  visionReports: {},
  chartDrawings: {},
  analyticsReport: null,
  analyticsBenchmarks: null,
  analyticsLoading: false,
  journalEntries: [],
  orderPrefill: null,
  /** Draft SL/TP dragged on chart or mirrored from order ticket — { symbol, side, stop_loss_price?, take_profit_price?, source } */
  chartSlTpDraft: null,

  setScanResults: (data) => set({ scanResults: data }),
  setVisionReport: (key, report) => set((state) => {
    const next = { ...state.visionReports, [key]: report };
    // FIX 1: Cap to 10 entries — vision reports contain large base64 images
    const keys = Object.keys(next);
    if (keys.length > 10) {
      for (const k of keys.slice(0, keys.length - 10)) delete next[k];
    }
    return { visionReports: next };
  }),
  setChartDrawings: (symbol, drawings) => set((state) => ({
    chartDrawings: { ...state.chartDrawings, [symbol]: drawings },
  })),
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
  setJournalEntries: (entries) => set({ journalEntries: Array.isArray(entries) ? entries : [] }),
  upsertJournalEntry: (entry) => set((state) => {
    const list = state.journalEntries.filter((e) => e.id !== entry.id);
    return { journalEntries: [entry, ...list] };
  }),
  removeJournalEntry: (id) => set((state) => ({
    journalEntries: state.journalEntries.filter((e) => e.id !== id),
  })),

  setOrderPrefill: (prefill) => set({ orderPrefill: prefill }),
  clearOrderPrefill: () => set({ orderPrefill: null }),
  setChartSlTpDraft: (draft) => set({ chartSlTpDraft: draft }),
  clearChartSlTpDraft: () => set({ chartSlTpDraft: null }),

  setConnectionStatus: (status) => set({ connectionStatus: status }),
  setApiStatus: (status) => set({ apiStatus: status }),

  setActiveSymbol: (symbol) => {
    setPinnedCandleSymbol(symbol);
    setLocal('terminal_active_symbol', symbol);
    set({ activeSymbol: symbol });
    import('../api/transport').then(({ sendAction }) => {
      sendAction(Action.SUBSCRIBE_SYMBOL, { symbol, limit: CHART_SNAPSHOT_BARS });
    });
  },

  setViewMode: (mode) => {
    setLocal('terminal_view_mode', mode);
    set({ viewMode: mode });
  },

  updateHistory: (historyData, meta) => {
    set((state) => {
      let candleRevision = state.candleRevision;
      let candleHistoryRevision = state.candleHistoryRevision;
      let anyChange = false;
      const tf = resolveHistoryTimeframe(meta);
      const intervalSecs = chartTimeframeSecs(tf);

      for (const [symbol, candles] of Object.entries(historyData)) {
        const key = candleBufferKey(symbol, tf);
        const { changed, fullRebuild } = mergeCandleHistory(symbol, candles, tf, intervalSecs);
        if (!changed) continue;
        anyChange = true;
        candleRevision = bumpRevision(candleRevision, key);
        if (fullRebuild) {
          candleHistoryRevision = bumpRevision(candleHistoryRevision, key);
        }
      }

      if (!anyChange) return {};
      return { candleRevision, candleHistoryRevision };
    });
    scheduleMarketSnapshotSave(get);
  },

  prependHistory: (historyData) => {
    set((state) => {
      let candleHistoryRevision = state.candleHistoryRevision;
      let anyChange = false;

      for (const [symbol, candles] of Object.entries(historyData)) {
        const { changed } = prependCandleHistory(symbol, candles);
        if (!changed) continue;
        anyChange = true;
        candleHistoryRevision = bumpRevision(candleHistoryRevision, symbol);
      }

      if (!anyChange) return {};
      return { candleHistoryRevision };
    });
    scheduleMarketSnapshotSave(get);
  },

  updateAccount: (accountData) => set({
    balances: accountData.balances || {},
    positions: accountData.positions || {},
    orders: accountData.orders || [],
  }),

  setTradeHistory: (data) => {
    const rawTrades = Array.isArray(data) ? data : (data.trades || []);
    const trades = rawTrades.map(t => {
      let ts = t.timestamp;
      if (typeof ts === 'number') {
        ts = ts < 10000000000 ? ts * 1000 : ts;
      } else if (typeof ts === 'string' && ts) {
        const parsed = new Date(ts).getTime();
        ts = isNaN(parsed) ? Date.now() : parsed;
      } else {
        ts = Date.now();
      }
      return { ...t, timestamp: ts };
    });

    const filledTrades = trades.filter(t => t.status === 'FILLED');
    const sellsWithPnl = filledTrades.filter(t => t.side === 'SELL' && t.realized_pnl != null);

    const winsList = sellsWithPnl.filter(t => t.realized_pnl > 0);
    const lossesList = sellsWithPnl.filter(t => t.realized_pnl < 0);

    const wins = winsList.length;
    const losses = lossesList.length;
    const total_sells = sellsWithPnl.length;
    const win_rate = total_sells > 0 ? (wins / total_sells) * 100 : 0.0;

    const total_pnl = sellsWithPnl.reduce((sum, t) => sum + t.realized_pnl, 0);

    const totalWinPnl = winsList.reduce((sum, t) => sum + t.realized_pnl, 0);
    const totalLossPnl = lossesList.reduce((sum, t) => sum + t.realized_pnl, 0);
    const profit_factor = Math.abs(totalLossPnl) > 0 ? (totalWinPnl / Math.abs(totalLossPnl)) : (totalWinPnl > 0 ? 99.9 : 0.0);

    const best_trade = sellsWithPnl.reduce((max, t) => t.realized_pnl > max ? t.realized_pnl : max, 0.0);
    const worst_trade = sellsWithPnl.reduce((min, t) => t.realized_pnl < min ? t.realized_pnl : min, 0.0);

    const avg_win = wins > 0 ? totalWinPnl / wins : 0.0;
    const avg_loss = losses > 0 ? totalLossPnl / losses : 0.0;

    const total_fills = filledTrades.length;
    const gross_volume = filledTrades.reduce((sum, t) => sum + (t.trade_value || 0), 0);

    set({
      tradeHistory: trades,
      tradeStats: {
        total_pnl,
        wins,
        losses,
        total_sells,
        win_rate,
        profit_factor,
        best_trade,
        worst_trade,
        avg_win,
        avg_loss,
        total_fills,
        gross_volume,
      },
    });
  },

  setSystemStats: (stats) => set({ systemStats: stats }),

  setTerminalMode: (mode) => set({ terminalMode: mode, isLive: mode !== 'SIMULATED' }),

  setTerminalConfig: ({ terminalMode, executionMode, allowLiveBots, allowCustomStrategies, symbols, terminalRole, distributed, botMinCandles, archiveTicksEnabled, archiveParquetEnabled, archiveBackend, workerAlive, workerHeartbeatAge, agentLlmEnabled, agentLlmAvailable, agentLlmProvider, agentLlmModel, agentLlmModels, agentVisionEnabled, agentEnabled, scannerEnabled, orderCapabilities, isOperator }) => set((state) => ({
    terminalMode: terminalMode ?? state.terminalMode,
    executionMode: executionMode ?? state.executionMode,
    isLive: (terminalMode ?? state.terminalMode) !== 'SIMULATED',
    orderCapabilities: orderCapabilities ?? state.orderCapabilities,
    isOperator: isOperator !== undefined ? isOperator : state.isOperator,
    allowLiveBots: allowLiveBots ?? state.allowLiveBots,
    allowCustomStrategies: allowCustomStrategies ?? state.allowCustomStrategies,
    terminalRole: terminalRole ?? state.terminalRole,
    distributed: distributed ?? state.distributed,
    botMinCandles: botMinCandles ?? state.botMinCandles,
    archiveTicksEnabled: archiveTicksEnabled ?? state.archiveTicksEnabled,
    archiveParquetEnabled: archiveParquetEnabled ?? state.archiveParquetEnabled,
    archiveBackend: archiveBackend ?? state.archiveBackend,
    workerAlive: workerAlive !== undefined ? workerAlive : state.workerAlive,
    workerHeartbeatAge: workerHeartbeatAge !== undefined ? workerHeartbeatAge : state.workerHeartbeatAge,
    agentLlmEnabled: agentLlmEnabled ?? state.agentLlmEnabled,
    agentLlmAvailable: agentLlmAvailable ?? state.agentLlmAvailable,
    agentLlmProvider: agentLlmProvider ?? state.agentLlmProvider,
    agentLlmModel: agentLlmModel ?? state.agentLlmModel,
    agentLlmModels: agentLlmModels ?? state.agentLlmModels,
    agentVisionEnabled: agentVisionEnabled ?? state.agentVisionEnabled,
    agentEnabled: agentEnabled ?? state.agentEnabled,
    scannerEnabled: scannerEnabled ?? state.scannerEnabled,
    ...(Array.isArray(symbols) ? { symbolsList: symbols } : {}),
  })),

  setSelectedLlmModel: (model) => {
    setLocal('terminal_llm_model', model);
    set({ selectedLlmModel: model });
  },

  agentDeepReasoning: {},
  setAgentDeepReasoning: (insightId, data) => set((state) => {
    const next = { ...state.agentDeepReasoning, [insightId]: data };
    // FIX 2: Cap to 20 entries — LLM reasoning text accumulates
    const keys = Object.keys(next);
    if (keys.length > 20) {
      for (const k of keys.slice(0, keys.length - 20)) delete next[k];
    }
    return { agentDeepReasoning: next };
  }),

  setAmbiguousOrders: (orders) => set({ ambiguousOrders: Array.isArray(orders) ? orders : [] }),

  setSymbolsList: (list) => set({ symbolsList: list }),

  setBots: (bots) => set({
    activeBots: bots,
    isBotRunning: Array.isArray(bots) && bots.some((b) => b.status === 'RUNNING'),
  }),
  setBotStrategy: (strategy) => {
    setLocal('terminal_bot_strategy', strategy);
    set({ botStrategy: strategy });
  },
  setBotExecutionMode: (mode) => {
    const normalized = mode === 'TICK' ? 'TICK' : 'BAR_CLOSE';
    setLocal('terminal_bot_execution_mode', normalized);
    set({ botExecutionMode: normalized });
  },
  setBotTimeframe: (timeframe) => {
    setLocal('terminal_bot_timeframe', timeframe);
    set({ botTimeframe: timeframe });
  },
  updateBotConfig: (config) => set((state) => {
    const next = { ...state.botConfig, ...config };
    setLocal('terminal_bot_config', next);
    return { botConfig: next };
  }),
  addBotLog: (log) => set((state) => {
    const entry = normalizeBotLogEntry(log, Date.now());
    const newLogs = [entry, ...state.botLogs];
    if (newLogs.length > 100) newLogs.pop();
    return { botLogs: newLogs };
  }),
  setBotLogs: (logsArray) => set({
    botLogs: [...logsArray]
      .sort((a, b) => {
        const ta = a.timestamp ?? 0;
        const tb = b.timestamp ?? 0;
        const toMs = (v) => (typeof v === 'string' ? new Date(v).getTime() : Number(v) || 0);
        return toMs(tb) - toMs(ta);
      })
      .map((log, i) => normalizeBotLogEntry(log, i)),
  }),
  clearBotLogs: () => set({ botLogs: [] }),

  setBacktestResults: (results) => set({ backtestResults: results }),
  setBacktestRuns: (runs) => set({ backtestRuns: Array.isArray(runs) ? runs : [] }),
  setBacktestRunning: (running) => set({ backtestRunning: Boolean(running) }),
  setBacktestProgress: (progress) => set({ backtestProgress: progress ?? null }),
  setBacktestJobId: (jobId) => set({ backtestJobId: jobId ?? null }),
  setBacktestLabOpen: (open) => set({ backtestLabOpen: Boolean(open) }),
  setBacktestLabTab: (tab) => set({
    backtestLabTab: ['results', 'optimizer', 'jobs'].includes(tab) ? tab : 'results',
  }),
  openBacktestLab: (tab = 'results') => set({
    backtestLabOpen: true,
    backtestLabTab: ['results', 'optimizer', 'jobs'].includes(tab) ? tab : 'results',
  }),
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

  setStrategyCatalog: (strategies) => {
    if (!Array.isArray(strategies) || strategies.length === 0) return;
    const templates = strategies
      .filter(s => !s.custom)
      .map((s) => ({
        id: `catalog-${s.id}`,
        name: s.name,
        strategy: s.id,
        execution_mode: s.execution_mode || 'BAR_CLOSE',
        allocation: 1000,
        config: { ...(s.defaults || {}), allocation: 1000 },
      }));
    if (templates.length > 0) {
      set({ strategyTemplates: templates });
    }
  },
  setChartInteractionMode: (mode) => set({ chartInteractionMode: mode }),
  setSelectedBotId: (id) => set({ selectedBotId: id }),
  setBotDetail: (detail) => set({ botDetail: detail }),
  setBotDrawerOpen: (open) => set({ botDrawerOpen: !!open }),
  setBotHistory: (bots) => set({ botHistory: Array.isArray(bots) ? bots : [] }),

  setAgentInsight: (symbol, insight) => set((state) => {
    const sym = String(symbol || insight?.symbol || '').toUpperCase();
    const key = agentInsightKey(sym, insight?.timeframe || '1m');
    const history = state.agentInsightHistory[sym] ?? [];
    const id = insight?.insight_id;
    const nextHistory = id && history.some((h) => h.insight_id === id)
      ? history
      : insight
        ? [insight, ...history].slice(0, 50)
        : history;
    const nextInsights = { ...state.agentInsights, [key]: insight };
    // Legacy symbol-only key for 1m consumers not yet migrated
    if (normalizeAnalystTimeframe(insight?.timeframe) === '1m') {
      nextInsights[sym] = insight;
    }
    // FIX 4: Cap agentInsights to 30 keys
    const iKeys = Object.keys(nextInsights);
    if (iKeys.length > 30) {
      for (const k of iKeys.slice(0, iKeys.length - 30)) delete nextInsights[k];
    }
    // Cap agentInsightHistory to 15 symbols
    const nextHistoryMap = { ...state.agentInsightHistory, [sym]: nextHistory };
    const hKeys = Object.keys(nextHistoryMap);
    if (hKeys.length > 15) {
      for (const k of hKeys.slice(0, hKeys.length - 15)) delete nextHistoryMap[k];
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
  setTickData: (data, meta) => set({
    tickData: data && typeof data === 'object' ? { ...data } : {},
    tickMeta: meta ?? null,
  }),

  setOrderResult: (result) => {
    set({ orderResult: result });
    setTimeout(() => {
      set((state) => (state.orderResult === result ? { orderResult: null } : {}));
    }, 4000);
  },

  updateOrderBooks: (orderBookData) => set((state) => ({
    orderBooks: { ...state.orderBooks, ...orderBookData },
  })),

  updateMarketData: (marketData) => {
    set((state) => {
      const tickerData = state.tickerData;
      const priceDirections = state.priceDirections;
      const massive = isLiveMassiveMode(state.terminalMode);
      let candleRevision = null;
      let candleHistoryRevision = null;
      let tickerChanged = false;
      let directionChanged = false;
      let orderBooksChanged = false;
      let nextTickers = tickerData;
      let nextDirections = priceDirections;
      let nextOrderBooks = state.orderBooks;

      for (const [symbol, info] of Object.entries(marketData)) {
        if (!info) continue;

        if (info.orderbook?.bids?.length && info.orderbook?.asks?.length) {
          const prevOb = state.orderBooks[symbol];
          if (prevOb !== info.orderbook) {
            if (!orderBooksChanged) {
              nextOrderBooks = { ...state.orderBooks };
              orderBooksChanged = true;
            }
            nextOrderBooks[symbol] = info.orderbook;
          }
        }

        const prev = tickerData[symbol];
        if (
          !prev
          || prev.price !== info.price
          || prev.change_24h !== info.change_24h
          || prev.volume_24h !== info.volume_24h
          || prev.high_24h !== info.high_24h
          || prev.low_24h !== info.low_24h
        ) {
          if (!tickerChanged) {
            nextTickers = { ...tickerData };
            tickerChanged = true;
          }
          nextTickers[symbol] = {
            price: info.price,
            change_24h: info.change_24h,
            volume_24h: info.volume_24h,
            high_24h: info.high_24h,
            low_24h: info.low_24h,
          };
        }

        if (info.price !== undefined) {
          const oldPrice = prev?.price;
          let dir = priceDirections[symbol];
          if (oldPrice !== undefined && oldPrice !== info.price) {
            dir = info.price > oldPrice ? 'up' : 'down';
          } else if (oldPrice === undefined) {
            dir = 'flat';
          }
          if (dir !== priceDirections[symbol]) {
            if (!directionChanged) {
              nextDirections = { ...priceDirections };
              directionChanged = true;
            }
            nextDirections[symbol] = dir;
          }
        }

        if (info.candle && !hasCandleHistory(symbol)) {
          setCandleHistory(symbol, [info.candle]);
          candleRevision = bumpRevision(candleRevision ?? state.candleRevision, symbol);
          candleHistoryRevision = bumpRevision(
            candleHistoryRevision ?? state.candleHistoryRevision,
            symbol,
          );
        } else if (hasCandleHistory(symbol)) {
          if (info.candle && applyLiveCandle(symbol, info.candle)) {
            candleRevision = bumpRevision(candleRevision ?? state.candleRevision, symbol);
          }
          if (info.price !== undefined) {
            const priceMoved = prev?.price === undefined || prev.price !== info.price;
            if (priceMoved) {
              const keys = applyLivePrice(symbol, info.price);
              if (massive) {
                emitLivePrice(symbol, info.price);
              } else {
                for (const key of keys) {
                  candleRevision = bumpRevision(candleRevision ?? state.candleRevision, key);
                }
              }
            }
          }
        }
      }

      const updates = {};
      if (tickerChanged) updates.tickerData = nextTickers;
      if (directionChanged) updates.priceDirections = nextDirections;
      if (orderBooksChanged) updates.orderBooks = nextOrderBooks;
      if (candleRevision) updates.candleRevision = candleRevision;
      if (candleHistoryRevision) updates.candleHistoryRevision = candleHistoryRevision;

      if (candleRevision || candleHistoryRevision) {
        scheduleMarketSnapshotSave(get);
      }

      return Object.keys(updates).length ? updates : {};
    });
  },
})));

initCandleBufferCache(getLocal('terminal_active_symbol', 'BTCUSDT'));

onCandleBufferEvict((symbol) => {
  useStore.setState((state) => {
    // FIX 5: Prune orderBooks and tickerData for evicted symbols
    const nextOrderBooks = { ...state.orderBooks };
    delete nextOrderBooks[symbol];
    return {
      candleRevision: bumpRevision(state.candleRevision, symbol),
      candleHistoryRevision: bumpRevision(state.candleHistoryRevision, symbol),
      orderBooks: nextOrderBooks,
    };
  });
});

if (import.meta.hot) {
  import.meta.hot.dispose((data) => {
    const s = useStore.getState();
    data.zustandSnapshot = {
      tickerData: s.tickerData,
      priceDirections: s.priceDirections,
      orderBooks: s.orderBooks,
      candleRevision: s.candleRevision,
      candleHistoryRevision: s.candleHistoryRevision,
      apiStatus: s.apiStatus,
    };
    forceMarketSnapshotSave(() => useStore.getState());
  });
}
