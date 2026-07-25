/** @vitest-environment node */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { WORKSPACE_PANEL_LABELS, focusWorkspacePanel, openModelTrainingDock } from './workspaceNav';

const openStandaloneWindow = vi.fn(() => ({ focus: vi.fn(), closed: false }));
const broadcastTerminalNav = vi.fn();
const focusStandaloneForDockTab = vi.fn(() => false);
const isStandaloneLocation = vi.fn(() => false);
const standaloneIdForDockTab = vi.fn(() => null);

vi.mock('../store/useResearchStore', () => ({
  useResearchStore: {
    getState: () => ({
      setBacktestLabOpen: vi.fn(),
    }),
  },
}));

vi.mock('../store/useSettingsStore', () => ({
  useSettingsStore: {
    getState: () => ({
      settings: { workspace: { detachedTabs: ['ml-training'] } },
    }),
  },
}));

vi.mock('./standalonePanels', () => ({
  broadcastTerminalNav: (...args) => broadcastTerminalNav(...args),
  focusStandaloneForDockTab: (...args) => focusStandaloneForDockTab(...args),
  isStandaloneLocation: (...args) => isStandaloneLocation(...args),
  openStandaloneWindow: (...args) => openStandaloneWindow(...args),
  standaloneIdForDockTab: (...args) => standaloneIdForDockTab(...args),
}));

describe('workspaceNav', () => {
  let dispatchEvent;

  beforeEach(() => {
    dispatchEvent = vi.fn();
    globalThis.window = { dispatchEvent, location: { search: '' } };
    openStandaloneWindow.mockClear();
    broadcastTerminalNav.mockClear();
    focusStandaloneForDockTab.mockClear();
    isStandaloneLocation.mockReturnValue(false);
    standaloneIdForDockTab.mockReturnValue(null);
    focusStandaloneForDockTab.mockReturnValue(false);
  });
  afterEach(() => {
    delete globalThis.window;
    vi.restoreAllMocks();
  });

  it('lists ml-training panel', () => {
    expect(WORKSPACE_PANEL_LABELS['ml-training']).toBe('ML Training');
  });

  it('dispatches dock-tab for known panels', () => {
    focusWorkspacePanel('ml-training');
    expect(dispatchEvent).toHaveBeenCalledTimes(1);
    const evt = dispatchEvent.mock.calls[0][0];
    expect(evt.type).toBe('dock-tab');
    expect(evt.detail).toBe('ml-training');
    expect(focusStandaloneForDockTab).toHaveBeenCalledWith('ml-training', ['ml-training']);
  });

  it('openModelTrainingDock targets ml-training', () => {
    openModelTrainingDock();
    expect(dispatchEvent.mock.calls[0][0].detail).toBe('ml-training');
  });

  it('ignores unknown panels', () => {
    focusWorkspacePanel('nope');
    expect(dispatchEvent).not.toHaveBeenCalled();
  });

  it('from standalone relays nav instead of local dock-tab', () => {
    isStandaloneLocation.mockReturnValue(true);
    standaloneIdForDockTab.mockReturnValue('algo');
    globalThis.window.location.search = '?panel=ml-lab';
    globalThis.window.opener = null;

    focusWorkspacePanel('algo');

    expect(broadcastTerminalNav).toHaveBeenCalledWith({ type: 'focus-panel', panelId: 'algo' });
    expect(openStandaloneWindow).toHaveBeenCalledWith('algo');
    expect(dispatchEvent).not.toHaveBeenCalled();
  });
});
