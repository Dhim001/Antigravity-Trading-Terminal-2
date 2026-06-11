/** In-memory OHLCV buffers — mutated in place to avoid copying 10k+ bars every tick. */

const MAX_BARS = 10080;
const buffers = new Map();

export function setCandleHistory(symbol, candles) {
  if (!symbol || !Array.isArray(candles)) return;
  buffers.set(symbol, candles);
}

export function hasCandleHistory(symbol) {
  return buffers.has(symbol) && buffers.get(symbol).length > 0;
}

export function getCandles(symbol) {
  return buffers.get(symbol) || [];
}

/** @returns {boolean} true if buffer changed */
export function applyLiveCandle(symbol, incoming) {
  if (!symbol || !incoming) return false;

  const buf = buffers.get(symbol);
  if (!buf?.length) return false;

  const last = buf[buf.length - 1];
  if (last.time === incoming.time) {
    if (
      last.open === incoming.open
      && last.high === incoming.high
      && last.low === incoming.low
      && last.close === incoming.close
      && last.volume === incoming.volume
    ) {
      return false;
    }
    buf[buf.length - 1] = incoming;
    return true;
  }

  buf.push(incoming);
  if (buf.length > MAX_BARS) buf.shift();
  return true;
}

export function clearCandleBuffer(symbol) {
  buffers.delete(symbol);
}
