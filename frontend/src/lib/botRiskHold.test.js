import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  formatCooloffRemaining,
  remainingCooloffSec,
  riskHoldBadgeLabel,
  effectiveRiskHold,
  botRuntimeActivityHint,
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
    expect(riskHoldBadgeLabel({
      kind: 'pretrade_streak',
      consecutive_losses: 3,
      cooloff_until: '2026-07-10T12:01:00.000Z',
    })).toBe('STREAK PAUSE · 3 · 1m 00s');
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

  it('handles dd_budget hold kind end-to-end', () => {
    const hold = {
      kind: 'dd_budget',
      tier: 2,
      consumed_pct: 90,
      budget_pct: 10,
      reason: 'DD budget 90% consumed',
      block_reason: 'DD budget 90% consumed (≥80%) — entries frozen, flattening position.',
    };
    expect(effectiveRiskHold(hold)?.kind).toBe('dd_budget');
    expect(riskHoldBadgeLabel(hold)).toBe('DD BUDGET · 90%');
    expect(botRuntimeActivityHint({
      status: 'RUNNING',
      last_signal_at: null,
      risk_hold: hold,
    })?.kind).toBe('held');
    expect(botRuntimeActivityHint({
      status: 'RUNNING',
      last_signal_at: null,
      risk_hold: hold,
    })?.label).toBe('Held · DD budget');
  });

  it('botRuntimeActivityHint prefers cooling off / held over no signal', () => {
    expect(botRuntimeActivityHint({
      status: 'RUNNING',
      last_signal_at: null,
      risk_hold: { kind: 'cooloff', cooloff_until: '2026-07-10T12:01:00.000Z' },
    })?.kind).toBe('cooling_off');

    expect(botRuntimeActivityHint({
      status: 'RUNNING',
      last_signal_at: null,
      risk_hold: {
        kind: 'streak_limit',
        consecutive_losses: 5,
        max_consecutive_losses: 5,
      },
    })?.kind).toBe('held');

    expect(botRuntimeActivityHint({
      status: 'RUNNING',
      last_signal_at: null,
    })?.kind).toBe('no_signal');

    expect(botRuntimeActivityHint({
      status: 'RUNNING',
      last_signal_at: '2026-07-10T11:00:00.000Z',
    })).toBeNull();

    expect(botRuntimeActivityHint({ status: 'STOPPED' })).toBeNull();
  });

  it('botRuntimeActivityHint surfaces safe mode on RUNNING bots', () => {
    const hint = botRuntimeActivityHint(
      { status: 'RUNNING', last_signal_at: '2026-07-10T11:00:00.000Z' },
      { safeModeActive: true },
    );
    expect(hint?.kind).toBe('held');
    expect(hint?.label).toBe('Safe mode');
    expect(botRuntimeActivityHint(
      { status: 'PAUSED', last_signal_at: null },
      { safeModeActive: true },
    )?.label).not.toBe('Safe mode');
  });

  it('botRuntimeActivityHint marks paused bots as not evaluating', () => {
    const hint = botRuntimeActivityHint({ status: 'PAUSED', last_signal_at: '2026-08-12T07:05:01Z' });
    expect(hint?.kind).toBe('held');
    expect(hint?.label).toBe('Not evaluating');
  });
});
