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
import { normalizeBotLogEntry } from '../lib/botLogInsight';
import {
  bumpLiveRevision,
  bumpHistoryRevision,
  clearRevisionsForKey,
  seedRevisions,
  snapshotRevisions,
} from '../services/candleRevisions';
import { isOrderBookRetentionEnabled } from '../services/orderBookInterest';

const initialSnapshot = hydrateFromSnapshot();
const hmrRev = getHmrData()?.zustandSnapshot;
if (initialSnapshot.candleRevision || initialSnapshot.candleHistoryRevision) {
  seedRevisions(initialSnapshot.candleRevision, initialSnapshot.candleHistoryRevision);
} else if (hmrRev?.candleRevision || hmrRev?.candleHistoryRevision) {
  seedRevisions(hmrRev.candleRevision, hmrRev.candleHistoryRevision);
}
const hmrStore = hmrRev;

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

/** Max symbols to keep orderBook data for (active + recently viewed). */
const MAX_ORDERBOOK_SYMBOLS = 6;
/** Max trade history entries retained client-side. */
const MAX_TRADE_HISTORY = 500;
/** Max symbols with chart drawings. */
const MAX_CHART_DRAWING_SYMBOLS = 8;
/** Max drawing primitives per symbol. */
const MAX_DRAWINGS_PER_SYMBOL = 200;

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
  symbolsList: [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT",
    "TRXUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
    "AAPL", "TSLA", "MSFT", "NVDA", "SPY",
  ],

  // Market data states
  tickerData: hmrStore?.tickerData ?? initialSnapshot.tickerData ?? {},
  priceDirections: hmrStore?.priceDirections ?? initialSnapshot.priceDirections ?? {},
  orderBooks: hmrStore?.orderBooks ?? {},

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

  chartInteractionMode: 'normal',
  strategyTemplates: [
    { id: 't1', name: 'Bull Market Scalper', strategy: 'MACD_RSI', category: 'trend', execution_mode: 'BAR_CLOSE', allocation: 2000, config: { rsi_length: 14, macd_fast: 12, macd_slow: 26, trailing_stop_percent: 1.5, take_profit_percent: 3, tp_mode: 'percent' } },
    { id: 't2', name: 'Trend Follower', strategy: 'SUPERTREND_ADX', category: 'trend', execution_mode: 'BAR_CLOSE', allocation: 5000, config: { st_length: 14, st_multiplier: 3, trailing_stop_percent: 3, take_profit_percent: 4, tp_mode: 'percent' } },
    { id: 't3', name: 'Mean Reversion Scalp', strategy: 'BRS_SCALPING', category: 'scalp', execution_mode: 'BAR_CLOSE', allocation: 1000, config: { bb_length: 20, bb_std: 2, trailing_stop_percent: 1, tp_mode: 'strategy' } },
    { id: 't4', name: 'VWAP Pullback', strategy: 'VWAP_PULLBACK', category: 'intraday', execution_mode: 'BAR_CLOSE', allocation: 1500, config: { trailing_stop_percent: 2, take_profit_percent: 2.5, tp_mode: 'percent' } },
    { id: 't5', name: 'Tick Momentum', strategy: 'TICK_MOMENTUM', category: 'tick', execution_mode: 'TICK', allocation: 1000, config: { lookback_ticks: 20, tick_cooldown_sec: 10, take_profit_percent: 0.2, tp_mode: 'percent' } },
    { id: 't6', name: 'Tick Mean Revert', strategy: 'TICK_MEAN_REVERT', category: 'tick', execution_mode: 'TICK', allocation: 1000, config: { lookback_ticks: 30, tick_cooldown_sec: 15, take_profit_percent: 0.15, tp_mode: 'percent' } },
    { id: 't7', name: 'Chart Analyst Agent', strategy: 'CHART_AGENT', category: 'agent', execution_mode: 'BAR_CLOSE', allocation: 2000, config: { min_confidence: 0.55, use_llm: false, trailing_stop_percent: 2, take_profit_percent: 3, tp_mode: 'percent', direction_mode: 'BOTH' } },
    { id: 't8', name: 'ICT Smart Money', strategy: 'ICT_SMC', category: 'smc', execution_mode: 'BAR_CLOSE', allocation: 2000, config: { ob_lookback: 10, fvg_min_gap_pct: 0.0005, sweep_lookback: 20, trailing_stop_percent: 2, take_profit_percent: 3, tp_mode: 'percent', direction_mode: 'BOTH' } },
    { id: 't9', name: 'Donchian Breakout', strategy: 'DONCHIAN_BREAKOUT', category: 'breakout', execution_mode: 'BAR_CLOSE', allocation: 3000, config: { breakout_length: 20, exit_length: 10, atr_confirm_mult: 1.0, trailing_stop_percent: 3, take_profit_percent: 4, tp_mode: 'percent', direction_mode: 'BOTH' } },
    { id: 't10', name: 'Market Maker', strategy: 'MARKET_MAKING', category: 'market_making', execution_mode: 'BAR_CLOSE', allocation: 5000, config: { spread_pct: 0.002, max_skew: 0.5, vol_shutdown_mult: 2.5, inventory_target: 0, trailing_stop_percent: 1, tp_mode: 'none', direction_mode: 'BOTH' } },
  ],
  selectedBotId: null,
  botDetail: null,
  botDrawerOpen: false,
  botHistory: [],
  tickData: {},
  tickMeta: null,
  chartDrawings: {},
  orderPrefill: null,
  /** Draft SL/TP dragged on chart or mirrored from order ticket — { symbol, side, stop_loss_price?, take_profit_price?, source } */
  chartSlTpDraft: null,
  
  copilotMessages: [],

  setChartDrawings: (symbol, drawings) => set((state) => {
    const list = Array.isArray(drawings) ? drawings.slice(0, MAX_DRAWINGS_PER_SYMBOL) : drawings;
    const next = { ...state.chartDrawings, [symbol]: list };
    const keys = Object.keys(next);
    if (keys.length > MAX_CHART_DRAWING_SYMBOLS) {
      for (const k of keys.slice(0, keys.length - MAX_CHART_DRAWING_SYMBOLS)) delete next[k];
    }
    return { chartDrawings: next };
  }),

  setOrderPrefill: (prefill) => set({ orderPrefill: prefill }),
  clearOrderPrefill: () => set({ orderPrefill: null }),
  setChartSlTpDraft: (draft) => set({ chartSlTpDraft: draft }),
  clearChartSlTpDraft: () => set({ chartSlTpDraft: null }),

  setConnectionStatus: (status) => set({ connectionStatus: status }),
  setAnalyticsReport: (report) => set({ analyticsReport: report }),
  
  appendCopilotMessage: (msg) => set((state) => {
    if (!msg || typeof msg !== 'object') return state;
    const id = msg.id != null ? String(msg.id) : null;
    const fingerprint = msg.fingerprint
      || msg.payload?.fingerprint
      || (msg.source_agent && msg.content
        ? `${msg.source_agent}|${String(msg.content).trim().toLowerCase()}`
        : null);
    if (id && state.copilotMessages.some((m) => m?.id != null && String(m.id) === id)) {
      return state;
    }
    if (
      fingerprint
      && state.copilotMessages.some((m) => {
        const fp = m?.fingerprint || m?.payload?.fingerprint
          || (m?.source_agent && m?.content
            ? `${m.source_agent}|${String(m.content).trim().toLowerCase()}`
            : null);
        return fp && fp === fingerprint;
      })
    ) {
      return state;
    }
    // Cap pending WS inbox until CopilotTab drains it.
    const next = [...state.copilotMessages, msg].slice(-40);
    return { copilotMessages: next };
  }),
  clearCopilotMessages: () => set({ copilotMessages: [] }),

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
    set(() => {
      let anyChange = false;
      const tf = resolveHistoryTimeframe(meta);
      const intervalSecs = chartTimeframeSecs(tf);

      for (const [symbol, candles] of Object.entries(historyData)) {
        const key = candleBufferKey(symbol, tf);
        const { changed, fullRebuild } = mergeCandleHistory(symbol, candles, tf, intervalSecs);
        if (!changed) continue;
        anyChange = true;
        bumpLiveRevision(key);
        if (fullRebuild) {
          bumpHistoryRevision(key);
        }
      }

      return anyChange ? {} : {};
    });
    scheduleMarketSnapshotSave(get);
  },

  prependHistory: (historyData) => {
    set(() => {
      let anyChange = false;

      for (const [symbol, candles] of Object.entries(historyData)) {
        const { changed } = prependCandleHistory(symbol, candles);
        if (!changed) continue;
        anyChange = true;
        bumpHistoryRevision(symbol);
      }

      return anyChange ? {} : {};
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
    const trades = [];
    let wins = 0;
    let losses = 0;
    let total_sells = 0;
    let total_pnl = 0;
    let totalWinPnl = 0;
    let totalLossPnl = 0;
    let best_trade = 0;
    let worst_trade = 0;
    let total_fills = 0;
    let gross_volume = 0;

    const limit = Math.min(rawTrades.length, MAX_TRADE_HISTORY);
    for (let i = 0; i < limit; i++) {
      const t = rawTrades[i];
      let ts = t.timestamp;
      if (typeof ts === 'number') {
        ts = ts < 10000000000 ? ts * 1000 : ts;
      } else if (typeof ts === 'string' && ts) {
        const parsed = new Date(ts).getTime();
        ts = isNaN(parsed) ? Date.now() : parsed;
      } else {
        ts = Date.now();
      }
      
      const trade = { ...t, timestamp: ts };
      trades.push(trade);

      if (trade.status === 'FILLED') {
        total_fills++;
        gross_volume += (trade.trade_value || 0);

        if (trade.side === 'SELL' && trade.realized_pnl != null) {
          total_sells++;
          const pnl = trade.realized_pnl;
          total_pnl += pnl;
          
          if (pnl > best_trade || total_sells === 1) best_trade = pnl;
          if (pnl < worst_trade || total_sells === 1) worst_trade = pnl;

          if (pnl > 0) {
            wins++;
            totalWinPnl += pnl;
          } else if (pnl < 0) {
            losses++;
            totalLossPnl += pnl;
          }
        }
      }
    }

    const win_rate = total_sells > 0 ? (wins / total_sells) * 100 : 0.0;
    const profit_factor = Math.abs(totalLossPnl) > 0 ? (totalWinPnl / Math.abs(totalLossPnl)) : (totalWinPnl > 0 ? 99.9 : 0.0);
    const avg_win = wins > 0 ? totalWinPnl / wins : 0.0;
    const avg_loss = losses > 0 ? totalLossPnl / losses : 0.0;

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

  setStrategyCatalog: (strategies) => {
    if (!Array.isArray(strategies) || strategies.length === 0) return;
    const templates = strategies
      .filter(s => !s.custom)
      .map((s) => ({
        id: `catalog-${s.id}`,
        name: s.name,
        strategy: s.id,
        category: s.category,
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

  updateOrderBooks: (orderBookData) => {
    if (!isOrderBookRetentionEnabled()) return;
    set((state) => {
    const merged = { ...state.orderBooks, ...orderBookData };
    // Prune to MAX_ORDERBOOK_SYMBOLS — keep active + most recently added
    const obKeys = Object.keys(merged);
    if (obKeys.length > MAX_ORDERBOOK_SYMBOLS) {
      const activeSymbol = state.activeSymbol;
      const keep = new Set([activeSymbol]);
      // Keep the most recently updated symbols
      for (const k of obKeys.slice(-MAX_ORDERBOOK_SYMBOLS)) keep.add(k);
      for (const k of obKeys) {
        if (!keep.has(k)) delete merged[k];
      }
    }
      return { orderBooks: merged };
    });
  },

  updateMarketData: (marketData) => {
    const retainOrderBooks = isOrderBookRetentionEnabled();
    set((state) => {
      const tickerData = state.tickerData;
      const priceDirections = state.priceDirections;
      const massive = isLiveMassiveMode(state.terminalMode);
      let tickerChanged = false;
      let directionChanged = false;
      let orderBooksChanged = false;
      let nextTickers = tickerData;
      let nextDirections = priceDirections;
      let nextOrderBooks = state.orderBooks;
      let candlesTouched = false;

      for (const [symbol, info] of Object.entries(marketData)) {
        if (!info) continue;

        if (
          retainOrderBooks
          && info.orderbook?.bids?.length
          && info.orderbook?.asks?.length
        ) {
          const prevOb = state.orderBooks[symbol];
          if (prevOb !== info.orderbook) {
            if (!orderBooksChanged) {
              nextOrderBooks = { ...state.orderBooks };
              orderBooksChanged = true;
            }
            nextOrderBooks[symbol] = info.orderbook;
            const obKeys = Object.keys(nextOrderBooks);
            if (obKeys.length > MAX_ORDERBOOK_SYMBOLS) {
              const activeSymbol = state.activeSymbol;
              let excess = obKeys.length - MAX_ORDERBOOK_SYMBOLS;
              for (const k of obKeys) {
                if (excess <= 0) break;
                if (k !== activeSymbol && k !== symbol) {
                  delete nextOrderBooks[k];
                  excess--;
                }
              }
            }
          }
        }

        const prev = tickerData[symbol];
        const tickerFields = ['price', 'change_24h', 'volume_24h', 'high_24h', 'low_24h'];
        const tickerDirty = !prev || tickerFields.some((k) => prev[k] !== info[k]);
        if (tickerDirty) {
          if (!tickerChanged) {
            nextTickers = { ...tickerData };
            tickerChanged = true;
          }
          if (prev) {
            const nextPrev = { ...prev };
            for (const k of tickerFields) {
              if (info[k] !== undefined) nextPrev[k] = info[k];
            }
            nextTickers[symbol] = nextPrev;
          } else {
            nextTickers[symbol] = {
              price: info.price,
              change_24h: info.change_24h,
              volume_24h: info.volume_24h,
              high_24h: info.high_24h,
              low_24h: info.low_24h,
            };
          }
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
            priceDirections[symbol] = dir;
            nextDirections = priceDirections;
            directionChanged = true;
          }
        }

        if (info.candle && !hasCandleHistory(symbol)) {
          setCandleHistory(symbol, [info.candle]);
          bumpLiveRevision(symbol);
          bumpHistoryRevision(symbol);
          candlesTouched = true;
        } else if (hasCandleHistory(symbol)) {
          if (info.candle && applyLiveCandle(symbol, info.candle)) {
            bumpLiveRevision(symbol);
            candlesTouched = true;
          }
          if (info.price !== undefined) {
            const priceMoved = prev?.price === undefined || prev.price !== info.price;
            if (priceMoved) {
              const keys = applyLivePrice(symbol, info.price);
              if (massive) {
                emitLivePrice(symbol, info.price);
                bumpLiveRevision(symbol);
                candlesTouched = true;
              } else {
                for (const key of keys) {
                  bumpLiveRevision(key);
                }
                if (keys.length) candlesTouched = true;
              }
            }
          }
        }
      }

      const updates = {};
      if (tickerChanged) updates.tickerData = nextTickers;
      if (directionChanged) updates.priceDirections = nextDirections;
      if (orderBooksChanged) updates.orderBooks = nextOrderBooks;

      if (candlesTouched) {
        scheduleMarketSnapshotSave(get);
      }

      return Object.keys(updates).length ? updates : {};
    });
  },
})));

initCandleBufferCache(getLocal('terminal_active_symbol', 'BTCUSDT'));

onCandleBufferEvict((symbol) => {
  clearRevisionsForKey(symbol);
  useStore.setState((state) => {
    const nextOrderBooks = { ...state.orderBooks };
    delete nextOrderBooks[symbol];
    const nextDrawings = { ...state.chartDrawings };
    delete nextDrawings[symbol];
    return {
      orderBooks: nextOrderBooks,
      chartDrawings: nextDrawings,
    };
  });
});

if (import.meta.hot) {
  import.meta.hot.dispose((data) => {
    const s = useStore.getState();
    const rev = snapshotRevisions();
    data.zustandSnapshot = {
      tickerData: s.tickerData,
      priceDirections: s.priceDirections,
      orderBooks: s.orderBooks,
      candleRevision: rev.candleRevision,
      candleHistoryRevision: rev.candleHistoryRevision,
      apiStatus: s.apiStatus,
    };
    forceMarketSnapshotSave(() => useStore.getState());
  });
}
