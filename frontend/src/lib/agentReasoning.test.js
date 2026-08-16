import { describe, expect, it } from 'vitest';
import {
  formatReasoningAgent,
  formatReasoningTime,
  normalizeReasoningChain,
  normalizeReasoningChains,
  reasoningVerdictVariant,
} from './agentReasoning';

describe('formatReasoningAgent', () => {
  it('maps known agents to friendly labels', () => {
    expect(formatReasoningAgent('PRETRADE_INTEL')).toBe('Pre-Trade Intel');
    expect(formatReasoningAgent('RISK_SENTINEL')).toBe('Risk Sentinel');
    expect(formatReasoningAgent('REGIME_ROTATION')).toBe('Regime Rotation');
    expect(formatReasoningAgent('POSTTRADE_LEARNER')).toBe('Post-Trade Learner');
  });

  it('title-cases unknown agents', () => {
    expect(formatReasoningAgent('META_LABEL')).toBe('Meta Label');
  });

  it('falls back for missing agent', () => {
    expect(formatReasoningAgent(null)).toBe('Agent');
  });
});

describe('reasoningVerdictVariant', () => {
  it('maps verdicts to badge variants', () => {
    expect(reasoningVerdictVariant('VETO')).toBe('destructive');
    expect(reasoningVerdictVariant('PAUSE')).toBe('destructive');
    expect(reasoningVerdictVariant('REDUCE_SIZE')).toBe('sell');
    expect(reasoningVerdictVariant('CONFIRM')).toBe('buy');
    expect(reasoningVerdictVariant('ROTATE')).toBe('secondary');
    expect(reasoningVerdictVariant(null)).toBe('secondary');
  });
});

describe('formatReasoningTime', () => {
  it('formats epoch seconds', () => {
    expect(formatReasoningTime(1700000000)).toBe(new Date(1700000000 * 1000).toLocaleTimeString());
  });

  it('returns empty string for missing/garbage input', () => {
    expect(formatReasoningTime(null)).toBe('');
    expect(formatReasoningTime('not-a-date')).toBe('');
  });
});

describe('normalizeReasoningChain', () => {
  it('normalizes a full API row', () => {
    const chain = normalizeReasoningChain({
      id: 7,
      bot_id: 'bot-1',
      agent: 'PRETRADE_INTEL',
      verdict: 'VETO',
      notes: 'Gap too wide.',
      observations: [
        { source: 'market_anomaly', signal: 'danger', confidence: 0.95, detail: 'Price gap of 3.10%' },
        { source: 'sentiment' },
        'junk',
      ],
      vetoes: ['price_gap_anomaly: 3.10% gap'],
      size_multiplier: 0.0,
      ts: 1700000100,
      created_at: '2026-08-16T10:00:00Z',
    });
    expect(chain.id).toBe(7);
    expect(chain.agent).toBe('PRETRADE_INTEL');
    expect(chain.verdict).toBe('VETO');
    expect(chain.observations).toHaveLength(2);
    expect(chain.observations[0].detail).toBe('Price gap of 3.10%');
    expect(chain.observations[1].confidence).toBeNull();
    expect(chain.vetoes).toEqual(['price_gap_anomaly: 3.10% gap']);
  });

  it('handles missing arrays and nulls', () => {
    const chain = normalizeReasoningChain({ bot_id: 'b', agent: 'X' });
    expect(chain.observations).toEqual([]);
    expect(chain.vetoes).toEqual([]);
    expect(chain.size_multiplier).toBeNull();
    expect(chain.ts).toBeNull();
  });

  it('returns null for non-object rows', () => {
    expect(normalizeReasoningChain(null)).toBeNull();
    expect(normalizeReasoningChain('x')).toBeNull();
  });
});

describe('normalizeReasoningChains', () => {
  it('drops junk rows and keeps order', () => {
    const rows = normalizeReasoningChains([
      { id: 1, agent: 'A' },
      null,
      { id: 2, agent: 'B' },
    ]);
    expect(rows.map((r) => r.id)).toEqual([1, 2]);
    expect(normalizeReasoningChains(undefined)).toEqual([]);
  });
});
