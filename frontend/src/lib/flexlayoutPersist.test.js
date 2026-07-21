import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import {
  listSelectedComponents,
  loadSelectedComponents,
  saveSelectedComponents,
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

describe('flexlayoutPersist', () => {
  beforeEach(() => {
    mockSessionStorage();
  });
  afterEach(() => {
    sessionStorage.clear();
  });

  it('round-trips selected component ids', () => {
    saveSelectedComponents(['ml-training', 'chart', 'positions']);
    expect(loadSelectedComponents()).toEqual(['ml-training', 'chart', 'positions']);
  });

  it('ignores non-arrays', () => {
    sessionStorage.setItem('tt-flexlayout-selected-components', '{"x":1}');
    expect(loadSelectedComponents()).toEqual([]);
  });

  it('lists selected components from a mock model', () => {
    const model = {
      visitNodes(cb) {
        cb({
          getType: () => 'tabset',
          getSelectedNode: () => ({ getComponent: () => 'ml-training' }),
        });
        cb({
          getType: () => 'tabset',
          getSelectedNode: () => ({ getComponent: () => 'chart' }),
        });
        cb({ getType: () => 'tab' });
      },
    };
    expect(listSelectedComponents(model)).toEqual(['ml-training', 'chart']);
  });
});
