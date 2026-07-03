/**
 * Position quick-trade helpers — partial close, full close, reverse.
 */

const QTY_DECIMALS = 6;

/** Round down partial quantity to avoid overselling. */
export function partialCloseQuantity(size, fraction = 0.5) {
  const abs = Math.abs(Number(size) || 0);
  if (abs <= 0) return 0;
  const factor = 10 ** QTY_DECIMALS;
  return Math.floor(abs * fraction * factor) / factor;
}

/** MARKET order payload to reduce or close a position. */
export function buildCloseOrderPayload(symbol, size, fraction = 1) {
  const qty = partialCloseQuantity(size, fraction);
  if (qty <= 0) return null;
  const s = Number(size) || 0;
  return {
    symbol,
    type: 'MARKET',
    side: s > 0 ? 'SELL' : 'BUY',
    quantity: qty,
  };
}

/**
 * Single-order flip: long → short or short → long (SIM / short-capable brokers).
 * Sends 2× abs(size) on the closing side.
 */
export function buildReverseOrderPayload(symbol, size) {
  const abs = Math.abs(Number(size) || 0);
  if (abs <= 0) return null;
  const s = Number(size) || 0;
  return {
    symbol,
    type: 'MARKET',
    side: s > 0 ? 'SELL' : 'BUY',
    quantity: abs * 2,
  };
}

export const DEFAULT_ORDER_CAPABILITIES = Object.freeze({
  partial_close: true,
  reverse_position: true,
  bracket_orders: true,
  oco: true,
  trailing_stop_manual: true,
  order_preview_costs: true,
  broker: 'SIMULATED',
});

export function normalizeOrderCapabilities(raw) {
  if (!raw || typeof raw !== 'object') return { ...DEFAULT_ORDER_CAPABILITIES };
  return { ...DEFAULT_ORDER_CAPABILITIES, ...raw };
}

/** Notional above which order entry shows a confirm step. */
export const ORDER_CONFIRM_NOTIONAL = 10_000;

export function needsOrderConfirm(preview) {
  if (!preview) return false;
  const notional = preview.notional ?? 0;
  const hasProtection = preview.stop_loss_price != null || preview.take_profit_price != null;
  return notional >= ORDER_CONFIRM_NOTIONAL || hasProtection;
}
