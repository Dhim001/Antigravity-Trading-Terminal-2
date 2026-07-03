import { describe, it, expect } from 'vitest';
import {
  clampSlTpPrice,
  hitTestSlTp,
  buildSlTpGraphic,
  kindFromTarget,
} from './slTpOverlay';

describe('clampSlTpPrice', () => {
  it('keeps SL below entry for long', () => {
    expect(clampSlTpPrice(95, 'BUY', 100, 'sl')).toBe(95);
    expect(clampSlTpPrice(105, 'BUY', 100, 'sl')).toBeLessThan(100);
  });

  it('keeps TP above entry for long', () => {
    expect(clampSlTpPrice(110, 'BUY', 100, 'tp')).toBe(110);
    expect(clampSlTpPrice(90, 'BUY', 100, 'tp')).toBeGreaterThan(100);
  });

  it('keeps SL above entry for short', () => {
    expect(clampSlTpPrice(105, 'SELL', 100, 'sl')).toBe(105);
    expect(clampSlTpPrice(95, 'SELL', 100, 'sl')).toBeGreaterThan(100);
  });

  it('keeps TP below entry for short', () => {
    expect(clampSlTpPrice(90, 'SELL', 100, 'tp')).toBe(90);
    expect(clampSlTpPrice(110, 'SELL', 100, 'tp')).toBeLessThan(100);
  });
});

describe('hitTestSlTp', () => {
  it('returns nearest line within hit px', () => {
    const lines = [
      { id: 'sl', y: 100 },
      { id: 'tp', y: 200 },
    ];
    expect(hitTestSlTp(102, lines)).toBe('sl');
    expect(hitTestSlTp(198, lines)).toBe('tp');
    expect(hitTestSlTp(150, lines)).toBeNull();
  });
});

describe('buildSlTpGraphic', () => {
  it('emits line + handle for live SL', () => {
    const { elements, hitLines } = buildSlTpGraphic({
      priceToY: (p) => 500 - p,
      plotLeft: 10,
      plotRight: 400,
      live: { stop_loss_price: 95 },
    });
    expect(hitLines.some((l) => l.id === 'sl')).toBe(true);
    expect(elements.some((e) => e.id === 'sl-handle')).toBe(true);
  });
});

describe('kindFromTarget', () => {
  it('maps draft targets', () => {
    expect(kindFromTarget('draft-sl')).toBe('sl');
    expect(kindFromTarget('tp')).toBe('tp');
  });
});
