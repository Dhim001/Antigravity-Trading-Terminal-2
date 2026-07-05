/**
 * Operator / admin UI helpers.
 *
 * Operator mode can be enabled at build time (`VITE_OPERATOR_MODE=true`) or at
 * runtime via backend `OPERATOR_MODE=true` (surfaced on GET /api/v1/session).
 * This only controls which operator-facing controls are shown — backend admin
 * routes are not authenticated by this flag.
 */

import { useStore } from '../store/useStore';

export const IS_OPERATOR_BUILD = import.meta.env.VITE_OPERATOR_MODE === 'true';

/** True when build-time or session operator mode is active. */
export function resolveIsOperator(storeIsOperator) {
  return IS_OPERATOR_BUILD || storeIsOperator === true;
}

/** React hook — prefer this over the build-time `IS_OPERATOR` constant. */
export function useIsOperator() {
  const storeIsOperator = useStore((s) => s.isOperator);
  return resolveIsOperator(storeIsOperator);
}

/** @deprecated Use `useIsOperator()` in components. */
export const IS_OPERATOR = IS_OPERATOR_BUILD;

const BROKER_LABELS = Object.freeze({
  SIMULATED: 'Sim',
  LIVE_ALPACA: 'Alpaca',
  LIVE_BINANCE: 'Binance',
  LIVE_ETORO: 'eToro',
  LIVE_IB: 'IB',
  LIVE_MASSIVE: 'Massive',
});

/** Short, human-readable broker name for a TERMINAL_MODE value. */
export function brokerLabel(mode) {
  if (!mode) return 'Sim';
  return BROKER_LABELS[mode] ?? mode.replace(/^LIVE_/, '');
}

/** True when the terminal is running against a live broker (not the simulator). */
export function isLiveMode(mode) {
  return Boolean(mode) && mode !== 'SIMULATED';
}
