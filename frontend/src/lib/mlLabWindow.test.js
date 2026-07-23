import { describe, expect, it } from 'vitest';
import {
  isMlLabStandaloneLocation,
  mlLabStandaloneUrl,
  ML_LAB_PANEL_VALUE,
} from './mlLabWindow';

describe('mlLabWindow standalone URL', () => {
  it('detects ?panel=ml-lab', () => {
    expect(isMlLabStandaloneLocation('?panel=ml-lab')).toBe(true);
    expect(isMlLabStandaloneLocation('panel=ml-lab')).toBe(true);
    expect(isMlLabStandaloneLocation('?panel=other')).toBe(false);
    expect(isMlLabStandaloneLocation('')).toBe(false);
  });

  it('builds standalone url with panel query', () => {
    // jsdom / vitest may not have a full location; function still returns a usable string.
    const url = mlLabStandaloneUrl();
    expect(url).toContain(`${'panel'}=${ML_LAB_PANEL_VALUE}`);
  });
});
