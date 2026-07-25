import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import {
  STANDALONE_PANELS,
  getStandalonePanelDef,
  isMlLabStandaloneLocation,
  isStandaloneLocation,
  readStandalonePanelQuery,
  standaloneIdForDockTab,
  standalonePanelUrl,
  focusStandaloneForDockTab,
} from './standalonePanels';

describe('standalonePanels catalog', () => {
  it('includes the ordered detach sequence', () => {
    expect(Object.keys(STANDALONE_PANELS)).toEqual(
      expect.arrayContaining([
        'ml-lab',
        'algo',
        'backtest-lab',
        'copilot',
        'insights',
        'automation',
        'portfolio',
      ]),
    );
  });

  it('maps dock tabs to panel ids', () => {
    expect(standaloneIdForDockTab('ml-training')).toBe('ml-lab');
    expect(standaloneIdForDockTab('algo')).toBe('algo');
    expect(standaloneIdForDockTab('copilot')).toBe('copilot');
    expect(standaloneIdForDockTab('scanner')).toBe('insights');
    expect(standaloneIdForDockTab('analyst')).toBe('insights');
    expect(standaloneIdForDockTab('positions')).toBeNull();
  });

  it('parses ?panel= query for every catalog entry', () => {
    for (const id of Object.keys(STANDALONE_PANELS)) {
      expect(readStandalonePanelQuery(`?panel=${id}`)).toBe(id);
      expect(isStandaloneLocation(`?panel=${id}`)).toBe(true);
    }
    expect(readStandalonePanelQuery('?panel=nope')).toBeNull();
    expect(readStandalonePanelQuery('')).toBeNull();
    expect(readStandalonePanelQuery('%%%')).toBeNull();
  });

  it('keeps ML Lab alias behavior', () => {
    expect(isMlLabStandaloneLocation('?panel=ml-lab')).toBe(true);
    expect(isMlLabStandaloneLocation('?panel=algo')).toBe(false);
  });

  it('builds urls with panel query', () => {
    for (const id of Object.keys(STANDALONE_PANELS)) {
      const url = standalonePanelUrl(id);
      expect(url).toContain(`panel=${id}`);
    }
    expect(getStandalonePanelDef('backtest-lab')?.title).toMatch(/Backtest Lab/);
  });
});

describe('focusStandaloneForDockTab', () => {
  let openSpy;

  beforeEach(() => {
    openSpy = vi.fn(() => ({ focus: vi.fn(), closed: false }));
    globalThis.window = {
      open: openSpy,
      location: { href: 'http://127.0.0.1:5176/' },
      __ttDetachedPanels: {},
    };
  });

  afterEach(() => {
    delete globalThis.window;
    vi.restoreAllMocks();
  });

  it('opens standalone when dock tab is detached', () => {
    const opened = focusStandaloneForDockTab('ml-training', ['ml-training']);
    expect(opened).toBe(true);
    expect(openSpy).toHaveBeenCalled();
    expect(String(openSpy.mock.calls[0][0])).toContain('panel=ml-lab');
  });

  it('no-ops when tab is not detached', () => {
    expect(focusStandaloneForDockTab('ml-training', [])).toBe(false);
    expect(openSpy).not.toHaveBeenCalled();
  });

  it('opens insights for either scanner or analyst when detached', () => {
    expect(focusStandaloneForDockTab('analyst', ['scanner', 'analyst'])).toBe(true);
    expect(String(openSpy.mock.calls[0][0])).toContain('panel=insights');
  });
});
