/**
 * Select or reopen a FlexLayout tab by component id.
 */
import { Actions, DockLocation } from 'flexlayout-react';
import { WORKSPACE_PANEL_LABELS } from './workspaceNav';
import { DOCK_GROUP_CONFIG } from '../settings/layoutModes';

/**
 * @param {import('flexlayout-react').Model} model
 * @param {string} component
 * @returns {import('flexlayout-react').TabNode | null}
 */
function findTabByComponent(model, component) {
  let found = null;
  model.visitNodes((node) => {
    if (found) return;
    if (node.getType() === 'tab' && node.getComponent() === component) {
      found = node;
    }
  });
  return found;
}

/**
 * Prefer a tabset that already hosts a sibling from the same family.
 * @param {import('flexlayout-react').Model} model
 * @param {string} component
 */
function findSiblingTabSet(model, component) {
  /** @type {Record<string, string[]>} */
  const seeds = {
    'ml-training': ['copilot', 'scanner', 'analyst', 'algo'],
    algo: ['bots', 'reconcile', 'scanner', 'copilot'],
    scanner: ['analyst', 'copilot', 'ml-training'],
    analyst: ['scanner', 'copilot', 'ml-training'],
    copilot: ['scanner', 'analyst', 'ml-training'],
    positions: ['orders', 'balances', 'history'],
    orders: ['positions', 'balances', 'history'],
    balances: ['positions', 'orders'],
    history: ['positions', 'orders', 'equity'],
    equity: ['history', 'ticks'],
    bots: ['algo', 'reconcile'],
    reconcile: ['algo', 'bots'],
    ticks: ['equity', 'history'],
    'order-entry': ['order-book', 'depth-chart', 'footprint'],
    'order-book': ['order-entry', 'depth-chart', 'footprint'],
    'depth-chart': ['order-entry', 'order-book', 'footprint'],
    footprint: ['order-entry', 'order-book', 'depth-chart'],
    watchlist: ['chart'],
    chart: ['watchlist'],
  };
  const preferred = seeds[component] || [];
  for (const seed of preferred) {
    const tab = findTabByComponent(model, seed);
    if (tab?.getParent?.()) return tab.getParent();
  }
  return model.getActiveTabset() || model.getFirstTabSet();
}

/**
 * @param {import('flexlayout-react').Model} model
 * @param {string} component
 * @returns {boolean}
 */
export function focusFlexLayoutComponent(model, component) {
  if (!model || !component || !WORKSPACE_PANEL_LABELS[component]) return false;

  const existing = findTabByComponent(model, component);
  if (existing) {
    model.doAction(Actions.selectTab(existing.getId()));
    return true;
  }

  const tabset = findSiblingTabSet(model, component);
  if (!tabset) return false;

  model.doAction(Actions.addNode(
    {
      type: 'tab',
      name: WORKSPACE_PANEL_LABELS[component],
      component,
    },
    tabset.getId(),
    DockLocation.CENTER,
    -1,
    true,
  ));
  return true;
}

/**
 * Focus ``primary`` unless it is already the selected tab in its tabset — then focus ``fallback``.
 * Used for sidebar-toggle (watchlist ↔ chart).
 * @param {import('flexlayout-react').Model} model
 * @param {string} primary
 * @param {string} fallback
 */
export function toggleFlexLayoutComponent(model, primary, fallback) {
  if (!model || !primary) return false;
  const tab = findTabByComponent(model, primary);
  if (tab) {
    const parent = tab.getParent?.();
    const selected = parent?.getSelectedNode?.();
    if (selected && selected.getId() === tab.getId()) {
      return focusFlexLayoutComponent(model, fallback);
    }
  }
  return focusFlexLayoutComponent(model, primary);
}

/**
 * Focus the first tab of a legacy dock group (portfolio / intelligence / …).
 * @param {import('flexlayout-react').Model} model
 * @param {string} group
 */
export function focusFlexLayoutDockGroup(model, group) {
  const cfg = DOCK_GROUP_CONFIG[group];
  if (!cfg?.tabs?.length) return false;
  return focusFlexLayoutComponent(model, cfg.tabs[0]);
}
