import { create } from 'zustand';

export const useStore = create((set, get) => ({
  connectionStatus: 'disconnected',
  activeSymbol: 'BTCUSDT',
  
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

  setConnectionStatus: (status) => set({ connectionStatus: status }),
  
  setActiveSymbol: (symbol) => set({ activeSymbol: symbol }),

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
