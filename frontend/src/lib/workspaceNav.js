/**
 * Navigate workspace FlexLayout panels (and close overlays that would hide them).
 */
import { useResearchStore } from '../store/useResearchStore';

/** @type {Record<string, string>} */
export const WORKSPACE_PANEL_LABELS = {
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

/**
 * Open a workspace panel by component id (e.g. ml-training, algo).
 * Closes Backtest Lab so the FlexLayout panel is visible underneath.
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

  window.dispatchEvent(new CustomEvent('dock-tab', { detail: id }));
}

/** Jump to Model Training (ML Training panel). */
export function openModelTrainingDock() {
  focusWorkspacePanel('ml-training');
}
