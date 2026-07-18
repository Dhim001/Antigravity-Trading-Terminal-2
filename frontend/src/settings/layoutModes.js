/** @typedef {'trade' | 'analyze' | 'automate' | 'portfolio'} LayoutMode */
/** @typedef {'portfolio' | 'intelligence' | 'automation' | 'data'} DockGroup */

export const LAYOUT_MODES = /** @type {const} */ ([
  'trade',
  'analyze',
  'automate',
  'portfolio',
]);

export const DOCK_GROUPS = /** @type {const} */ ([
  'portfolio',
  'intelligence',
  'automation',
  'data',
]);

export const PRESET_CATEGORIES = {
  scalping: { label: 'Scalping', icon: 'Zap', color: 'text-yellow-500' },
  swing: { label: 'Swing', icon: 'TrendingUp', color: 'text-blue-500' },
  analysis: { label: 'Analysis', icon: 'Brain', color: 'text-purple-500' },
  portfolio: { label: 'Portfolio', icon: 'Landmark', color: 'text-emerald-500' },
  monitoring: { label: 'Monitoring', icon: 'Activity', color: 'text-orange-500' },
};

/** @type {Record<DockGroup, { label: string; tabs: string[] }>} */
export const DOCK_GROUP_CONFIG = {
  portfolio: {
    label: 'Portfolio',
    tabs: ['positions', 'orders', 'balances'],
  },
  intelligence: {
    label: 'Intelligence',
    tabs: ['scanner', 'analyst', 'copilot', 'ml-training'],
  },
  automation: {
    label: 'Automation',
    tabs: ['algo', 'reconcile', 'bots'],
  },
  data: {
    label: 'Data',
    tabs: ['history', 'equity', 'ticks'],
  },
};

/** @param {string} tabId */
export function dockGroupForTab(tabId) {
  for (const [group, cfg] of Object.entries(DOCK_GROUP_CONFIG)) {
    if (cfg.tabs.includes(tabId)) return /** @type {DockGroup} */ (group);
  }
  return 'portfolio';
}

/** @type {Record<LayoutMode, { label: string; description: string; dockHeight: number; dockTab: string; dockGroup: DockGroup; rightPanel: boolean; rightPanelTab: string; dockVisible: boolean; showCommandBar: boolean }>} */
export const LAYOUT_MODE_CONFIG = {
  trade: {
    label: 'Trade',
    description: 'Chart-first manual trading',
    dockHeight: 260,
    dockTab: 'positions',
    dockGroup: 'portfolio',
    rightPanel: true,
    rightPanelTab: 'trade',
    dockVisible: true,
    showCommandBar: true,
  },
  analyze: {
    label: 'Analyze',
    description: 'Scanner & analyst research',
    dockHeight: 220,
    dockTab: 'scanner',
    dockGroup: 'intelligence',
    rightPanel: false,
    rightPanelTab: 'trade',
    dockVisible: true,
    showCommandBar: true,
  },
  automate: {
    label: 'Automate',
    description: 'Bot deployment & history',
    dockHeight: 300,
    dockTab: 'algo',
    dockGroup: 'automation',
    rightPanel: false,
    rightPanelTab: 'trade',
    dockVisible: true,
    showCommandBar: true,
  },
  portfolio: {
    label: 'Portfolio',
    description: 'P&L, history & balances',
    dockHeight: 420,
    dockTab: 'equity',
    dockGroup: 'data',
    rightPanel: false,
    rightPanelTab: 'trade',
    dockVisible: true,
    showCommandBar: true,
  },
};

/** @param {LayoutMode} mode */
export function applyLayoutMode(mode) {
  return LAYOUT_MODE_CONFIG[mode] || LAYOUT_MODE_CONFIG.trade;
}
