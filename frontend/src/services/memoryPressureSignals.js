/**
 * Cross-UI signals for memory-pressure degradation ladder (MEMORY #26).
 * memoryGuard writes; MultiChart / Scanner / echartsInit read.
 */

/** @typedef {{ forceDpr: number | null, scannerPaused: boolean, multiChartMaxPanes: number | null, ladder: 'ok' | 'warn' | 'critical' }} MemoryPressureState */

/** @type {MemoryPressureState} */
let state = {
  forceDpr: null,
  scannerPaused: false,
  multiChartMaxPanes: null,
  ladder: 'ok',
};

/** @type {Set<(s: MemoryPressureState) => void>} */
const listeners = new Set();

export function getMemoryPressureState() {
  return { ...state };
}

export function subscribeMemoryPressure(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function emit() {
  const snap = getMemoryPressureState();
  for (const fn of listeners) {
    try {
      fn(snap);
    } catch {
      /* ignore listener errors */
    }
  }
  if (typeof window !== 'undefined') {
    try {
      window.dispatchEvent(new CustomEvent('memory-pressure', { detail: snap }));
    } catch {
      /* ignore */
    }
  }
}

/** Warn ladder: DPR 1.0, pause scanner auto-refresh, cap multi-chart at 2 panes. */
export function applyMemoryWarnLadder() {
  state = {
    forceDpr: 1,
    scannerPaused: true,
    multiChartMaxPanes: 2,
    ladder: 'warn',
  };
  emit();
}

/** Critical ladder: same as warn (callers also prune HT / offload research). */
export function applyMemoryCriticalLadder() {
  state = {
    forceDpr: 1,
    scannerPaused: true,
    multiChartMaxPanes: 2,
    ladder: 'critical',
  };
  emit();
}

export function clearMemoryPressureLadder() {
  state = {
    forceDpr: null,
    scannerPaused: false,
    multiChartMaxPanes: null,
    ladder: 'ok',
  };
  emit();
}

export function isScannerAutoRefreshPaused() {
  return state.scannerPaused;
}

/** @internal */
export function resetMemoryPressureForTests() {
  clearMemoryPressureLadder();
  listeners.clear();
}
