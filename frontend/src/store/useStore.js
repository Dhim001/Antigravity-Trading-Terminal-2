import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import {
  setCandleHistory, applyLiveCandle, hasCandleHistory,
} from '../services/candleBuffer';

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

function bumpRevision(revisions, symbol) {
  return { ...revisions, [symbol]: (revisions[symbol] || 0) + 1 };
}

export const useStore = create(subscribeWithSelector((set, get) => ({
  connectionStatus: 'disconnected',
  activeSymbol: getLocal('terminal_active_symbol', 'BTCUSDT'),
  viewMode: getLocal('terminal_view_mode', 'single'),
  terminalMode: 'SIMULATED',
  isLive: false,
  symbolsList: ["BTCUSDT", "ETHUSDT", "AAPL", "TSLA", "MSFT"],

  // Market data states
  tickerData: {},
  priceDirections: {},
  orderBooks: {},
  /** Bumped on live ticks — drives incremental chart patches */
  candleRevision: {},
  /** Bumped only on history load — drives full chart rebuild */
  candleHistoryRevision: {},

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
  botConfig: getLocal('terminal_bot_config', {
    allocation: 1000,
  }),
  botLogs: [],

  backtestResults: null,
  chartInteractionMode: 'normal',
  strategyTemplates: [
    { id: 't1', name: 'Bull Market Scalper', strategy: 'MACD_RSI', allocation: 2000, config: { rsi_length: 14, macd_fast: 12, macd_slow: 26, trailing_stop_percent: 1.5 } },
    { id: 't2', name: 'Trend Follower', strategy: 'SUPERTREND', allocation: 5000, config: { st_length: 14, st_multiplier: 3, trailing_stop_percent: 3 } },
    { id: 't3', name: 'Mean Reversion', strategy: 'BB_STOCH', allocation: 1000, config: { bb_length: 20, bb_std: 2, trailing_stop_percent: 1 } },
  ],
  selectedBotId: null,

  setConnectionStatus: (status) => set({ connectionStatus: status }),

  setActiveSymbol: (symbol) => {
    setLocal('terminal_active_symbol', symbol);
    set({ activeSymbol: symbol });
    import('../services/websocket').then(({ sendWebSocketAction }) => {
      sendWebSocketAction("subscribe_symbol", { symbol });
    });
  },

  setViewMode: (mode) => {
    setLocal('terminal_view_mode', mode);
    set({ viewMode: mode });
  },

  updateHistory: (historyData) => set((state) => {
    let candleRevision = state.candleRevision;
    let candleHistoryRevision = state.candleHistoryRevision;
    for (const [symbol, candles] of Object.entries(historyData)) {
      setCandleHistory(symbol, candles);
      candleRevision = bumpRevision(candleRevision, symbol);
      candleHistoryRevision = bumpRevision(candleHistoryRevision, symbol);
    }
    return { candleRevision, candleHistoryRevision };
  }),

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

  setSymbolsList: (list) => set({ symbolsList: list }),

  setBots: (bots) => set({ activeBots: bots }),
  startBot: () => set({ isBotRunning: true }),
  stopBot: () => set({ isBotRunning: false }),
  setBotStrategy: (strategy) => {
    setLocal('terminal_bot_strategy', strategy);
    set({ botStrategy: strategy });
  },
  updateBotConfig: (config) => set((state) => {
    const next = { ...state.botConfig, ...config };
    setLocal('terminal_bot_config', next);
    return { botConfig: next };
  }),
  addBotLog: (log) => set((state) => {
    const time = new Date().toLocaleTimeString();
    const entry = `[${time}] ${log.message || log}`;
    const newLogs = [entry, ...state.botLogs];
    if (newLogs.length > 100) newLogs.pop();
    return { botLogs: newLogs };
  }),
  setBotLogs: (logsArray) => set({
    botLogs: logsArray.map(log => {
      const time = new Date(log.timestamp + "Z").toLocaleTimeString();
      return `[${time}] ${log.level || 'INFO'} - ${log.message}`;
    }),
  }),
  clearBotLogs: () => set({ botLogs: [] }),

  setBacktestResults: (results) => set({ backtestResults: results }),
  setChartInteractionMode: (mode) => set({ chartInteractionMode: mode }),
  setSelectedBotId: (id) => set({ selectedBotId: id }),

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
      const activeSymbol = state.activeSymbol;
      const tickerData = state.tickerData;
      const priceDirections = state.priceDirections;
      let candleRevision = null;
      let tickerChanged = false;
      let directionChanged = false;
      let nextTickers = tickerData;
      let nextDirections = priceDirections;

      for (const [symbol, info] of Object.entries(marketData)) {
        if (!info) continue;

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

        // Live candle: only symbols with loaded history (O(1) in-place mutation)
        if (info.candle && hasCandleHistory(symbol)) {
          if (applyLiveCandle(symbol, info.candle)) {
            candleRevision = bumpRevision(candleRevision ?? state.candleRevision, symbol);
          }
        }
      }

      const updates = {};
      if (tickerChanged) updates.tickerData = nextTickers;
      if (directionChanged) updates.priceDirections = nextDirections;
      if (candleRevision) updates.candleRevision = candleRevision;

      return Object.keys(updates).length ? updates : {};
    });
  },
})));
