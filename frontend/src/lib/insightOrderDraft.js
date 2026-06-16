/**
 * Map analyst insight → order ticket draft (HITL; does not place orders).
 */

const DEFAULT_ALLOCATION = 500;

/**
 * @param {object} insight
 * @param {{ tickerPrice?: number, defaultAllocation?: number }} [opts]
 */
export function buildOrderDraftFromInsight(insight, opts = {}) {
  if (!insight?.symbol) return null;
  const signal = insight.signal || '';
  const side = signal === 'BUY' || signal === 'SELL' ? signal : null;
  if (!side) return null;

  const price = opts.tickerPrice ?? insight.levels?.entry_hint ?? 0;
  if (!price || price <= 0) return null;

  const sizeFactor = insight.sub_reports?.risk?.suggested_size_factor ?? 1;
  const allocation = (opts.defaultAllocation ?? DEFAULT_ALLOCATION) * sizeFactor;
  const quantity = Math.max(0.0001, allocation / price);

  const levels = insight.levels || {};
  let stop_loss_price;
  if (levels.stop_loss_distance != null) {
    stop_loss_price = side === 'BUY'
      ? price - levels.stop_loss_distance
      : price + levels.stop_loss_distance;
  }

  return {
    symbol: insight.symbol,
    side,
    orderType: 'MARKET',
    quantity: Number(quantity.toFixed(6)),
    stop_loss_price: stop_loss_price != null ? Number(stop_loss_price.toFixed(8)) : undefined,
    take_profit_price: levels.take_profit_price,
    sourceInsightId: insight.insight_id,
    sizeFactor,
    signal,
    confidence: insight.confidence,
    atrRegime: insight.sub_reports?.risk?.atr_regime,
    notional: Number((quantity * price).toFixed(2)),
  };
}
