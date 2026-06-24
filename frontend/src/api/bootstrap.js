import { Action } from './protocol';
import { sendAction } from './transport';
import { useStore } from '../store/useStore';
import { getStoreActions } from './dispatch';
import {
  CHART_SNAPSHOT_BARS,
  hasChartReadyHistory,
  isHigherTimeframe,
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
  const { terminalMode } = useStore.getState();
  if (isLiveMassiveMode(terminalMode)) {
    return 1;
  }
  return DEFAULT_PREFETCH_CAP;
}

function prefetchStaggerMs() {
  return isLiveMassiveMode(useStore.getState().terminalMode) ? 100 : 80;
}

/**
 * HTTP snapshot hydration — used on mount and after WebSocket reconnect.
 * @param {{ symbol?: string, light?: boolean, skipCandles?: boolean, timeframe?: string }} [opts]
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
    const timeframe = opts.timeframe ?? '1m';

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

    const massive = isLiveMassiveMode(useStore.getState().terminalMode);
    const chartTf = massive && isHigherTimeframe(timeframe) ? timeframe : '1m';

    if (!skipCandles && !hasChartReadyHistory(symbol, undefined, chartTf)) {
      tasks.push(fetchCandles(symbol, storeActions, {
        limit: CHART_SNAPSHOT_BARS,
        interval: chartTf !== '1m' ? chartTf : undefined,
      }));
    }

    const results = await Promise.allSettled(tasks);
    const succeeded = results.filter((r) => r.status === 'fulfilled').length;

    if (succeeded > 0) {
      useStore.getState().setApiStatus('ready');
    } else if (!light) {
      useStore.getState().setApiStatus('error');
      console.warn('[bootstrap] All HTTP snapshot requests failed — waiting for WebSocket.');
    }

    if (!skipCandles && massive) {
      subscribeChartSymbols([symbol], storeActions, { interval: '1m' });
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
  const { activeSymbol, symbolsList, terminalMode } = useStore.getState();
  const storeActions = getStoreActions();
  if (isLiveMassiveMode(terminalMode)) {
    subscribeChartSymbols([activeSymbol].filter(Boolean), storeActions, { interval: '1m' });
    return;
  }
  const cap = prefetchSymbolCap();
  const symbols = [...new Set([activeSymbol, ...(symbolsList || [])].filter(Boolean))].slice(0, cap);
  subscribeChartSymbols(symbols, storeActions);
}

/**
 * Subscribe + fetch candle history for a set of symbols (multi-chart, watchlist).
 * Staggers requests slightly to avoid burst load on the server.
 * @param {{ interval?: string }} [opts]
 */
export function subscribeChartSymbols(symbols, storeActions, opts = {}) {
  const unique = [...new Set((symbols || []).filter(Boolean))];
  const interval = opts.interval || '1m';
  const stagger = prefetchStaggerMs();
  unique.forEach((sym, i) => {
    setTimeout(() => {
      const payload = { symbol: sym, limit: CHART_SNAPSHOT_BARS };
      if (interval && interval !== '1m') payload.interval = interval;
      sendAction(Action.SUBSCRIBE_SYMBOL, payload);
      if (!hasChartReadyHistory(sym, undefined, interval)) {
        fetchCandles(sym, storeActions, {
          limit: CHART_SNAPSHOT_BARS,
          interval: interval !== '1m' ? interval : undefined,
        });
      }
    }, i * stagger);
  });
}
