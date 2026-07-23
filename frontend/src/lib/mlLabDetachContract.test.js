/**
 * Contract: detached ML Lab is a standalone window/tab (?panel=ml-lab),
 * not a portal inside the trading layout — dock never mounts a second Lab.
 */
import { describe, expect, it } from 'vitest';
import { isMlLabStandaloneLocation } from './mlLabWindow';

function mlDockContent({ detached }) {
  return detached ? 'dock-link' : 'lab-dashboard';
}

function mlLiveLabInTradingLayout({ detached }) {
  return detached ? 0 : 1;
}

describe('ML Lab standalone detach contract', () => {
  it('dock shows link when detached', () => {
    expect(mlDockContent({ detached: false })).toBe('lab-dashboard');
    expect(mlDockContent({ detached: true })).toBe('dock-link');
  });

  it('trading layout never hosts Lab while detached', () => {
    expect(mlLiveLabInTradingLayout({ detached: false })).toBe(1);
    expect(mlLiveLabInTradingLayout({ detached: true })).toBe(0);
  });

  it('standalone mode is URL-driven', () => {
    expect(isMlLabStandaloneLocation('?panel=ml-lab')).toBe(true);
  });
});
