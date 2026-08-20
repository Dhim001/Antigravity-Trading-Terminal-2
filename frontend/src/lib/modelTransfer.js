/**
 * Cross-asset model transfer — donor-list filtering and train-payload shaping.
 *
 * A donor is a trained model version for the same strategy on a different
 * symbol. The target train warm-starts from the donor's checkpoint (deep
 * nets / RL) or recipe (GBM). The backend re-validates compatibility; these
 * helpers only shape the UI state → POST /ml/train config.donor payload.
 */

/** Strategies whose donor train supports the freeze-trunk (head-only) option. */
export const NN_TRANSFER_STRATEGIES = new Set([
  'LSTM_DIRECTION',
  'TCN_MULTI_HORIZON',
  'TRANSFORMER_SIGNAL',
  'GNN_CROSS_ASSET',
  'VAE_REGIME_DETECTOR',
]);

/** Strategies that support any donor transfer (weights or recipe). */
export const TRANSFER_STRATEGIES = new Set([
  ...NN_TRANSFER_STRATEGIES,
  'RL_PPO_AGENT',
  'ML_SIGNAL_BOOST',
]);

/** RL donor trains expose the scaler-strategy toggle (recompute vs carry). */
export const SCALER_STRATEGY_STRATEGIES = new Set(['RL_PPO_AGENT']);

export function transferSupported(strategy) {
  return TRANSFER_STRATEGIES.has(String(strategy || '').toUpperCase());
}

export function freezeTrunkSupported(strategy) {
  return NN_TRANSFER_STRATEGIES.has(String(strategy || '').toUpperCase());
}

export function scalerStrategySupported(strategy) {
  return SCALER_STRATEGY_STRATEGIES.has(String(strategy || '').toUpperCase());
}

/**
 * Normalize the donors endpoint payload into picker rows, excluding any row
 * that accidentally matches the target symbol. Newest first (server already
 * sorts, but stay defensive).
 */
export function normalizeDonorList(payload, targetSymbol) {
  const rows = Array.isArray(payload?.donors) ? payload.donors : [];
  const target = String(targetSymbol || '').toUpperCase();
  return rows
    .filter((d) => d && String(d.symbol || '').toUpperCase() !== target)
    .map((d) => ({
      symbol: String(d.symbol || '').toUpperCase(),
      versionId: d.version_id || null,
      trainedAt: d.trained_at || null,
      timeframe: d.timeframe || null,
      hasCheckpoint: Boolean(d.has_checkpoint),
      meanReturnPct: d.mean_return_pct ?? null,
      accuracy: d.accuracy ?? null,
    }))
    .sort((a, b) => String(b.trainedAt || '').localeCompare(String(a.trainedAt || '')));
}

/**
 * Shape the config.donor payload for POST /ml/train. Returns null when the
 * picker is disabled or no donor is selected — the train then runs
 * from-scratch exactly as before.
 */
export function buildDonorConfig({
  enabled,
  strategy,
  donorSymbol,
  donorVersionId,
  scalerStrategy = 'recompute',
  freezeTrunk = false,
} = {}) {
  if (!enabled) return null;
  const symbol = String(donorSymbol || '').trim().toUpperCase();
  if (!symbol || !transferSupported(strategy)) return null;
  const out = { symbol };
  if (donorVersionId) out.version_id = String(donorVersionId);
  if (scalerStrategySupported(strategy)) {
    out.scaler_strategy = scalerStrategy === 'carry' ? 'carry' : 'recompute';
  }
  if (freezeTrunkSupported(strategy) && freezeTrunk) {
    out.freeze_trunk = true;
  }
  return out;
}

/** Short lineage badge text for version rows: "from BTCUSDT · 2026-08-10". */
export function formatTransferBadge(transfer) {
  if (!transfer || typeof transfer !== 'object') return null;
  const donor = String(transfer.donor_symbol || '').toUpperCase();
  if (!donor) return null;
  const at = transfer.donor_trained_at;
  let datePart = '';
  if (at) {
    const d = new Date(at);
    if (!Number.isNaN(d.getTime())) {
      datePart = ` · ${d.toISOString().slice(0, 10)}`;
    }
  }
  const method = transfer.method === 'recipe_transfer' ? 'recipe' : null;
  return `from ${donor}${datePart}${method ? ` · ${method}` : ''}`;
}

/** Reason shown when the donor picker is disabled. */
export function donorDisabledReason({ enabled, supported, donors }) {
  if (enabled === false) return 'Model transfer is disabled on the backend';
  if (!supported) return 'This strategy does not support donor transfer';
  if (Array.isArray(donors) && donors.length === 0) {
    return 'No compatible donors — train another asset with this strategy first';
  }
  return null;
}
