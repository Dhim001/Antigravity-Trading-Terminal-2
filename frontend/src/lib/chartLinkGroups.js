/** Multi-chart link group helpers (B4). */

export const LINK_GROUPS = ['A', 'B', 'C'];

export const LINK_GROUP_COLORS = {
  A: '#3b82f6',
  B: '#f59e0b',
  C: '#a855f7',
};

/** Cycle: A → B → C → unlinked → A */
export function cycleLinkGroup(current) {
  if (!current) return 'A';
  const idx = LINK_GROUPS.indexOf(current);
  if (idx < 0 || idx >= LINK_GROUPS.length - 1) return null;
  return LINK_GROUPS[idx + 1];
}

export function defaultLinkGroups(count, chartLinkMode = 'all') {
  const n = Math.max(1, count || 1);
  if (chartLinkMode === 'focused') {
    return Array.from({ length: n }, (_, i) => (i === 0 ? 'A' : null));
  }
  return Array.from({ length: n }, () => 'A');
}

export function resizeLinkGroups(prev, count, chartLinkMode = 'all') {
  const defaults = defaultLinkGroups(count, chartLinkMode);
  const next = [...defaults];
  for (let i = 0; i < Math.min(prev?.length ?? 0, count); i++) {
    if (prev[i]) next[i] = prev[i];
  }
  return next;
}
