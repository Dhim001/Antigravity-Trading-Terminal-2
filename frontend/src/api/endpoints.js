import { apiAction, apiRequest } from './client';
import { applyHttpEnvelope } from './dispatch';

/** GET /health — liveness + partial terminal metadata (not action-router envelope). */
export async function fetchHealth(storeActions) {
  const body = await apiRequest('/health');
  if (body.terminal_mode != null || body.terminal_role != null) {
    storeActions.setTerminalConfig({
      terminalMode: body.terminal_mode,
      terminalRole: body.terminal_role,
    });
  }
  return body;
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
