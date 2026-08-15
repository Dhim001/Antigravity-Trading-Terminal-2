/**
 * Pure helpers used by useMlLabState (testable without React / RTL).
 */
import { isMlStrategy } from '@/config/strategies';
import { normalizeStatusTimeframe } from '@/lib/mlTrainingSession';

/**
 * Backend retrain pending/cooldown key: SYMBOL:STRATEGY:TF
 * (see ml_retrain_scheduler._cooldown_key).
 */
export function retrainQueueKey(symbol, strategy, timeframe = '1m') {
  const tf = normalizeStatusTimeframe(timeframe);
  return `${String(symbol || '').toUpperCase()}:${String(strategy || '').toUpperCase()}:${tf}`;
}

/** Match pending/action rows for a symbol+strategy (+ optional TF). */
export function matchesRetrainTarget(entry, symbol, strategy, timeframe = '1m') {
  if (!entry) return false;
  const sym = String(symbol || '').toUpperCase();
  const strat = String(strategy || '').toUpperCase();
  const tf = normalizeStatusTimeframe(timeframe);
  const entrySym = String(entry.symbol || '').toUpperCase();
  const entryStrat = String(entry.strategy || '').toUpperCase();
  if (entrySym && entryStrat && (entrySym !== sym || entryStrat !== strat)) {
    return false;
  }
  const key = String(entry.key || '');
  if (!key) {
    const entryTf = entry.timeframe != null
      ? normalizeStatusTimeframe(entry.timeframe)
      : null;
    return entrySym === sym && entryStrat === strat
      && (entryTf == null || entryTf === tf);
  }
  const full = retrainQueueKey(sym, strat, tf);
  if (key === full) return true;
  // Legacy keys without timeframe: SYMBOL:STRATEGY (exactly two segments).
  const parts = key.split(':');
  if (parts.length === 2) {
    return parts[0] === sym && parts[1] === strat;
  }
  return false;
}

/**
 * Whether the ML training session belongs to the lab's current symbol/strategy.
 */
export function sessionMatchesLab(mlSession, activeSymbol, strategy) {
  if (!mlSession) return false;
  return (
    String(mlSession.symbol || '').toUpperCase() === String(activeSymbol || '').toUpperCase()
    && String(mlSession.strategy || '').toUpperCase() === String(strategy || '').toUpperCase()
  );
}

/**
 * Derive busy / progress flags from session + match.
 * @returns {{
 *   jobMatches: boolean,
 *   training: boolean,
 *   validating: boolean,
 *   jobProgress: object|null,
 *   serverProgress: object|null,
 *   pollLog: array,
 *   activeJobId: string|null,
 *   validation: object|null,
 *   busyElsewhere: boolean,
 *   sessionTuningHint: boolean,
 * }}
 */
export function deriveMlLabJobFlags(mlSession, activeSymbol, strategy) {
  const jobMatches = sessionMatchesLab(mlSession, activeSymbol, strategy);
  const tuning = Boolean(jobMatches && mlSession?.tuning);
  // Auto-Tune owns its own progress UI — do not also drive the Train/Validate bar.
  const trainOrValidate = Boolean(
    jobMatches
    && !mlSession?.tuning
    && (mlSession?.training || mlSession?.validating)
  );
  return {
    jobMatches,
    training: Boolean(trainOrValidate && mlSession?.training),
    validating: Boolean(trainOrValidate && mlSession?.validating),
    jobProgress: trainOrValidate ? (mlSession?.jobProgress ?? null) : null,
    serverProgress: trainOrValidate ? (mlSession?.serverProgress ?? null) : null,
    pollLog: trainOrValidate ? (mlSession?.pollLog || []) : [],
    activeJobId: trainOrValidate ? (mlSession?.jobId ?? null) : null,
    validation: jobMatches ? (mlSession?.validation ?? null) : null,
    busyElsewhere: Boolean(
      (mlSession?.training || mlSession?.validating || mlSession?.tuning)
      && !jobMatches
      && (mlSession?.symbol || mlSession?.strategy),
    ),
    sessionTuningHint: tuning,
  };
}

/**
 * Normalize retrain-status `pending` map into lab list rows.
 * @param {Record<string, object>|null|undefined} pendingMap
 */
export function normalizeRetrainPending(pendingMap) {
  const map = pendingMap && typeof pendingMap === 'object' ? pendingMap : {};
  return Object.entries(map)
    .map(([key, info]) => ({
      key,
      strategy: info?.strategy,
      symbol: info?.symbol,
      timeframe: info?.timeframe || normalizeStatusTimeframe(
        (String(key).split(':')[2]) || '1m',
      ),
      reasons: Array.isArray(info?.reasons) ? info.reasons : [],
      requested_at: info?.requested_at,
    }))
    .filter((p) => isMlStrategy(p.strategy));
}

/**
 * Filter retrain_actions to ML strategies only.
 * @param {unknown} actions
 */
export function normalizeRetrainActions(actions) {
  if (!Array.isArray(actions)) return [];
  return actions.filter((a) => isMlStrategy(a?.strategy));
}

/**
 * Watchlist options for the ML Lab symbol selector.
 * Ensures the active chart symbol stays selectable even if missing from the list.
 * @param {unknown} symbolsList
 * @param {string|null|undefined} activeSymbol
 * @returns {string[]}
 */
export function resolveMlLabSymbolOptions(symbolsList, activeSymbol) {
  const seen = new Set();
  const out = [];
  for (const sym of Array.isArray(symbolsList) ? symbolsList : []) {
    const s = String(sym || '').trim();
    if (!s || seen.has(s)) continue;
    seen.add(s);
    out.push(s);
  }
  const active = String(activeSymbol || '').trim();
  if (active && !seen.has(active)) out.unshift(active);
  return out;
}
