/**
 * Persist FlexLayout selected tabs so SPA reload (header Refresh UI) restores
 * the user's panels instead of jumping back to Chart / Positions / Scanner.
 */
import { Actions } from 'flexlayout-react';

const STORAGE_KEY = 'tt-flexlayout-selected-components';

/**
 * @param {import('flexlayout-react').Model} model
 * @returns {string[]}
 */
export function listSelectedComponents(model) {
  if (!model?.visitNodes) return [];
  const selected = [];
  model.visitNodes((node) => {
    if (node.getType?.() !== 'tabset') return;
    const sel = node.getSelectedNode?.();
    const comp = sel?.getComponent?.();
    if (comp) selected.push(String(comp));
  });
  return selected;
}

/**
 * @param {string[]} components
 */
export function saveSelectedComponents(components) {
  try {
    if (typeof sessionStorage === 'undefined') return;
    const list = Array.isArray(components)
      ? components.filter((c) => typeof c === 'string' && c)
      : [];
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(list));
  } catch {
    /* ignore quota / private mode */
  }
}

/**
 * @returns {string[]}
 */
export function loadSelectedComponents() {
  try {
    if (typeof sessionStorage === 'undefined') return [];
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((c) => typeof c === 'string' && c) : [];
  } catch {
    return [];
  }
}

/**
 * Snapshot current selection into sessionStorage.
 * @param {import('flexlayout-react').Model} model
 */
export function persistFlexLayoutSelection(model) {
  saveSelectedComponents(listSelectedComponents(model));
}

/**
 * Re-select tabs by component id after a cold Model.fromJson.
 * @param {import('flexlayout-react').Model} model
 * @param {(model: import('flexlayout-react').Model, component: string) => void} focusFn
 */
export function restoreFlexLayoutSelection(model, focusFn) {
  const saved = loadSelectedComponents();
  if (!saved.length || typeof focusFn !== 'function') return;
  for (const component of saved) {
    try {
      focusFn(model, component);
    } catch {
      /* tab may have been closed */
    }
  }
}

/**
 * Wrap Layout onModelChange — always persist after FlexLayout mutates.
 * @param {import('flexlayout-react').Model} model
 * @param {(model: import('flexlayout-react').Model) => void} [userHandler]
 */
export function makePersistOnModelChange(model, userHandler) {
  return (m) => {
    persistFlexLayoutSelection(m || model);
    if (typeof userHandler === 'function') userHandler(m || model);
  };
}

/**
 * Prefer SELECT_TAB actions for immediate persist (before paint settles).
 * @param {import('flexlayout-react').Action} action
 * @param {import('flexlayout-react').Model} model
 */
export function persistOnSelectAction(action, model) {
  try {
    if (action?.type === Actions.SELECT_TAB) {
      // Selection applied after doAction returns; defer one tick.
      queueMicrotask(() => persistFlexLayoutSelection(model));
    }
  } catch {
    /* ignore */
  }
  return action;
}
