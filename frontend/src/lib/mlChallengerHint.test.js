import { describe, expect, it } from 'vitest';
import {
  buildChallengerHint,
  challengerBeatsChampion,
  pickChallengerVersion,
} from './mlChallengerHint';

describe('mlChallengerHint', () => {
  it('challengerBeatsChampion requires finite improvement', () => {
    expect(challengerBeatsChampion(0.62, 0.60)).toBe(true);
    expect(challengerBeatsChampion(0.601, 0.60)).toBe(false);
    expect(challengerBeatsChampion(null, 0.60)).toBe(false);
  });

  it('pickChallengerVersion only matches named non-current version', () => {
    const versions = [
      { version_id: 'v2', is_current: true },
      { version_id: 'v1', is_current: false },
    ];
    expect(pickChallengerVersion(versions, null)).toBe(null);
    expect(pickChallengerVersion(versions, 'v2')).toBe(null);
    expect(pickChallengerVersion(versions, 'v1')?.version_id).toBe('v1');
  });

  it('buildChallengerHint is dismiss-only without named challenger version', () => {
    const hint = buildChallengerHint({
      validation: { ok: true, mean_accuracy: 0.65 },
      championOos: 0.55,
      versions: [
        { version_id: 'new', is_current: false },
        { version_id: 'old', is_current: true },
      ],
    });
    expect(hint?.canActivate).toBe(false);
    expect(hint?.alreadyLive).toBe(true);
  });

  it('buildChallengerHint activates when validation names a non-current version', () => {
    const hint = buildChallengerHint({
      validation: { ok: true, mean_accuracy: 0.65, version_id: 'new' },
      championOos: 0.55,
      versions: [
        { version_id: 'new', is_current: false },
        { version_id: 'old', is_current: true },
      ],
    });
    expect(hint?.canActivate).toBe(true);
    expect(hint?.version?.version_id).toBe('new');
  });

  it('buildChallengerHint skips when champion OOS is missing', () => {
    expect(buildChallengerHint({
      validation: { ok: true, mean_accuracy: 0.65 },
      championOos: null,
      versions: [],
    })).toBe(null);
  });
});
