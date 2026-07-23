/**
 * Standalone panel windows — full document load at ?panel=<id>
 * (separate JS realm from the trading terminal, not a React portal).
 */

export const STANDALONE_PANEL_QUERY = 'panel';

/** @typedef {{
 *   id: string,
 *   query: string,
 *   windowName: string,
 *   channel: string,
 *   title: string,
 *   features: string,
 *   dockTabs?: string[],
 * }} StandalonePanelDef */

const DEFAULT_FEATURES =
  'width=1120,height=820,left=80,top=40,resizable=yes,scrollbars=yes';

/** @type {Record<string, StandalonePanelDef>} */
export const STANDALONE_PANELS = Object.freeze({
  'ml-lab': {
    id: 'ml-lab',
    query: 'ml-lab',
    windowName: 'tt-ml-lab',
    channel: 'tt-standalone:ml-lab',
    title: 'ML Lab · Antigravity',
    features: DEFAULT_FEATURES,
    dockTabs: ['ml-training'],
  },
  algo: {
    id: 'algo',
    query: 'algo',
    windowName: 'tt-algo',
    channel: 'tt-standalone:algo',
    title: 'Algo Bot · Antigravity',
    features: 'width=1080,height=860,left=60,top=40,resizable=yes,scrollbars=yes',
    dockTabs: ['algo'],
  },
  'backtest-lab': {
    id: 'backtest-lab',
    query: 'backtest-lab',
    windowName: 'tt-backtest-lab',
    channel: 'tt-standalone:backtest-lab',
    title: 'Backtest Lab · Antigravity',
    features: 'width=1200,height=860,left=40,top=30,resizable=yes,scrollbars=yes',
    dockTabs: [],
  },
  copilot: {
    id: 'copilot',
    query: 'copilot',
    windowName: 'tt-copilot',
    channel: 'tt-standalone:copilot',
    title: 'Copilot · Antigravity',
    features: 'width=720,height=900,left=100,top=40,resizable=yes,scrollbars=yes',
    dockTabs: ['copilot'],
  },
  insights: {
    id: 'insights',
    query: 'insights',
    windowName: 'tt-insights',
    channel: 'tt-standalone:insights',
    title: 'Insights · Antigravity',
    features: 'width=1100,height=860,left=70,top=40,resizable=yes,scrollbars=yes',
    dockTabs: ['scanner', 'analyst'],
  },
  automation: {
    id: 'automation',
    query: 'automation',
    windowName: 'tt-automation',
    channel: 'tt-standalone:automation',
    title: 'Automation Studio · Antigravity',
    features: 'width=1180,height=880,left=50,top=30,resizable=yes,scrollbars=yes',
    dockTabs: [],
  },
  portfolio: {
    id: 'portfolio',
    query: 'portfolio',
    windowName: 'tt-portfolio',
    channel: 'tt-standalone:portfolio',
    title: 'Portfolio · Antigravity',
    features: 'width=1280,height=900,left=40,top=20,resizable=yes,scrollbars=yes',
    dockTabs: [],
  },
});

export function getStandalonePanelDef(panelIdOrQuery) {
  const key = String(panelIdOrQuery || '').toLowerCase();
  if (STANDALONE_PANELS[key]) return STANDALONE_PANELS[key];
  return Object.values(STANDALONE_PANELS).find((p) => p.query === key) || null;
}

/** Dock tab id → standalone panel id (e.g. scanner → insights). */
export function standaloneIdForDockTab(tabId) {
  const t = String(tabId || '');
  for (const p of Object.values(STANDALONE_PANELS)) {
    if (p.dockTabs?.includes(t)) return p.id;
  }
  return null;
}

export function readStandalonePanelQuery(
  search = typeof window !== 'undefined' ? window.location.search : '',
) {
  try {
    const q = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);
    const raw = q.get(STANDALONE_PANEL_QUERY);
    if (!raw) return null;
    const def = getStandalonePanelDef(raw);
    return def ? def.id : null;
  } catch {
    return null;
  }
}

export function isStandaloneLocation(search) {
  return Boolean(readStandalonePanelQuery(search));
}

/** @deprecated prefer isStandaloneLocation / readStandalonePanelQuery */
export function isMlLabStandaloneLocation(search) {
  return readStandalonePanelQuery(search) === 'ml-lab';
}

export function standalonePanelUrl(panelId) {
  const def = getStandalonePanelDef(panelId);
  if (!def) return typeof window !== 'undefined' ? window.location.href : '/';
  if (typeof window === 'undefined') return `?${STANDALONE_PANEL_QUERY}=${def.query}`;
  const url = new URL(window.location.href);
  url.searchParams.set(STANDALONE_PANEL_QUERY, def.query);
  url.hash = '';
  return url.toString();
}

function detachedRegistry() {
  if (typeof window === 'undefined') return null;
  if (!window.__ttDetachedPanels) window.__ttDetachedPanels = {};
  return window.__ttDetachedPanels;
}

export function focusStandaloneWindow(panelId) {
  const def = getStandalonePanelDef(panelId);
  if (!def) return false;
  const win = detachedRegistry()?.[def.id];
  if (!win || win.closed) return false;
  try {
    win.focus();
    return true;
  } catch {
    return false;
  }
}

/**
 * Open a standalone panel window (call from a user click).
 * @returns {Window | null}
 */
export function openStandaloneWindow(panelId) {
  const def = getStandalonePanelDef(panelId);
  if (!def || typeof window === 'undefined') return null;
  const reg = detachedRegistry();
  const existing = reg?.[def.id];
  if (existing && !existing.closed) {
    try {
      existing.focus();
    } catch {
      /* ignore */
    }
    return existing;
  }

  const win = window.open(standalonePanelUrl(def.id), def.windowName, def.features);
  if (!win) return null;
  if (reg) reg[def.id] = win;
  return win;
}

export function closeStandaloneWindow(panelId) {
  const def = getStandalonePanelDef(panelId);
  if (!def || typeof window === 'undefined') return;
  const reg = detachedRegistry();
  const win = reg?.[def.id];
  try {
    if (win && !win.closed) win.close();
  } catch {
    /* ignore */
  }
  if (reg) delete reg[def.id];
}

export function broadcastStandaloneEvent(panelId, type, payload = {}) {
  const def = getStandalonePanelDef(panelId);
  if (!def) return;
  try {
    const bc = new BroadcastChannel(def.channel);
    bc.postMessage({ panelId: def.id, type, ...payload, at: Date.now() });
    bc.close();
  } catch {
    try {
      localStorage.setItem(
        `${def.channel}:ping`,
        JSON.stringify({ panelId: def.id, type, ...payload, at: Date.now() }),
      );
    } catch {
      /* ignore */
    }
  }
}

/**
 * Subscribe to one panel's lifecycle (or all if panelId omitted).
 * @returns {() => void}
 */
export function subscribeStandaloneEvents(panelId, handler) {
  const defs = panelId
    ? [getStandalonePanelDef(panelId)].filter(Boolean)
    : Object.values(STANDALONE_PANELS);

  const channels = [];
  const onStorage = (e) => {
    for (const def of defs) {
      if (e.key !== `${def.channel}:ping` || !e.newValue) continue;
      try {
        handler(JSON.parse(e.newValue));
      } catch {
        /* ignore */
      }
    }
  };

  for (const def of defs) {
    try {
      const bc = new BroadcastChannel(def.channel);
      bc.onmessage = (ev) => {
        if (ev?.data?.type) handler(ev.data);
      };
      channels.push(bc);
    } catch {
      /* ignore */
    }
  }

  if (typeof window !== 'undefined') {
    window.addEventListener('storage', onStorage);
  }

  return () => {
    for (const bc of channels) {
      try {
        bc.close();
      } catch {
        /* ignore */
      }
    }
    if (typeof window !== 'undefined') {
      window.removeEventListener('storage', onStorage);
    }
  };
}

/* ── ML Lab backward-compatible aliases ─────────────────────────────────── */

export const ML_LAB_PANEL_QUERY = STANDALONE_PANEL_QUERY;
export const ML_LAB_PANEL_VALUE = 'ml-lab';
export const ML_LAB_WINDOW_NAME = 'tt-ml-lab';
export const ML_LAB_CHANNEL = 'tt-standalone:ml-lab';
export const ML_LAB_WINDOW_FEATURES = DEFAULT_FEATURES;

export function mlLabStandaloneUrl() {
  return standalonePanelUrl('ml-lab');
}

export function focusMlLabWindow() {
  return focusStandaloneWindow('ml-lab');
}

export function openMlLabStandaloneWindow() {
  return openStandaloneWindow('ml-lab');
}

export function broadcastMlLabEvent(type, payload = {}) {
  return broadcastStandaloneEvent('ml-lab', type, payload);
}

export function subscribeMlLabEvents(handler) {
  return subscribeStandaloneEvents('ml-lab', handler);
}
