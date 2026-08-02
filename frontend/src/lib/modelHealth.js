/**
 * Model freshness / health assessment for ML bots and batch train scope.
 */

const MS_PER_HOUR = 1000 * 60 * 60;

export function modelAgeHours(modelStatus, now = Date.now()) {
  const raw = modelStatus?.trained_at;
  if (!raw) return null;
  const ts = new Date(raw).getTime();
  if (!Number.isFinite(ts)) return null;
  return Math.max(0, (now - ts) / MS_PER_HOUR);
}

/**
 * @param {object} [_bot] — reserved for future bot-runtime signals
 * @param {object|null|undefined} modelStatus
 * @returns {{ level: 'fresh'|'aging'|'stale'|'untrained'|'unknown', label: string, color: string, tooltip: string }}
 */
export function assessModelHealth(_bot, modelStatus) {
  // Cache miss / still loading — never treat as Untrained (false retrain CTA).
  if (modelStatus == null) {
    return {
      level: 'unknown',
      label: 'Checking',
      color: 'var(--text-muted, #94a3b8)',
      tooltip: 'Checking model status…',
    };
  }

  if (!modelStatus.trained) {
    return {
      level: 'untrained',
      label: 'Untrained',
      color: 'var(--text-muted, #94a3b8)',
      tooltip: 'No model trained for this symbol/strategy',
    };
  }

  const ageHours = modelAgeHours(modelStatus);
  const ageLabel = formatAgeLabel(ageHours);
  const wfOk = Boolean(modelStatus.walk_forward?.ok);
  const wfPart = wfOk ? 'WF validated' : 'WF not validated';

  // Freshly trained models are Fresh even before walk-forward — missing WF must
  // not look like "Aging"/retrain-needed right after Lab train completes.
  if (ageHours != null && ageHours < 24) {
    return {
      level: 'fresh',
      label: 'Fresh',
      color: 'var(--color-up, #22c55e)',
      tooltip: `Model trained ${ageLabel}, ${wfPart}`,
    };
  }

  if (ageHours != null && ageHours < 48) {
    return {
      level: 'aging',
      label: 'Aging',
      color: 'var(--color-warn, #eab308)',
      tooltip: `Model trained ${ageLabel}, ${wfPart}`,
    };
  }

  // Trained but missing/unparseable trained_at — show Trained, not Stale.
  if (ageHours == null) {
    return {
      level: 'aging',
      label: 'Trained',
      color: 'var(--color-warn, #eab308)',
      tooltip: `Model trained (unknown age), ${wfPart}`,
    };
  }

  return {
    level: 'stale',
    label: 'Stale',
    color: 'var(--color-down, #ef4444)',
    tooltip: `Model trained ${ageLabel}, ${wfPart}`,
  };
}

export function shouldSuggestRetrain(bot, modelStatus) {
  const health = assessModelHealth(bot, modelStatus);
  return health.level === 'stale' || health.level === 'untrained';
}

/**
 * True when model is trained and older than maxAgeHours.
 * Missing/unparseable trained_at is not stale (matches assessModelHealth "Trained"
 * and avoids batch "stale" scope false positives right after train).
 * Untrained models are never stale (use shouldSuggestRetrain / assessModelHealth).
 */
export function isModelStale(modelStatus, maxAgeHours = 48) {
  if (!modelStatus?.trained) return false;
  const age = modelAgeHours(modelStatus);
  if (age == null) return false;
  return age >= maxAgeHours;
}

function formatAgeLabel(ageHours) {
  if (ageHours == null || !Number.isFinite(ageHours)) return 'unknown ago';
  if (ageHours < 1) {
    const mins = Math.max(1, Math.round(ageHours * 60));
    return `${mins}m ago`;
  }
  if (ageHours < 48) {
    const h = Math.round(ageHours);
    return `${h}h ago`;
  }
  const days = Math.round(ageHours / 24);
  return `${days}d ago`;
}
