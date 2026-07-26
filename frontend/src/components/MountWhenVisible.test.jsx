/**
 * DOM remount scenarios are covered by shouldRenderPanelChildren in
 * MountWhenVisible.test.js (node env). Keep this file empty of runnable
 * suites so vitest include of *.test.jsx does not require jsdom.
 */
import { describe, it } from 'vitest';

describe.skip('MountWhenVisible DOM (needs jsdom)', () => {
  it('placeholder', () => {});
});
