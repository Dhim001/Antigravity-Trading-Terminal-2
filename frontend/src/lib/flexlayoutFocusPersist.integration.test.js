import { describe, expect, it, vi } from 'vitest';
import { focusFlexLayoutDockGroup } from './flexlayoutFocus';
import {
  persistFlexLayoutSelection,
  restoreFlexLayoutSelection,
  saveSelectedComponents,
  loadSelectedComponents,
} from './flexlayoutPersist';

function mockSessionStorage() {
  const store = new Map();
  globalThis.sessionStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => { store.set(k, String(v)); },
    removeItem: (k) => { store.delete(k); },
    clear: () => { store.clear(); },
  };
}

describe('flexlayout dock-group + persist integration', () => {
  it('keeps already-selected ml-training when focusing intelligence group', () => {
    const mlTab = { getId: () => 'tab-ml' };
    const scannerTab = { getId: () => 'tab-scan' };
    const parent = { getSelectedNode: () => mlTab };
    mlTab.getParent = () => parent;
    scannerTab.getParent = () => parent;

    const model = {
      visitNodes(cb) {
        cb({ getType: () => 'tab', getComponent: () => 'ml-training', getId: () => 'tab-ml', getParent: () => parent });
        cb({ getType: () => 'tab', getComponent: () => 'scanner', getId: () => 'tab-scan', getParent: () => parent });
      },
      doAction: vi.fn(),
    };

    const ok = focusFlexLayoutDockGroup(model, 'intelligence');
    expect(ok).toBe(true);
    expect(model.doAction).not.toHaveBeenCalled();
  });

  it('restore focuses saved components including ml-training', () => {
    mockSessionStorage();
    saveSelectedComponents(['chart', 'ml-training', 'positions']);
    expect(loadSelectedComponents()).toContain('ml-training');

    const focusFn = vi.fn();
    restoreFlexLayoutSelection({}, focusFn);
    expect(focusFn).toHaveBeenCalledWith({}, 'chart');
    expect(focusFn).toHaveBeenCalledWith({}, 'ml-training');
    expect(focusFn).toHaveBeenCalledWith({}, 'positions');
  });

  it('persistFlexLayoutSelection writes selected components', () => {
    mockSessionStorage();
    const model = {
      visitNodes(cb) {
        cb({
          getType: () => 'tabset',
          getSelectedNode: () => ({ getComponent: () => 'ml-training' }),
        });
      },
    };
    persistFlexLayoutSelection(model);
    expect(loadSelectedComponents()).toEqual(['ml-training']);
  });
});
