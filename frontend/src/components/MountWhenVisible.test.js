import { describe, expect, it } from 'vitest';
import { shouldRenderPanelChildren } from '../components/MountWhenVisible.jsx';

describe('shouldRenderPanelChildren', () => {
  it('prefers live FlexLayout visibility over deferred React state', () => {
    expect(shouldRenderPanelChildren({
      nodeVisible: true,
      visible: false,
      keptWarm: false,
    })).toBe(true);
  });

  it('keeps children warm after hide until keep-alive expires', () => {
    expect(shouldRenderPanelChildren({
      nodeVisible: false,
      visible: false,
      keptWarm: true,
    })).toBe(true);
  });

  it('shows placeholder only when hidden and keep-alive expired', () => {
    expect(shouldRenderPanelChildren({
      nodeVisible: false,
      visible: false,
      keptWarm: false,
    })).toBe(false);
  });
});
