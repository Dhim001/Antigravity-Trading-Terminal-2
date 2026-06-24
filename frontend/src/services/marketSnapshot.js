/**
 * Session-scoped market snapshot — survives refresh & HMR within the same tab.
 * Profile-scoped key so sim / ib / massive instances do not cross-contaminate.
 */
import { getCandles, setCandleHistory } from './candleBuffer';

const PROFILE = import.meta.env.VITE_TERMINAL_PROFILE || 'default';
const KEY = `terminal_market_snapshot_${PROFILE}`;
const MAX_CANDLES_PER_SYMBOL = 300;
const SAVE_DEBOUNCE_MS = 2000;

let saveTimer = null;

/** @returns {{ tickerData?: object, priceDirections?: object, candleRevision?: object, candleHistoryRevision?: object }} */
export function hydrateFromSnapshot() {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return {};

    const snap = JSON.parse(raw);
    const candleRevision = {};
    const candleHistoryRevision = {};

    if (snap.candles && typeof snap.candles === 'object') {
      for (const [sym, bars] of Object.entries(snap.candles)) {
        if (Array.isArray(bars) && bars.length > 0) {
          setCandleHistory(sym, bars);
          candleRevision[sym] = 1;
          candleHistoryRevision[sym] = 1;
        }
      }
    }

    return {
      tickerData: snap.tickerData || {},
      priceDirections: snap.priceDirections || {},
      candleRevision,
      candleHistoryRevision,
    };
  } catch (_) {
    return {};
  }
}

export function scheduleMarketSnapshotSave(getState) {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saveTimer = null;
    saveMarketSnapshot(getState);
  }, SAVE_DEBOUNCE_MS);
}

function saveMarketSnapshot(getState) {
  const state = getState();
  const symbols = new Set([state.activeSymbol, ...(state.symbolsList || [])]);
  const candles = {};

  for (const sym of symbols) {
    if (!sym) continue;
    const bars = getCandles(sym);
    if (bars.length > 0) {
      candles[sym] = bars.slice(-MAX_CANDLES_PER_SYMBOL);
    }
  }

  const payload = {
    v: 1,
    savedAt: Date.now(),
    tickerData: state.tickerData,
    priceDirections: state.priceDirections,
    candles,
  };

  try {
    sessionStorage.setItem(KEY, JSON.stringify(payload));
  } catch (_) {
    try {
      const active = state.activeSymbol;
      const trimmed = {
        v: 1,
        savedAt: Date.now(),
        tickerData: state.tickerData,
        priceDirections: state.priceDirections,
        candles: active && candles[active] ? { [active]: candles[active] } : {},
      };
      sessionStorage.setItem(KEY, JSON.stringify(trimmed));
    } catch (_) {
      /* quota exceeded — skip */
    }
  }
}

export function clearMarketSnapshot() {
  try {
    sessionStorage.removeItem(KEY);
  } catch (_) {}
}

/** Flush snapshot immediately (HMR dispose, beforeunload). */
export function forceMarketSnapshotSave(getState) {
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
  }
  saveMarketSnapshot(getState);
}
