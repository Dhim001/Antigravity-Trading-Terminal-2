/**
 * Pure helpers for RL episode replay (shared with unit tests).
 */

export const ACTION_LABELS = {
  0: 'HOLD',
  1: 'BUY',
  2: 'SELL',
  3: 'CLOSE',
};

export function normalizeAction(action) {
  if (Array.isArray(action) && action.length) return Number(action[0]);
  if (typeof action === 'number') return action;
  if (typeof action === 'string') {
    const key = action.toUpperCase();
    const idx = Object.entries(ACTION_LABELS).find(([, v]) => v === key);
    if (idx) return Number(idx[0]);
    const n = Number(action);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

export function actionLabel(action) {
  const n = normalizeAction(action);
  if (n != null && Number.isInteger(n) && ACTION_LABELS[n] != null) return ACTION_LABELS[n];
  if (typeof action === 'number') return action.toFixed(2);
  if (typeof action === 'string') return action;
  return '—';
}

export function actionTone(action) {
  const label = actionLabel(action);
  if (label === 'BUY') return 'up';
  if (label === 'SELL' || label === 'CLOSE') return 'down';
  return 'neutral';
}

/** Next index with BUY/SELL/CLOSE, or -1 if none. */
export function findNextTradeAction(steps, fromIndex) {
  if (!Array.isArray(steps) || steps.length === 0) return -1;
  for (let i = fromIndex + 1; i < steps.length; i += 1) {
    const label = actionLabel(steps[i]?.action);
    if (label === 'BUY' || label === 'SELL' || label === 'CLOSE') return i;
  }
  return -1;
}

/** Map episode step index onto a downsampled sparkline length. */
export function sparkIndexForStep(stepIndex, stepCount, sparkLen) {
  if (!stepCount || sparkLen < 2) return 0;
  const maxStep = Math.max(stepCount - 1, 1);
  return Math.round((stepIndex / maxStep) * (sparkLen - 1));
}
