import { describe, expect, it } from 'vitest';
import {
  ML_BACKTEST_RANGE_HOLDOUT,
  ML_FREE_RANGE_DAYS,
  TA_BACKTEST_RANGE_DAYS,
  coerceBacktestDaysForStrategy,
  estimateHoldoutDays,
  resolveHoldoutDaysFromStatus,
  resolveMlBacktestDaysPayload,
} from './mlBacktestRange';

describe('mlBacktestRange', () => {
  it('estimates holdout like the backend default_holdout_days', () => {
    expect(estimateHoldoutDays(1)).toBe(7);
    expect(estimateHoldoutDays(3)).toBe(14);
    expect(estimateHoldoutDays(12)).toBe(54);
    expect(estimateHoldoutDays(36)).toBe(60);
  });

  it('prefers calendar holdout_days from model status', () => {
    expect(resolveHoldoutDaysFromStatus({ data_calendar: { holdout_days: 21 } }, {})).toBe(21);
    expect(resolveHoldoutDaysFromStatus(null, { training_window_months: 3 })).toBe(14);
  });

  it('coerces legacy 7d to holdout for ML and restores numeric for TA', () => {
    expect(coerceBacktestDaysForStrategy('7', { isMl: true })).toBe(ML_BACKTEST_RANGE_HOLDOUT);
    expect(coerceBacktestDaysForStrategy('30', { isMl: true })).toBe('30');
    expect(coerceBacktestDaysForStrategy(ML_BACKTEST_RANGE_HOLDOUT, { isMl: false })).toBe('14');
    expect(coerceBacktestDaysForStrategy('7', { isMl: false })).toBe('7');
  });

  it('resolves holdout sentinel and free payloads', () => {
    expect(resolveMlBacktestDaysPayload('holdout', 21, { isMl: true })).toEqual({
      days: 21,
      ml_backtest_range: 'holdout',
    });
    expect(resolveMlBacktestDaysPayload('30', 21, { isMl: true })).toEqual({
      days: 30,
      ml_backtest_range: 'free',
    });
    expect(resolveMlBacktestDaysPayload('7', 14, { isMl: false })).toEqual({
      days: 7,
      ml_backtest_range: undefined,
    });
  });

  it('keeps TA options including 7 and ML free options without bare 7', () => {
    expect(TA_BACKTEST_RANGE_DAYS).toContain('7');
    expect(ML_FREE_RANGE_DAYS).not.toContain('7');
    expect(ML_FREE_RANGE_DAYS[0]).toBe('14');
  });
});
