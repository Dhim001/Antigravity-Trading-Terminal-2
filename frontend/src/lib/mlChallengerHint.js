/**
 * Champion / challenger helpers for Model Training dock (ML Lab §3.4).
 *
 * Walk-forward validates the *live* model root — it does not score a specific
 * version snapshot. Activate is only offered when validation metadata names a
 * challenger version that is not already live; otherwise dismiss-only.
 */

/**
 * @param {number|null|undefined} challengerOos
 * @param {number|null|undefined} championOos
 * @param {number} [minDelta=0.002]
 * @returns {boolean}
 */
export function challengerBeatsChampion(challengerOos, championOos, minDelta = 0.002) {
  if (challengerOos == null || championOos == null || challengerOos === '' || championOos === '') {
    return false;
  }
  const c = Number(challengerOos);
  const h = Number(championOos);
  if (!Number.isFinite(c) || !Number.isFinite(h)) return false;
  return c > h + Number(minDelta || 0);
}

/**
 * Resolve Activate target only when validation explicitly names a version
 * that is not already current.
 * @param {Array<{ version_id?: string, trained_at?: string, is_current?: boolean }>|null|undefined} versions
 * @param {string|null|undefined} challengerVersionId
 */
export function pickChallengerVersion(versions, challengerVersionId) {
  const needle = challengerVersionId ? String(challengerVersionId) : '';
  if (!needle || !Array.isArray(versions) || versions.length === 0) return null;
  const match = versions.find((v) => {
    if (!v) return false;
    const id = v.version_id || v.trained_at;
    return id && String(id) === needle;
  });
  if (!match || match.is_current) return null;
  return match;
}

/**
 * Build UI hint after a successful validate.
 * Champion OOS must be prior walk-forward mean (same metric family) — never
 * fall back to in-sample val_accuracy.
 * @param {{
 *   validation: object,
 *   championOos: number|null|undefined,
 *   versions?: array,
 * }} args
 */
export function buildChallengerHint({
  validation,
  championOos,
  versions,
}) {
  if (!validation || validation.ok === false) return null;
  if (championOos == null || championOos === '') return null;
  if (!Number.isFinite(Number(championOos))) return null;
  const challengerOos = validation.mean_accuracy
    ?? validation.aggregate?.mean_oos_accuracy
    ?? null;
  if (!challengerBeatsChampion(challengerOos, championOos)) return null;
  const namedId = validation.version_id
    || validation.challenger_version
    || validation.challenger_version_id
    || null;
  const version = pickChallengerVersion(versions, namedId);
  return {
    championOos: Number(championOos),
    challengerOos: Number(challengerOos),
    version,
    canActivate: Boolean(version),
    alreadyLive: !version,
  };
}
