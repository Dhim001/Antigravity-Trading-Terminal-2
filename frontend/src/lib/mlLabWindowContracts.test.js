/**
 * Contract tests for ML Lab window → bar/knob sync (mirrors ModelTrainingDashboard helpers).
 * Validate defaults to capacity parity with Train (accuracy-first).
 */
import { describe, expect, it } from 'vitest';

function estimateTrainingBars(monthsValue, tfValue) {
  const months = Number(monthsValue) || 3;
  const secs = ({ '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400 })[tfValue] || 60;
  const hard = 100_000;
  const ideal = Math.floor(months * 30 * 86400 / secs);
  if (secs > 60) return Math.max(500, Math.min(ideal, hard));
  const cap1m = ({ 1: 12000, 3: 25000, 6: 40000, 12: 50000 })[months] ?? 25000;
  return Math.max(500, Math.min(ideal, cap1m, hard));
}

/** Capacity-parity Validate — same calendar depth as Train (RL soft-capped). */
function estimateValidateBars(monthsValue, tfValue, strategy) {
  const trainBars = estimateTrainingBars(monthsValue, tfValue);
  if (strategy === 'RL_PPO_AGENT') {
    return Math.max(2_000, Math.min(trainBars, 20_000));
  }
  return trainBars;
}

function suggestedNFolds(monthsValue, strategy) {
  if (strategy === 'RL_PPO_AGENT') return 2;
  const months = Number(monthsValue) || 3;
  if (months >= 12) return 4;
  if (months >= 6) return 3;
  return 3;
}

describe('ML Lab window sync contracts', () => {
  it('6mo · 5m train targets full calendar (not ~8k scale crush)', () => {
    const bars = estimateTrainingBars(6, '5m');
    expect(bars).toBe(51_840);
  });

  it('6mo · 5m validate matches train at capacity parity', () => {
    const train = estimateTrainingBars(6, '5m');
    const validate = estimateValidateBars(6, '5m', 'TRANSFORMER_SIGNAL');
    expect(validate).toBe(train);
  });

  it('changing months changes validate budget', () => {
    const v1 = estimateValidateBars(1, '5m', 'TRANSFORMER_SIGNAL');
    const v6 = estimateValidateBars(6, '5m', 'TRANSFORMER_SIGNAL');
    const v12 = estimateValidateBars(12, '5m', 'TRANSFORMER_SIGNAL');
    expect(v1).toBeLessThan(v6);
    expect(v6).toBeLessThanOrEqual(v12);
  });

  it('RL validate uses deep window soft-capped at 20k', () => {
    expect(estimateValidateBars(3, '1m', 'RL_PPO_AGENT')).toBe(20_000);
    expect(estimateValidateBars(1, '1m', 'RL_PPO_AGENT')).toBe(
      Math.max(2_000, Math.min(estimateTrainingBars(1, '1m'), 20_000)),
    );
    expect(suggestedNFolds(12, 'RL_PPO_AGENT')).toBe(2);
  });

  it('longer windows suggest more folds', () => {
    expect(suggestedNFolds(3, 'TRANSFORMER_SIGNAL')).toBe(3);
    expect(suggestedNFolds(12, 'TRANSFORMER_SIGNAL')).toBe(4);
  });
});
