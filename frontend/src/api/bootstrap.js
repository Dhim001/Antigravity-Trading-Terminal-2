import { Action } from './protocol';
import { sendAction } from './transport';
import { useStore } from '../store/useStore';
import { getStoreActions } from './dispatch';
import {
  fetchAccount,
  fetchBots,
  fetchCandles,
  fetchHealth,
  fetchHistory,
  fetchLlmModels,
  fetchStrategies,
  resumeActiveBacktestJob,
} from './endpoints';

/**
 * HTTP snapshot hydration — used on mount and after WebSocket reconnect.
 * @param {{ symbol?: string, light?: boolean, skipCandles?: boolean }} [opts]
 */
export async function runBootstrap(opts = {}) {
  const storeActions = getStoreActions();
  const symbol = opts.symbol ?? useStore.getState().activeSymbol;
  const light = opts.light ?? false;
  const skipCandles = opts.skipCandles ?? false;

  if (!light) {
    useStore.getState().setApiStatus('loading');
  }

  const tasks = [
    fetchHealth(storeActions),
    fetchLlmModels(storeActions),
    fetchAccount(storeActions),
    fetchHistory(storeActions),
    fetchBots(storeActions),
    fetchStrategies(storeActions),
  ];

  if (!skipCandles) {
    tasks.push(fetchCandles(symbol, storeActions));
  }

  const results = await Promise.allSettled(tasks);

  if (!light) {
    resumeActiveBacktestJob(storeActions);
  }
  const succeeded = results.filter((r) => r.status === 'fulfilled').length;

  if (succeeded > 0) {
    useStore.getState().setApiStatus('ready');
  } else if (!light) {
    useStore.getState().setApiStatus('error');
    console.warn('[bootstrap] All HTTP snapshot requests failed — waiting for WebSocket.');
  }

  return { succeeded, total: tasks.length };
}

/** Re-subscribe chart symbols after reconnect (watchlist + active). */
export function resubscribeMarketSymbols() {
  const { activeSymbol, symbolsList } = useStore.getState();
  const storeActions = getStoreActions();
  const symbols = [...new Set([activeSymbol, ...(symbolsList || [])].filter(Boolean))].slice(0, 12);
  subscribeChartSymbols(symbols, storeActions);
}

/**
 * Subscribe + fetch candle history for a set of symbols (multi-chart, watchlist).
 * Staggers requests slightly to avoid burst load on the server.
 */
export function subscribeChartSymbols(symbols, storeActions) {
  const unique = [...new Set((symbols || []).filter(Boolean))];
  unique.forEach((sym, i) => {
    setTimeout(() => {
      sendAction(Action.SUBSCRIBE_SYMBOL, { symbol: sym });
      fetchCandles(sym, storeActions);
    }, i * 80);
  });
}
