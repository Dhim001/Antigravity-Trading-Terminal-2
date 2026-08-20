import { describe, expect, it } from 'vitest';
import {
  LIVE_PIN_PX,
  anchorQuery,
  escapeAttrValue,
  isPinnedToLive,
} from './usePinnedPrependScroll';

describe('isPinnedToLive', () => {
  it('is true only at the top of the console', () => {
    expect(isPinnedToLive(0)).toBe(true);
    expect(isPinnedToLive(LIVE_PIN_PX)).toBe(true);
    expect(isPinnedToLive(LIVE_PIN_PX + 1)).toBe(false);
    expect(isPinnedToLive(120)).toBe(false);
  });
});

describe('anchorQuery', () => {
  it('returns null for empty ids and quotes attribute values', () => {
    expect(anchorQuery(null)).toBeNull();
    expect(anchorQuery('')).toBeNull();
    expect(anchorQuery('log-1')).toBe(`[data-scroll-anchor-id="${escapeAttrValue('log-1')}"]`);
    expect(anchorQuery('x"y')).toContain('data-scroll-anchor-id');
    expect(escapeAttrValue('x"y')).not.toBe('x"y');
  });
});
