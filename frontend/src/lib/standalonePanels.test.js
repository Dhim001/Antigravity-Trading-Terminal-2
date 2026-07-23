import { describe, expect, it } from 'vitest';
import {
  STANDALONE_PANELS,
  getStandalonePanelDef,
  isMlLabStandaloneLocation,
  isStandaloneLocation,
  readStandalonePanelQuery,
  standaloneIdForDockTab,
  standalonePanelUrl,
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
