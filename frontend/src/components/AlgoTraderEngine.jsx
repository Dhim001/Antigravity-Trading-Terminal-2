/**
 * AlgoTraderEngine.jsx
 * Headless, client-side auto-trading bot engine.
 * Watches candleData ticks and evaluates selected strategy signals in real-time.
 * Places automated BUY orders on entry signals and exits positions on Stop Loss,
 * Take Profit, or trend reversal SELL signals.
 */
import { useEffect, useRef } from 'react';
import { useStore } from '../store/useStore';
import { sendWebSocketAction } from '../services/websocket';
import { calcEMA, calcRSI, calcMACD } from '../utils/indicators';

export default function AlgoTraderEngine() {
  const {
    isBotRunning, botStrategy, botConfig, candleData, activeSymbol,
    positions, balances, addBotLog, stopBot
  } = useStore();

  // Keep a reference to the last evaluated timestamp to prevent double triggers on the same candle close
  const lastEvaluatedTimeRef = useRef(null);

  useEffect(() => {
    if (!isBotRunning) {
      lastEvaluatedTimeRef.current = null;
      return;
    }

    const candles = candleData[activeSymbol];
    if (!candles || candles.length < 35) return;

    const latestCandle = candles[candles.length - 1];
    const curPrice     = latestCandle.close;
    const activeCandleTime = latestCandle.time;

    // 1. Prevent repainting: Only run strategy logic once per candle close (on candle boundary change)
    if (lastEvaluatedTimeRef.current === activeCandleTime) {
      return;
    }

    // Slice candles to look only at completed candles (excluding active live one)
    const completedCandles = candles.slice(0, candles.length - 1);
    if (completedCandles.length < 30) return;

    // Lock evaluation for the current candle period immediately
    lastEvaluatedTimeRef.current = activeCandleTime;

    const isCrypto = activeSymbol.includes("USDT");
    const quote = isCrypto ? "USDT" : "USD";
    const base  = isCrypto ? activeSymbol.replace("USDT", "") : activeSymbol;

    let signal = null; // 'BUY', 'SELL', or null

    // ── EMA Crossover Strategy (9/21) ───────────────────────────────────────
    if (botStrategy === 'EMA_CROSS') {
      const ema9 = calcEMA(completedCandles, 9);
      const ema21 = calcEMA(completedCandles, 21);
      if (ema9.length >= 2 && ema21.length >= 2) {
        const last9 = ema9[ema9.length - 1].value;
        const last21 = ema21[ema21.length - 1].value;
        const prev9 = ema9[ema9.length - 2].value;
        const prev21 = ema21[ema21.length - 2].value;

        if (prev9 <= prev21 && last9 > last21) {
          signal = 'BUY';
        } else if (prev9 >= prev21 && last9 < last21) {
          signal = 'SELL';
        }
      }
    }

    // ── RSI Mean Reversion (14) ──────────────────────────────────────────────
    else if (botStrategy === 'RSI_MEAN_REV') {
      const rsi = calcRSI(completedCandles, 14);
      if (rsi.length >= 2) {
        const lastRsi = rsi[rsi.length - 1].value;
        const prevRsi = rsi[rsi.length - 2].value;

        if (prevRsi < 30 && lastRsi >= 30) {
          signal = 'BUY';
        } else if (prevRsi > 70 && lastRsi <= 70) {
          signal = 'SELL';
        }
      }
    }

    // ── MACD Trend Follower ──────────────────────────────────────────────────
    else if (botStrategy === 'MACD_TREND') {
      const { macdLine, signalLine } = calcMACD(completedCandles, 12, 26, 9);
      if (macdLine.length >= 2 && signalLine.length >= 2) {
        const lastM = macdLine[macdLine.length - 1].value;
        const lastS = signalLine[signalLine.length - 1].value;
        const prevM = macdLine[macdLine.length - 2].value;
        const prevS = signalLine[signalLine.length - 2].value;

        if (prevM <= prevS && lastM > lastS) {
          signal = 'BUY';
        } else if (prevM >= prevS && lastM < lastS) {
          signal = 'SELL';
        }
      }
    }

    // ── Execution Logic ──────────────────────────────────────────────────────
    if (signal === 'BUY' && (!pos || pos.size === 0)) {
      const quoteBal = balances[quote]?.balance || 0;
      const quoteLoc = balances[quote]?.locked || 0;
      const quoteAvailable = quoteBal - quoteLoc;
      const cost = curPrice * botConfig.quantity;

      if (cost > quoteAvailable) {
        addBotLog(`⚠️ Insufficient balance to execute auto-buy. Needed: $${cost.toFixed(2)} ${quote}, Available: $${quoteAvailable.toFixed(2)} ${quote}. Stopping bot.`);
        stopBot();
        return;
      }

      addBotLog(`🤖 Strategy [${botStrategy}] triggered BUY signal on ${activeSymbol} at $${curPrice.toFixed(2)}. Placing market buy order...`);
      sendWebSocketAction("place_order", {
        symbol: activeSymbol,
        type: "MARKET",
        side: "BUY",
        quantity: botConfig.quantity,
        stop_loss_percent: botConfig.stopLossPercent,
        take_profit_percent: botConfig.takeProfitPercent
      });
    }

    else if (signal === 'SELL' && pos && pos.size > 0) {
      addBotLog(`🤖 Strategy [${botStrategy}] triggered SELL signal on ${activeSymbol} at $${curPrice.toFixed(2)}. Exiting position...`);
      sendWebSocketAction("place_order", {
        symbol: activeSymbol,
        type: "MARKET",
        side: "SELL",
        quantity: pos.size
      });
    }

    else {
      // Log completed candle scanning status
      addBotLog(`🔍 Candle completed for ${activeSymbol}. Close: $${curPrice.toFixed(2)}. Scanner: ${botStrategy} (Signal: Neutral).`);
    }

  }, [candleData, activeSymbol, isBotRunning, botStrategy, botConfig, positions, balances]);

  // Log bot startup / shutdown
  useEffect(() => {
    if (isBotRunning) {
      addBotLog(`🚀 Automated Trading Bot ACTIVATED for ${activeSymbol}. Running ${botStrategy} strategy.`);
    } else {
      addBotLog(`🛑 Automated Trading Bot DEACTIVATED.`);
    }
  }, [isBotRunning]);

  return null;
}
