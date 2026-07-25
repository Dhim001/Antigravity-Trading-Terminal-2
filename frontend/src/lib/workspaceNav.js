/**
 * Navigate workspace FlexLayout panels (and close overlays that would hide them).
 */
import { useResearchStore } from '../store/useResearchStore';
import { useSettingsStore } from '../store/useSettingsStore';
import {
  broadcastTerminalNav,
  focusStandaloneForDockTab,
  isStandaloneLocation,
  openStandaloneWindow,
  standaloneIdForDockTab,
} from './standalonePanels';

/** @type {Record<string, string>} */
export const WORKSPACE_PANEL_LABELS = {
  watchlist: 'Watchlist',
  movers: 'Movers',
  chart: 'Chart',
  'order-entry': 'Trade',
  'order-book': 'Book',
  'depth-chart': 'Depth',
  footprint: 'Footprint',
  positions: 'Positions',
  orders: 'Orders',
  balances: 'Balances',
  algo: 'Algo',
  scanner: 'Scanner',
  analyst: 'Analyst',
  copilot: 'Copilot',
  'ml-training': 'ML Training',
  reconcile: 'Reconcile',
  bots: 'Bot History',
  ticks: 'Ticks',
  history: 'History',
  equity: 'Equity',
};

function detachedTabsFromSettings() {
  try {
    const tabs = useSettingsStore.getState().settings?.workspace?.detachedTabs;
    return Array.isArray(tabs) ? tabs : [];
  } catch {
    return [];
  }
}

/**
 * Focus/open a detached standalone window when navigating to its dock tab.
 * Safe to call from any `dock-tab` listener on the main terminal.
 * @param {string} tabId
 * @returns {boolean}
 */
export function focusDetachedPanelForTab(tabId) {
  return focusStandaloneForDockTab(tabId, detachedTabsFromSettings());
}

function relayDockTabToOpener(tabId) {
  try {
    if (typeof window === 'undefined') return;
    const opener = window.opener;
    if (!opener || opener.closed) return;
    opener.dispatchEvent(new CustomEvent('dock-tab', { detail: tabId }));
    try {
      opener.focus();
    } catch {
      /* ignore */
    }
  } catch {
    /* cross-origin / closed */
  }
}

/**
 * Open a workspace panel by component id (e.g. ml-training, algo).
 * Closes Backtest Lab so the FlexLayout panel is visible underneath.
 * When the target tab is detached, focuses its standalone window.
 * When called from a standalone window, relays to the main terminal.
 * @param {string} panelId
 */
export function focusWorkspacePanel(panelId) {
  const id = String(panelId || '').trim();
  if (!id || !WORKSPACE_PANEL_LABELS[id]) return;

  try {
    useResearchStore.getState().setBacktestLabOpen(false);
  } catch {
    /* store may be unavailable in tests */
  }

  if (typeof window !== 'undefined' && isStandaloneLocation(window.location.search)) {
    // Detached realm has no FlexLayout — ask the main terminal to navigate,
    // and open the target's own standalone window when it is also detachable.
    broadcastTerminalNav({ type: 'focus-panel', panelId: id });
    relayDockTabToOpener(id);
    const sid = standaloneIdForDockTab(id);
    if (sid) openStandaloneWindow(sid);
    return;
  }

  if (typeof window === 'undefined') return;

  focusDetachedPanelForTab(id);
  window.dispatchEvent(new CustomEvent('dock-tab', { detail: id }));
}

/** Jump to Model Training (ML Training panel). */
export function openModelTrainingDock() {
  focusWorkspacePanel('ml-training');
}
