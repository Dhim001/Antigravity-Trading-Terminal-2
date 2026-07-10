import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  formatCooloffRemaining,
  remainingCooloffSec,
  riskHoldBadgeLabel,
  effectiveRiskHold,
} from './botRiskHold';

describe('botRiskHold', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-10T12:00:00.000Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('formats cooloff countdown', () => {
    expect(formatCooloffRemaining(45)).toBe('45s');
    expect(formatCooloffRemaining(125)).toBe('2m 05s');
    expect(formatCooloffRemaining(3665)).toBe('1h 1m');
  });

  it('computes remaining from cooloff_until', () => {
    const hold = {
      kind: 'cooloff',
      cooloff_until: '2026-07-10T12:02:30.000Z',
      remaining_sec: 999,
    };
    expect(remainingCooloffSec(hold)).toBe(150);
  });

  it('builds badge labels', () => {
    expect(riskHoldBadgeLabel({
      kind: 'cooloff',
      cooloff_until: '2026-07-10T12:01:00.000Z',
    })).toBe('COOLING OFF · 1m 00s');
    expect(riskHoldBadgeLabel({
      kind: 'streak_limit',
      consecutive_losses: 5,
      max_consecutive_losses: 5,
    })).toBe('LOSS STREAK · 5/5');
  });

  it('effectiveRiskHold drops expired cooloff', () => {
    const hold = {
      kind: 'cooloff',
      cooloff_until: '2026-07-10T11:59:00.000Z',
      remaining_sec: 0,
    };
    expect(effectiveRiskHold(hold)).toBeNull();
    expect(riskHoldBadgeLabel(hold)).toBeNull();
  });

  it('effectiveRiskHold keeps active cooloff and streak', () => {
    expect(effectiveRiskHold({
      kind: 'cooloff',
      cooloff_until: '2026-07-10T12:01:00.000Z',
    })?.kind).toBe('cooloff');
    expect(effectiveRiskHold({
      kind: 'streak_limit',
      consecutive_losses: 5,
      max_consecutive_losses: 5,
    })?.kind).toBe('streak_limit');
    expect(effectiveRiskHold({
      kind: 'drawdown',
      drawdown_pct: 15,
      max_drawdown_pct: 10,
    })?.kind).toBe('drawdown');
  });

  it('builds drawdown badge label', () => {
    expect(riskHoldBadgeLabel({
      kind: 'drawdown',
      drawdown_pct: 15,
      max_drawdown_pct: 10,
    })).toBe('MAX DD · 15%/10%');
  });
});
