/** Display helpers for persisted agent reasoning chains (GET /bots/{id}/reasoning). */

const AGENT_LABELS = {
  PRETRADE_INTEL: 'Pre-Trade Intel',
  RISK_SENTINEL: 'Risk Sentinel',
  REGIME_ROTATION: 'Regime Rotation',
  POSTTRADE_LEARNER: 'Post-Trade Learner',
};

export function formatReasoningAgent(agent) {
  if (!agent) return 'Agent';
  const key = String(agent).toUpperCase();
  if (AGENT_LABELS[key]) return AGENT_LABELS[key];
  return key
    .toLowerCase()
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/** Badge variant for a persisted verdict/decision. */
export function reasoningVerdictVariant(verdict) {
  const v = String(verdict || '').toUpperCase();
  if (v === 'VETO' || v === 'PAUSE') return 'destructive';
  if (v === 'REDUCE_SIZE') return 'sell';
  if (v === 'CONFIRM') return 'buy';
  return 'secondary';
}

/** Epoch-seconds (or ISO string) → compact local time. */
export function formatReasoningTime(ts) {
  if (ts == null) return '';
  const d = typeof ts === 'number'
    ? new Date(ts * 1000)
    : new Date(typeof ts === 'string' && !ts.endsWith('Z') && !/[+-]\d{2}:\d{2}$/.test(ts) ? `${ts}Z` : ts);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString();
}

export function normalizeReasoningObservation(raw) {
  if (!raw || typeof raw !== 'object') return null;
  return {
    source: String(raw.source ?? ''),
    signal: raw.signal ?? null,
    confidence: typeof raw.confidence === 'number' ? raw.confidence : null,
    detail: String(raw.detail ?? ''),
  };
}

/** Defensive normalize of one API row into a render-ready chain. */
export function normalizeReasoningChain(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const observations = Array.isArray(raw.observations) ? raw.observations : [];
  const vetoes = Array.isArray(raw.vetoes) ? raw.vetoes : [];
  return {
    id: raw.id ?? null,
    bot_id: raw.bot_id ?? null,
    agent: String(raw.agent ?? ''),
    verdict: raw.verdict ?? null,
    notes: raw.notes ?? '',
    observations: observations.map(normalizeReasoningObservation).filter(Boolean),
    vetoes: vetoes.map(String),
    size_multiplier: typeof raw.size_multiplier === 'number' ? raw.size_multiplier : null,
    ts: typeof raw.ts === 'number' ? raw.ts : null,
    created_at: raw.created_at ?? null,
  };
}

export function normalizeReasoningChains(rows) {
  if (!Array.isArray(rows)) return [];
  return rows.map(normalizeReasoningChain).filter(Boolean);
}
