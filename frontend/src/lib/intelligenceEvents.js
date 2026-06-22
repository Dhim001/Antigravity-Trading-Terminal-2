/** Cross-surface intelligence navigation (Scanner ↔ Analyst ↔ Hub). */

import { useStore } from '../store/useStore';

/**
 * Focus analyst on a symbol — dock or Insights Hub.
 * @param {{ symbol?: string, expandLatest?: boolean, openHub?: boolean, preview?: boolean }} opts
 */
export function focusAnalyst({
  symbol,
  expandLatest = true,
  openHub = false,
  preview = false,
} = {}) {
  if (symbol) {
    useStore.getState().setActiveSymbol(symbol);
    window.dispatchEvent(new CustomEvent('analyst-focus', {
      detail: { symbol, expandLatest, preview },
    }));
  }
  if (openHub) {
    window.dispatchEvent(new CustomEvent('insights-hub-open'));
    window.dispatchEvent(new CustomEvent('insights-hub-tab', { detail: 'analyst' }));
  } else {
    window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'analyst' }));
  }
}

/** Open Insights Hub on scanner tab (optionally after scan). */
export function openScannerHub() {
  window.dispatchEvent(new CustomEvent('insights-hub-open'));
  window.dispatchEvent(new CustomEvent('insights-hub-tab', { detail: 'scanner' }));
}
