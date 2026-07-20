/**
 * Shared echarts.init with capped devicePixelRatio (MEMORY_CENTRIC_REVIEW #10).
 * Retina (DPR 2–3) otherwise allocates 4–9× canvas pixel buffers.
 * Under memory warn/critical, force DPR 1.0 (MEMORY #26).
 */
import * as echarts from 'echarts';
import { getMemoryPressureState } from '../services/memoryPressureSignals';

let liveInstanceCount = 0;

export function getEchartsInstanceCount() {
  return liveInstanceCount;
}

/** @param {{ multiPane?: boolean }} [opts] */
export function cappedDevicePixelRatio({ multiPane = false } = {}) {
  const dpr = typeof window !== 'undefined' ? (Number(window.devicePixelRatio) || 1) : 1;
  const forced = getMemoryPressureState().forceDpr;
  if (forced != null && Number.isFinite(forced)) {
    return Math.min(dpr, forced, multiPane ? 1 : forced);
  }
  if (multiPane) return Math.min(dpr, 1);
  return Math.min(dpr, 1.5);
}

/**
 * @param {HTMLElement} el
 * @param {string | object | null | undefined} theme
 * @param {{ multiPane?: boolean } & Record<string, unknown>} [opts]
 */
export function initEcharts(el, theme, opts = {}) {
  const { multiPane = false, ...rest } = opts;
  const chart = echarts.init(el, theme ?? undefined, {
    devicePixelRatio: cappedDevicePixelRatio({ multiPane }),
    ...rest,
  });
  liveInstanceCount += 1;
  const origDispose = chart.dispose.bind(chart);
  chart.dispose = (...args) => {
    liveInstanceCount = Math.max(0, liveInstanceCount - 1);
    return origDispose(...args);
  };
  return chart;
}

/** @internal */
export function resetEchartsInstanceCountForTests() {
  liveInstanceCount = 0;
}
