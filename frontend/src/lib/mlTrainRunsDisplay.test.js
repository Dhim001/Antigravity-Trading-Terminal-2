import { describe, expect, it } from 'vitest';
import {
  formatVersionId,
  runModelLabel,
  runResultLabel,
  runVersionCell,
  runVersionParts,
} from './mlTrainRunsDisplay';

describe('mlTrainRunsDisplay', () => {
  it('keeps full version ids visible', () => {
    expect(formatVersionId('20260804T234715Z')).toBe('20260804T234715Z');
  });

  it('uses strategy label for Model column', () => {
    expect(runModelLabel({
      display_name: 'ETH transformer v2',
      strategy: 'TRANSFORMER_SIGNAL',
    })).toMatch(/Transformer/i);
  });

  it('shows version name + id together', () => {
    expect(runVersionParts({
      kind: 'train',
      version_id: '20260804T234715Z',
      display_name: 'ETH boost v3',
    })).toEqual({
      name: 'ETH boost v3',
      id: '20260804T234715Z',
      emptyLabel: '—',
    });
    expect(runVersionCell({
      kind: 'train',
      version_id: '20260804T234715Z',
      display_name: 'ETH boost v3',
    })).toBe('ETH boost v3 · 20260804T234715Z');
  });

  it('resolves display_name from versions list', () => {
    const parts = runVersionParts(
      { kind: 'train', version_id: '20260804T234715Z' },
      [{ version_id: '20260804T234715Z', display_name: 'Pinned champ' }],
    );
    expect(parts.name).toBe('Pinned champ');
    expect(parts.id).toBe('20260804T234715Z');
  });

  it('labels validate rows without a pin clearly', () => {
    expect(runVersionCell({ kind: 'validate', version_id: null })).toBe('no pin (WF)');
  });

  it('formats result metrics', () => {
    expect(runResultLabel({ ok: true, metrics: { val_accuracy: 0.42 } })).toContain('ok');
    expect(runResultLabel({ ok: false, error: 'cancelled' })).toBe('cancelled');
  });
});
