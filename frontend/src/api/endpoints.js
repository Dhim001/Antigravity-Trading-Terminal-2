import { apiAction, apiRequest } from './client';
import { applyHttpEnvelope } from './dispatch';
import { useStore } from '../store/useStore';

/** GET /health — liveness + partial terminal metadata (not action-router envelope). */
export async function fetchHealth(storeActions) {
  const body = await apiRequest('/health');
  if (body.terminal_mode != null || body.terminal_role != null) {
    storeActions.setTerminalConfig({
      terminalMode: body.terminal_mode,
      terminalRole: body.terminal_role,
      distributed: body.worker != null,
    });
  }
  return body;
}

export async function fetchStrategies(storeActions) {
  try {
    const body = await apiRequest('/api/v1/strategies');
    if (body.strategies) {
      storeActions.setStrategyCatalog(body.strategies);
    }
    return body;
  } catch (e) {
    console.warn('[bootstrap] Strategy catalog unavailable:', e.message);
    return null;
  }
}

export async function fetchBacktestRuns(storeActions, symbol) {
  try {
    const qs = symbol ? `?symbol=${encodeURIComponent(symbol)}&limit=20` : '?limit=20';
    const body = await apiRequest(`/api/v1/backtest/runs${qs}`);
    if (body.runs) {
      storeActions.setBacktestRuns(body.runs);
    }
    return body;
  } catch (e) {
    console.warn('[bootstrap] Backtest runs unavailable:', e.message);
    return null;
  }
}

export async function fetchAccount(storeActions) {
  const body = await apiAction('/api/v1/account');
  applyHttpEnvelope(body, storeActions);
  return body;
}

export async function fetchHistory(storeActions) {
  const body = await apiAction('/api/v1/history');
  applyHttpEnvelope(body, storeActions);
  return body;
}

export async function fetchBots(storeActions) {
  const body = await apiAction('/api/v1/bots');
  applyHttpEnvelope(body, storeActions);
  return body;
}

export async function fetchCandles(symbol, storeActions) {
  const encoded = encodeURIComponent(symbol);
  const body = await apiAction(`/api/v1/market/${encoded}/candles`);
  applyHttpEnvelope(body, storeActions);
  return body;
}

/** Fetch archived OHLCV range and prepend to chart buffer (scroll-left load). */
export async function fetchOlderCandles(symbol, from, to, interval = 'auto') {
  const encoded = encodeURIComponent(symbol);
  const qs = new URLSearchParams({
    from: String(from),
    to: String(to),
    interval,
  });
  const body = await apiAction(
    `/api/v1/market/${encoded}/history?${qs}`,
    { timeoutMs: 20000 },
  );
  const bars = body.data?.[symbol];
  if (!Array.isArray(bars) || bars.length === 0) return 0;

  const { prependHistory } = useStore.getState();
  prependHistory({ [symbol]: bars });
  return bars.length;
}
