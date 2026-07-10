/**
 * Client-side memory observability — dev badge + Settings panel.
 */

import { getCandleBufferStats } from './candleBuffer';
import {
  CANDLE_BUFFER_MAX_BARS,
  CANDLE_ARCHIVE_MAX_BARS,
  CANDLE_LRU_MAX_SYMBOLS,
  CHART_DISPLAY_MAX_BARS,
  CHART_DISPLAY_BARS_DEFAULT,
} from './memoryBudget';

/** @returns {import('./memoryObservability').ClientMemoryStats} */
export function collectClientMemoryStats() {
  const mem = typeof performance !== 'undefined' ? performance.memory : null;
  const buf = getCandleBufferStats();
  const heapMb = mem ? Math.round(mem.usedJSHeapSize / 1048576) : null;
  const heapLimitMb = mem ? Math.round(mem.jsHeapSizeLimit / 1048576) : null;
  const heapPct = mem && mem.jsHeapSizeLimit > 0
    ? Math.round((mem.usedJSHeapSize / mem.jsHeapSizeLimit) * 100)
    : null;

  return {
    heapMb,
    heapLimitMb,
    heapPct,
    ...buf,
    budgets: {
      maxSymbols: CANDLE_LRU_MAX_SYMBOLS,
      maxBars1m: CANDLE_BUFFER_MAX_BARS,
      maxArchive: CANDLE_ARCHIVE_MAX_BARS,
      maxDisplay: CHART_DISPLAY_MAX_BARS,
      defaultDisplay: CHART_DISPLAY_BARS_DEFAULT,
    },
  };
}

/** Heap-only level — drives snapshot pause and critical trims. */
export function heapPressureLevel(stats) {
  if (stats.heapPct != null && stats.heapPct >= 85) return 'critical';
  if (stats.heapPct != null && stats.heapPct >= 70) return 'warn';
  return 'ok';
}

/** Buffer at designed capacity — trim cold-path data, do not pause snapshots. */
export function bufferPressureNeedsTrim(stats) {
  if (stats.symbols1m >= stats.budgets.maxSymbols) return true;
  if (stats.bars1m > stats.budgets.maxBars1m * stats.budgets.maxSymbols * 0.9) return true;
  return false;
}

/** Combined level for UI badges (heap + buffer signals). */
export function memoryPressureLevel(stats) {
  const heap = heapPressureLevel(stats);
  if (heap !== 'ok') return heap;
  if (bufferPressureNeedsTrim(stats)) return 'warn';
  return 'ok';
}

/** @typedef {{ heapMb: number | null, heapLimitMb: number | null, heapPct: number | null, symbols1m: number, bars1m: number, htKeys: number, htBars: number, pinnedSymbol: string | null, lruOrder: string[], budgets: { maxSymbols: number, maxBars1m: number, maxArchive: number, maxDisplay: number, defaultDisplay: number } }} ClientMemoryStats */
