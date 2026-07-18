/** @vitest-environment node */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { WORKSPACE_PANEL_LABELS, focusWorkspacePanel, openModelTrainingDock } from './workspaceNav';

vi.mock('../store/useResearchStore', () => ({
  useResearchStore: {
    getState: () => ({
      setBacktestLabOpen: vi.fn(),
    }),
  },
}));

describe('workspaceNav', () => {
  let dispatchEvent;

  beforeEach(() => {
    dispatchEvent = vi.fn();
    globalThis.window = { dispatchEvent };
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
  });

  it('openModelTrainingDock targets ml-training', () => {
    openModelTrainingDock();
    expect(dispatchEvent.mock.calls[0][0].detail).toBe('ml-training');
  });

  it('ignores unknown panels', () => {
    focusWorkspacePanel('nope');
    expect(dispatchEvent).not.toHaveBeenCalled();
  });
});
