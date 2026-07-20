/**
 * Client-side memory observability — dev badge + Settings panel.
 * Includes subsystem estimates (MEMORY #25).
 */

import { getCandleBufferStats } from './candleBuffer';
import {
  CANDLE_BUFFER_MAX_BARS,
  CANDLE_ARCHIVE_MAX_BARS,
  CANDLE_LRU_MAX_SYMBOLS,
  CHART_DISPLAY_MAX_BARS,
  CHART_DISPLAY_BARS_DEFAULT,
} from './memoryBudget';
import { getEchartsInstanceCount } from '../lib/echartsInit';
import { getMemoryPressureState } from './memoryPressureSignals';
import { useResearchStore } from '../store/useResearchStore';

function estimateBacktestKb(results) {
  if (!results) return 0;
  if (results._offloaded) return 2;
  let bytes = 4096;
  if (Array.isArray(results.trades)) bytes += results.trades.length * 180;
  if (Array.isArray(results.equity_curve)) bytes += results.equity_curve.length * 32;
  if (Array.isArray(results.sweep?.results)) bytes += results.sweep.results.length * 400;
  if (Array.isArray(results.walk_forward?.folds)) {
    bytes += results.walk_forward.folds.length * 8000;
  }
  return Math.round(bytes / 1024);
}

function estimateResearchStoreKb() {
  try {
    const s = useResearchStore.getState();
    let kb = estimateBacktestKb(s.backtestResults);
    if (s.scanResults?.rows) kb += Math.round((s.scanResults.rows.length * 120) / 1024);
    if (s.journalEntries) kb += Math.round((s.journalEntries.length * 200) / 1024);
    const insightKeys = Object.keys(s.agentInsightHistory || {});
    for (const k of insightKeys) {
      const arr = s.agentInsightHistory[k];
      if (Array.isArray(arr)) kb += Math.round((arr.length * 800) / 1024);
    }
    return kb;
  } catch {
    return null;
  }
}

/** Approximate candle buffer bytes (CompactBarSeries ≈ 48 B/bar). */
function estimateCandleBufferKb(buf) {
  const bars = (buf.bars1m || 0) + (buf.htBars || 0);
  return Math.round((bars * 48) / 1024);
}

/** @returns {import('./memoryObservability').ClientMemoryStats} */
export function collectClientMemoryStats() {
  const mem = typeof performance !== 'undefined' ? performance.memory : null;
  const buf = getCandleBufferStats();
  const heapMb = mem ? Math.round(mem.usedJSHeapSize / 1048576) : null;
  const heapLimitMb = mem ? Math.round(mem.jsHeapSizeLimit / 1048576) : null;
  const heapPct = mem && mem.jsHeapSizeLimit > 0
    ? Math.round((mem.usedJSHeapSize / mem.jsHeapSizeLimit) * 100)
    : null;

  const researchKb = estimateResearchStoreKb();
  const candleKb = estimateCandleBufferKb(buf);
  const pressure = getMemoryPressureState();

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
    subsystems: {
      candleBuffersKb: candleKb,
      researchStoreKb: researchKb,
      echartsInstances: getEchartsInstanceCount(),
      pressureLadder: pressure.ladder,
      scannerPaused: pressure.scannerPaused,
      multiChartMaxPanes: pressure.multiChartMaxPanes,
      forceDpr: pressure.forceDpr,
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
