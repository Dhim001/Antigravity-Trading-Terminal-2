/**
 * Select or reopen a FlexLayout tab by component id.
 */
import { Actions, DockLocation } from 'flexlayout-react';
import { WORKSPACE_PANEL_LABELS } from './workspaceNav';

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
