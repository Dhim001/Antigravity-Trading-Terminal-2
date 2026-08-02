import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  assessModelHealth,
  shouldSuggestRetrain,
  isModelStale,
  modelAgeHours,
} from './modelHealth';

function hoursAgo(h) {
  return new Date(Date.now() - h * 60 * 60 * 1000).toISOString();
}

describe('assessModelHealth', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns unknown when status is null (loading — not Untrained)', () => {
    const h = assessModelHealth({}, null);
    expect(h.level).toBe('unknown');
    expect(h.label).toBe('Checking');
    expect(shouldSuggestRetrain({}, null)).toBe(false);
  });

  it('returns untrained when not trained', () => {
    const h = assessModelHealth({}, { trained: false });
    expect(h.level).toBe('untrained');
    expect(h.label).toBe('Untrained');
  });

  it('returns fresh when <24h even without walk_forward ok', () => {
    const h = assessModelHealth(null, {
      trained: true,
      trained_at: hoursAgo(0.1),
      walk_forward: { ok: false },
    });
    expect(h.level).toBe('fresh');
    expect(shouldSuggestRetrain(null, {
      trained: true,
      trained_at: hoursAgo(0.1),
      walk_forward: { ok: false },
    })).toBe(false);
  });

  it('returns fresh when <24h and walk_forward ok', () => {
    const h = assessModelHealth(null, {
      trained: true,
      trained_at: hoursAgo(2),
      walk_forward: { ok: true },
    });
    expect(h.level).toBe('fresh');
  });

  it('returns aging when between 24h and 48h', () => {
    const h = assessModelHealth(null, {
      trained: true,
      trained_at: hoursAgo(30),
      walk_forward: { ok: true },
    });
    expect(h.level).toBe('aging');
  });

  it('returns Trained (not Stale) when trained_at missing', () => {
    const h = assessModelHealth(null, {
      trained: true,
      walk_forward: { ok: true },
    });
    expect(h.level).toBe('aging');
    expect(h.label).toBe('Trained');
    expect(shouldSuggestRetrain(null, { trained: true })).toBe(false);
  });

  it('returns stale when >= 48h', () => {
    const h = assessModelHealth(null, {
      trained: true,
      trained_at: hoursAgo(72),
      walk_forward: { ok: true },
    });
    expect(h.level).toBe('stale');
  });
});

describe('shouldSuggestRetrain', () => {
  it('suggests for stale and untrained only', () => {
    expect(shouldSuggestRetrain({}, { trained: false })).toBe(true);
    expect(shouldSuggestRetrain({}, {
      trained: true,
      trained_at: hoursAgo(72),
    })).toBe(true);
    expect(shouldSuggestRetrain({}, {
      trained: true,
      trained_at: hoursAgo(2),
      walk_forward: { ok: true },
    })).toBe(false);
  });
});

describe('isModelStale', () => {
  it('is false for untrained', () => {
    expect(isModelStale({ trained: false })).toBe(false);
  });

  it('is true when age exceeds maxAgeHours', () => {
    expect(isModelStale({ trained: true, trained_at: hoursAgo(49) }, 48)).toBe(true);
    expect(isModelStale({ trained: true, trained_at: hoursAgo(10) }, 48)).toBe(false);
  });

  it('is false when trained_at missing (unknown age ≠ stale)', () => {
    expect(isModelStale({ trained: true })).toBe(false);
  });
});

describe('modelAgeHours', () => {
  it('computes age from trained_at', () => {
    const now = Date.now();
    const age = modelAgeHours({ trained_at: new Date(now - 3 * 3600_000).toISOString() }, now);
    expect(age).toBeCloseTo(3, 1);
  });
});
