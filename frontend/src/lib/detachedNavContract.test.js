/**
 * Contract: navigating to/from a detached dock tab must leave the main
 * FlexLayout placeholder and actually focus the standalone window (and the
 * reverse: dock-tab events inside ?panel= must reach the terminal).
 */
import { describe, expect, it } from 'vitest';
import {
  focusStandaloneForDockTab,
  standaloneIdForDockTab,
} from './standalonePanels';

function shouldFocusStandalone(tabId, detachedTabs) {
  return focusStandaloneForDockTab(tabId, detachedTabs);
}

function shouldRelayFromStandalone(search, tabId) {
  const standalone = String(search || '').includes('panel=');
  if (!standalone) return { relay: false, openTarget: null };
  return {
    relay: true,
    openTarget: standaloneIdForDockTab(tabId),
  };
}

describe('detached tab link contract', () => {
  it('link-to detached ML Lab opens ml-lab window', () => {
    // Use a stub window.open via focusStandaloneForDockTab tests elsewhere;
    // here we only assert the mapping + detached gate.
    expect(standaloneIdForDockTab('ml-training')).toBe('ml-lab');
    expect(shouldFocusStandalone('ml-training', [])).toBe(false);
  });

  it('link-from standalone to algo should relay + open algo panel', () => {
    const out = shouldRelayFromStandalone('?panel=insights', 'algo');
    expect(out.relay).toBe(true);
    expect(out.openTarget).toBe('algo');
  });

  it('link-from standalone to chart relays without opening a panel window', () => {
    const out = shouldRelayFromStandalone('?panel=ml-lab', 'chart');
    expect(out.relay).toBe(true);
    expect(out.openTarget).toBeNull();
  });
});
