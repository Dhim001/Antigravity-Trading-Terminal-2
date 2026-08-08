import { getStrategyMeta } from '@/config/strategies';
import { fmtMetric } from '@/components/ml-lab/MlLabConstants';

/** Keep full version id readable; only truncate extremely long pins. */
export function formatVersionId(versionId) {
  if (!versionId) return null;
  const s = String(versionId);
  if (s.length > 28) return `${s.slice(0, 26)}…`;
  return s;
}

/** @deprecated use formatVersionId — kept for older imports/tests */
export function shortVersion(versionId) {
  return formatVersionId(versionId);
}

export function runModelLabel(run) {
  // Model column = strategy family; custom names belong in Version.
  const meta = getStrategyMeta(run?.strategy);
  return meta?.label || meta?.shortLabel || run?.strategy || '—';
}

export function runResultLabel(run) {
  if (!run?.ok) {
    if (run?.error === 'cancelled') return 'cancelled';
    return 'fail';
  }
  const metricHint = run.metrics?.mean_oos_accuracy
    ?? run.metrics?.mean_accuracy
    ?? run.metrics?.val_accuracy
    ?? run.metrics?.best_mean_return
    ?? run.metrics?.mean_return_pct
    ?? run.metrics?.pbo;
  if (metricHint == null) return 'ok';
  const formatted = fmtMetric(metricHint, 3, 'mean_oos_accuracy') ?? metricHint;
  return `ok · ${formatted}`;
}

export function runTitle(run) {
  const parts = [
    run?.strategy,
    run?.symbol,
    run?.timeframe,
    run?.display_name,
    run?.version_id && `v${run.version_id}`,
    run?.artifact,
    run?.error,
  ].filter(Boolean);
  return parts.join(' · ');
}

/**
 * Version cell content: custom name (if any) + immutable version id.
 * @returns {{ name: string|null, id: string|null, emptyLabel: string }}
 */
export function runVersionParts(run, versions = []) {
  const versionId = run?.version_id ? String(run.version_id) : null;
  let displayName = run?.display_name || run?.metrics?.display_name || null;

  if (!displayName && versionId && Array.isArray(versions) && versions.length) {
    const hit = versions.find((v) => (
      v?.version_id === versionId
      || v?.trained_at === versionId
      || String(v?.version_id || '') === versionId
    ));
    if (hit?.display_name) displayName = String(hit.display_name);
  }

  if (!versionId && !displayName) {
    if (run?.kind === 'validate') {
      return { name: null, id: null, emptyLabel: 'no pin (WF)' };
    }
    if (run?.kind === 'hyperparam_sweep') {
      return { name: null, id: null, emptyLabel: 'sweep' };
    }
    return { name: null, id: null, emptyLabel: '—' };
  }

  return {
    name: displayName ? String(displayName) : null,
    id: formatVersionId(versionId),
    emptyLabel: '—',
  };
}

/** Flat string for simple consumers/tests. */
export function runVersionCell(run, versions = []) {
  const parts = runVersionParts(run, versions);
  if (parts.name && parts.id) return `${parts.name} · ${parts.id}`;
  return parts.name || parts.id || parts.emptyLabel;
}
