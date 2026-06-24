import { Action } from './protocol';
import { sendAction } from './transport';
import { useStore } from '../store/useStore';
import { getStoreActions } from './dispatch';
import {
  CHART_SNAPSHOT_BARS,
  hasChartReadyHistory,
} from '../services/candleBuffer';
import {
  fetchCandles,
  fetchHealth,
  fetchSession,
} from './endpoints';
import { isLiveMassiveMode } from '../lib/massiveMarket';

let lastBootstrapAt = 0;
let bootstrapInFlight = null;
const LIGHT_BOOTSTRAP_COOLDOWN_MS = 45000;
const DEFAULT_PREFETCH_CAP = 12;

function prefetchSymbolCap() {
  const { terminalMode, symbolsList } = useStore.getState();
  if (isLiveMassiveMode(terminalMode)) {
    return Math.max(symbolsList?.length ?? 0, DEFAULT_PREFETCH_CAP);
  }
  return DEFAULT_PREFETCH_CAP;
}

function prefetchStaggerMs() {
  return isLiveMassiveMode(useStore.getState().terminalMode) ? 100 : 80;
}

/**
 * HTTP snapshot hydration — used on mount and after WebSocket reconnect.
 * @param {{ symbol?: string, light?: boolean, skipCandles?: boolean }} [opts]
 */
export async function runBootstrap(opts = {}) {
  if (bootstrapInFlight) {
    return bootstrapInFlight;
  }

  const run = async () => {
    const storeActions = getStoreActions();
    const symbol = opts.symbol ?? useStore.getState().activeSymbol;
    const light = opts.light ?? false;
    const skipCandles = opts.skipCandles ?? false;

    if (light && Date.now() - lastBootstrapAt < LIGHT_BOOTSTRAP_COOLDOWN_MS) {
      resubscribeMarketSymbols();
      return { succeeded: 0, total: 0, skipped: true };
    }
    lastBootstrapAt = Date.now();

    if (!light) {
      useStore.getState().setApiStatus('loading');
    }

    const tasks = light
      ? [fetchHealth(storeActions)]
      : [fetchSession(storeActions)];

    if (!skipCandles && !hasChartReadyHistory(symbol)) {
      tasks.push(fetchCandles(symbol, storeActions));
    }

    const results = await Promise.allSettled(tasks);
    const succeeded = results.filter((r) => r.status === 'fulfilled').length;

    if (succeeded > 0) {
      useStore.getState().setApiStatus('ready');
    } else if (!light) {
      useStore.getState().setApiStatus('error');
      console.warn('[bootstrap] All HTTP snapshot requests failed — waiting for WebSocket.');
    }

    if (!skipCandles && isLiveMassiveMode(useStore.getState().terminalMode)) {
      subscribeChartSymbols(useStore.getState().symbolsList ?? [], storeActions);
    }

    return { succeeded, total: tasks.length };
  };

  bootstrapInFlight = run().finally(() => {
    bootstrapInFlight = null;
  });
  return bootstrapInFlight;
}

/** Re-subscribe chart symbols after reconnect (watchlist + active). */
export function resubscribeMarketSymbols() {
  const { activeSymbol, symbolsList } = useStore.getState();
  const storeActions = getStoreActions();
  const cap = prefetchSymbolCap();
  const symbols = [...new Set([activeSymbol, ...(symbolsList || [])].filter(Boolean))].slice(0, cap);
  subscribeChartSymbols(symbols, storeActions);
}

/**
 * Subscribe + fetch candle history for a set of symbols (multi-chart, watchlist).
 * Staggers requests slightly to avoid burst load on the server.
 */
export function subscribeChartSymbols(symbols, storeActions) {
  const unique = [...new Set((symbols || []).filter(Boolean))];
  const stagger = prefetchStaggerMs();
  unique.forEach((sym, i) => {
    setTimeout(() => {
      sendAction(Action.SUBSCRIBE_SYMBOL, { symbol: sym, limit: CHART_SNAPSHOT_BARS });
      if (!hasChartReadyHistory(sym)) {
        fetchCandles(sym, storeActions);
      }
    }, i * stagger);
  });
}
