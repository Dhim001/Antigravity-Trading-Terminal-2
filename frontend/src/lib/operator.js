/**
 * Operator / env-only feature helpers.
 *
 * `IS_OPERATOR` is a build-time visibility gate (set `VITE_OPERATOR_MODE=true`).
 * It only controls which operator-facing controls are shown in the UI — the
 * backend admin routes are not authenticated by this flag.
 */

export const IS_OPERATOR = import.meta.env.VITE_OPERATOR_MODE === 'true';

const BROKER_LABELS = Object.freeze({
  SIMULATED: 'Sim',
  LIVE_ALPACA: 'Alpaca',
  LIVE_BINANCE: 'Binance',
  LIVE_ETORO: 'eToro',
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
