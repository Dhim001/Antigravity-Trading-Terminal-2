/**
 * Pressure-responsive memory trimming — acts on heap % thresholds.
 * Ladder (MEMORY #26): warn → DPR/scanner/multi-chart; critical → HT prune + research offload.
 */

import { buildBacktestOverlay } from '../lib/backtestSlim';
import {
  collectClientMemoryStats,
  heapPressureLevel,
  bufferPressureNeedsTrim,
} from './memoryObservability';
import { pruneSymbolCache, pruneHtBuffersExceptPinned } from './candleBuffer';
import {
  applyMemoryWarnLadder,
  applyMemoryCriticalLadder,
  clearMemoryPressureLadder,
} from './memoryPressureSignals';

const CHECK_INTERVAL_MS = 30_000;
let timerId = null;
let lastLevel = 'ok';
let snapshotPaused = false;

export function isSnapshotPaused() {
  return snapshotPaused;
}

/**
 * @param {() => import('../store/useStore').useStore} getMarketStore
 * @param {() => import('../store/useResearchStore').useResearchStore} getResearchStore
 */
export function startMemoryGuard(getMarketStore, getResearchStore) {
  if (typeof window === 'undefined' || timerId != null) return;
  timerId = setInterval(() => {
    tickMemoryGuard(getMarketStore, getResearchStore);
  }, CHECK_INTERVAL_MS);
}

export function stopMemoryGuard() {
  if (timerId != null) {
    clearInterval(timerId);
    timerId = null;
  }
  lastLevel = 'ok';
  snapshotPaused = false;
  clearMemoryPressureLadder();
}

function trimVisionAndInsightHistory(research) {
  const nextVision = { ...research.visionReports };
  for (const k of Object.keys(nextVision)) {
    const entry = nextVision[k];
    if (entry && (entry.image_base64 || entry.image)) {
      const { image_base64, image, ...rest } = entry;
      nextVision[k] = rest;
    }
  }

  const nextHistory = { ...research.agentInsightHistory };
  for (const sym of Object.keys(nextHistory)) {
    if (Array.isArray(nextHistory[sym]) && nextHistory[sym].length > 10) {
      nextHistory[sym] = nextHistory[sym].slice(0, 10);
    }
  }

  return { visionReports: nextVision, agentInsightHistory: nextHistory };
}

function trimScanAndJournal(research) {
  const patches = {};
  if (research.scanResults?.rows?.length > 50) {
    patches.scanResults = {
      ...research.scanResults,
      rows: research.scanResults.rows.slice(0, 50),
      rows_truncated: research.scanResults.rows.length,
    };
  }
  if (research.journalEntries?.length > 100) {
    patches.journalEntries = research.journalEntries.slice(0, 100);
  }
  return patches;
}

function trimWarn(getResearchStore, { pauseSnapshots = true } = {}) {
  if (pauseSnapshots) {
    snapshotPaused = true;
    applyMemoryWarnLadder();
  }
  getResearchStore().setState((current) => trimVisionAndInsightHistory(current));
}

function trimCritical(getMarketStore, getResearchStore) {
  snapshotPaused = true;
  applyMemoryCriticalLadder();
  pruneSymbolCache();

  const active = getMarketStore().getState().activeSymbol;
  pruneHtBuffersExceptPinned(active ? [active] : []);

  getMarketStore().setState((market) => {
    const a = market.activeSymbol;
    return {
      orderBooks: a && market.orderBooks[a]
        ? { [a]: market.orderBooks[a] }
        : {},
    };
  });

  // Force offload research backtest tree even if Lab is open (MEMORY #26).
  const research = getResearchStore().getState();
  if (research.backtestResults && !research.backtestResults._offloaded) {
    import('./backtestStorage').then(({ offloadBacktestFromMemory }) => {
      getResearchStore().setState((current) => {
        const patches = {
          ...trimVisionAndInsightHistory(current),
          ...trimScanAndJournal(current),
        };
        if (!current.backtestResults || current.backtestResults._offloaded) {
          return patches;
        }
        const slim = offloadBacktestFromMemory(current.backtestResults);
        return {
          ...patches,
          backtestResults: slim,
          backtestOverlay: buildBacktestOverlay(slim) ?? current.backtestOverlay,
        };
      });
    }).catch(() => {
      getResearchStore().setState((current) => ({
        ...trimVisionAndInsightHistory(current),
        ...trimScanAndJournal(current),
      }));
    });
    return;
  }

  getResearchStore().setState((current) => ({
    ...trimVisionAndInsightHistory(current),
    ...trimScanAndJournal(current),
  }));
}

function tickMemoryGuard(getMarketStore, getResearchStore) {
  const stats = collectClientMemoryStats();
  const heapLevel = heapPressureLevel(stats);
  const bufferTrim = bufferPressureNeedsTrim(stats);

  if (heapLevel === 'ok' && !bufferTrim) {
    if (lastLevel !== 'ok') {
      snapshotPaused = false;
      clearMemoryPressureLadder();
    }
    lastLevel = 'ok';
    return;
  }

  if (heapLevel === 'critical') {
    if (lastLevel === 'critical') return;
    lastLevel = 'critical';
    snapshotPaused = true;
    trimCritical(getMarketStore, getResearchStore);
    return;
  }

  if (heapLevel === 'warn') {
    if (lastLevel === 'warn') return;
    lastLevel = 'warn';
    snapshotPaused = true;
    trimWarn(getResearchStore);
    return;
  }

  if (lastLevel === 'buffer') return;
  lastLevel = 'buffer';
  snapshotPaused = false;
  trimWarn(getResearchStore, { pauseSnapshots: false });
}

/** @internal */
export function resetMemoryGuardForTests() {
  stopMemoryGuard();
}

/** @internal — simulate guard tick for unit tests. */
export function runMemoryGuardTickForTests(getMarketStore, getResearchStore) {
  tickMemoryGuard(getMarketStore, getResearchStore);
}
