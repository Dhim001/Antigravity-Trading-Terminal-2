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
import {
  isLiveMassiveMode,
  preferLiveAlpacaSymbol,
  usesNativeHtCharts,
} from '../lib/massiveMarket';

let lastBootstrapAt = 0;
let bootstrapInFlight = null;
let alpacaSymbolNudgeDone = false;
const LIGHT_BOOTSTRAP_COOLDOWN_MS = 45000;
const DEFAULT_PREFETCH_CAP = 12;

function prefetchSymbolCap() {
  const { terminalMode } = useStore.getState();
  // Massive + Alpaca: avoid REST/WS burst on connect — active symbol only.
  if (usesNativeHtCharts(terminalMode)) {
    return 1;
  }
  return DEFAULT_PREFETCH_CAP;
}

function prefetchStaggerMs() {
  return usesNativeHtCharts(useStore.getState().terminalMode) ? 100 : 80;
}

/**
 * HTTP snapshot hydration — used on mount and after WebSocket reconnect.
 * @param {{ symbol?: string, light?: boolean, skipCandles?: boolean, timeframe?: string, offline?: boolean }} [opts]
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
    const offline = opts.offline ?? false;
    const timeframe = opts.timeframe ?? '1m';

    if (light && !offline && Date.now() - lastBootstrapAt < LIGHT_BOOTSTRAP_COOLDOWN_MS) {
      if (useStore.getState().connectionStatus === 'connected') {
        resubscribeMarketSymbols();
      }
      return { succeeded: 0, total: 0, skipped: true };
    }
    lastBootstrapAt = Date.now();

    if (!light && !offline) {
      useStore.getState().setApiStatus('loading');
    }

    const tasks = offline
      ? [fetchSession(storeActions)]
      : light
        ? [fetchHealth(storeActions)]
        : [fetchSession(storeActions)];

    const nativeHt =
      usesNativeHtCharts(useStore.getState().terminalMode)
      && isHigherTimeframe(timeframe);
    const chartTf = nativeHt ? timeframe : '1m';

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

    const mode = useStore.getState().terminalMode;
    // Weekend / after-hours: equities look dead on Alpaca — land on BTC once.
    if (!alpacaSymbolNudgeDone) {
      const state = useStore.getState();
      const nudge = preferLiveAlpacaSymbol(mode, state.activeSymbol, state.symbolsList);
      if (nudge) {
        alpacaSymbolNudgeDone = true;
        state.setActiveSymbol(nudge);
      } else if (mode === 'LIVE_ALPACA') {
        alpacaSymbolNudgeDone = true;
      }
    }
    const chartSymbol = useStore.getState().activeSymbol || symbol;
    if (
      !skipCandles
      && usesNativeHtCharts(mode)
      && useStore.getState().connectionStatus === 'connected'
    ) {
      subscribeChartSymbols([chartSymbol], storeActions, { interval: '1m' });
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
  if (useStore.getState().connectionStatus !== 'connected') {
    return;
  }
  const { activeSymbol, symbolsList, terminalMode } = useStore.getState();
  const storeActions = getStoreActions();
  if (usesNativeHtCharts(terminalMode)) {
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
  const wsConnected = useStore.getState().connectionStatus === 'connected';
  unique.forEach((sym, i) => {
    setTimeout(() => {
      const payload = { symbol: sym, limit: CHART_SNAPSHOT_BARS };
      if (interval && interval !== '1m') payload.interval = interval;
      if (wsConnected) {
        sendAction(Action.SUBSCRIBE_SYMBOL, payload);
      }
      if (!hasChartReadyHistory(sym, undefined, interval)) {
        fetchCandles(sym, storeActions, {
          limit: CHART_SNAPSHOT_BARS,
          interval: interval !== '1m' ? interval : undefined,
        });
      }
    }, i * stagger);
  });
}
