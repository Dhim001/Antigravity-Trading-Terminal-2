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

  // Algorithmic Auto-Trading States
  isBotRunning: false,
  botStrategy: getLocal('terminal_bot_strategy', 'EMA_CROSS'), // 'EMA_CROSS', 'RSI_MEAN_REV', 'MACD_TREND'
  botConfig: getLocal('terminal_bot_config', {
    quantity: 0.1,         // default quantity (BTC/ETH/Shares)
    stopLossPercent: 1.5,  // default SL %
    takeProfitPercent: 3.0,// default TP %
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

  setTradeHistory: (data) => set({
    tradeHistory: data.trades || [],
    tradeStats:   data.stats  || null,
  }),

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
    const entry = `[${time}] ${log}`;
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
