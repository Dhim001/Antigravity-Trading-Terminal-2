import { create } from 'zustand';

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

export const useStore = create((set, get) => ({
  connectionStatus: 'disconnected',
  activeSymbol: getLocal('terminal_active_symbol', 'BTCUSDT'),
  viewMode: getLocal('terminal_view_mode', 'single'),
  terminalMode: 'SIMULATED',
  isLive: false,
  symbolsList: ["BTCUSDT", "ETHUSDT", "AAPL", "TSLA", "MSFT"],
  
  // Market data states
  tickerData: {},      // symbol -> { price, change_24h, volume_24h, high_24h, low_24h }
  priceDirections: {}, // symbol -> 'up' | 'down' | 'flat' (for flashing)
  orderBooks: {},      // symbol -> { bids: [], asks: [] }
  candleData: {},      // symbol -> [ candles ]
  
  // Account states
  balances: {},        // asset -> { balance, locked }
  positions: {},       // symbol -> { size, avg_price }
  orders: [],          // list of recent orders
  orderResult: null,   // { status: 'success'|'error', message }

  // Trade history (populated on demand when History panel is opened)
  tradeHistory: [],    // full enriched trade list with realized P&L
  tradeStats:   null,  // aggregate statistics object

  // Diagnostics and Admin Panel Stats
  systemStats: { clients: 1, positions_count: 0, pending_orders_count: 0, filled_trades_count: 0, tick_interval: 0.25, volatility_multiplier: 1.0 },

  // Algorithmic Auto-Trading States
  activeBots: [],          // array of bots from backend
  isBotRunning: false,
  botStrategy: getLocal('terminal_bot_strategy', 'MACD_RSI'),
  botConfig: getLocal('terminal_bot_config', {
    allocation: 1000,      // Default capital allocation
  }),
  botLogs: [],             // array of bot operation log strings

  setConnectionStatus: (status) => set({ connectionStatus: status }),
  
  setActiveSymbol: (symbol) => {
    setLocal('terminal_active_symbol', symbol);
    set({ activeSymbol: symbol });
  },

  setViewMode: (mode) => {
    setLocal('terminal_view_mode', mode);
    set({ viewMode: mode });
  },

  updateHistory: (historyData) => set({ candleData: historyData }),

  updateAccount: (accountData) => set({
    balances: accountData.balances || {},
    positions: accountData.positions || {},
    orders: accountData.orders || []
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
    
    const stats = {
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
      gross_volume
    };

    set({
      tradeHistory: trades,
      tradeStats: stats,
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
    if (newLogs.length > 100) newLogs.pop(); // Cap log size
    return { botLogs: newLogs };
  }),
  clearBotLogs: () => set({ botLogs: [] }),

  setOrderResult: (result) => {
    set({ orderResult: result });
    // Auto-clear order notifications after 4 seconds
    setTimeout(() => {
      set((state) => {
        // Only clear if it's the same result instance
        if (state.orderResult === result) {
          return { orderResult: null };
        }
        return {};
      });
    }, 4000);
  },

  updateMarketData: (marketData) => {
    set((state) => {
      const newTickers = { ...state.tickerData };
      const newDirections = { ...state.priceDirections };
      const newOrderBooks = { ...state.orderBooks };
      const newCandles = { ...state.candleData };
      
      let hasTickerChange = false;
      let hasOBChange = false;
      let hasCandleChange = false;

      for (const [symbol, info] of Object.entries(marketData)) {
        if (!info) continue;
        
        // 1. Ticker updates & price flashes
        const oldPrice = newTickers[symbol]?.price;
        const newPrice = info.price;
        
        if (oldPrice !== undefined && oldPrice !== newPrice) {
          newDirections[symbol] = newPrice > oldPrice ? 'up' : 'down';
        } else if (oldPrice === undefined) {
          newDirections[symbol] = 'flat';
        }
        
        newTickers[symbol] = {
          price: info.price,
          change_24h: info.change_24h,
          volume_24h: info.volume_24h,
          high_24h: info.high_24h,
          low_24h: info.low_24h
        };
        hasTickerChange = true;

        // 2. Order book updates
        if (info.orderbook) {
          newOrderBooks[symbol] = info.orderbook;
          hasOBChange = true;
        }

        // 3. Candle updates (append/replace latest candle)
        if (info.candle) {
          const candles = newCandles[symbol] ? [...newCandles[symbol]] : [];
          const incomingCandle = info.candle;
          
          if (candles.length > 0 && candles[candles.length - 1].time === incomingCandle.time) {
            // Update current active candle
            candles[candles.length - 1] = incomingCandle;
          } else {
            // Append new candle
            candles.push(incomingCandle);
            if (candles.length > 500) {
              candles.shift();
            }
          }
          newCandles[symbol] = candles;
          hasCandleChange = true;
        }
      }

      const updates = {};
      if (hasTickerChange) {
        updates.tickerData = newTickers;
        updates.priceDirections = newDirections;
      }
      if (hasOBChange) {
        updates.orderBooks = newOrderBooks;
      }
      if (hasCandleChange) {
        updates.candleData = newCandles;
      }
      
      return updates;
    });
  }
}));
