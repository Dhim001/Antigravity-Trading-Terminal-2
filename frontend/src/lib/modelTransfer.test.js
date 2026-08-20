import { describe, expect, it } from 'vitest';
import {
  buildDonorConfig,
  donorDisabledReason,
  formatTransferBadge,
  freezeTrunkSupported,
  normalizeDonorList,
  scalerStrategySupported,
  transferSupported,
} from './modelTransfer';

describe('transferSupported', () => {
  it('covers RL, GBM, and the supervised deep nets', () => {
    expect(transferSupported('RL_PPO_AGENT')).toBe(true);
    expect(transferSupported('ML_SIGNAL_BOOST')).toBe(true);
    expect(transferSupported('LSTM_DIRECTION')).toBe(true);
    expect(transferSupported('TCN_MULTI_HORIZON')).toBe(true);
    expect(transferSupported('TRANSFORMER_SIGNAL')).toBe(true);
    expect(transferSupported('GNN_CROSS_ASSET')).toBe(true);
    expect(transferSupported('VAE_REGIME_DETECTOR')).toBe(true);
  });

  it('is case-insensitive and rejects unknown strategies', () => {
    expect(transferSupported('rl_ppo_agent')).toBe(true);
    expect(transferSupported('MEAN_REVERSION')).toBe(false);
    expect(transferSupported('')).toBe(false);
    expect(transferSupported(null)).toBe(false);
  });
});

describe('capability gates', () => {
  it('freeze-trunk applies to deep nets only', () => {
    expect(freezeTrunkSupported('LSTM_DIRECTION')).toBe(true);
    expect(freezeTrunkSupported('RL_PPO_AGENT')).toBe(false);
    expect(freezeTrunkSupported('ML_SIGNAL_BOOST')).toBe(false);
  });

  it('scaler strategy applies to RL only', () => {
    expect(scalerStrategySupported('RL_PPO_AGENT')).toBe(true);
    expect(scalerStrategySupported('LSTM_DIRECTION')).toBe(false);
  });
});

describe('normalizeDonorList', () => {
  const payload = {
    donors: [
      {
        symbol: 'ethusdt',
        version_id: 'v1',
        trained_at: '2026-08-01T00:00:00Z',
        timeframe: '1m',
        has_checkpoint: 1,
        mean_return_pct: 2.5,
      },
      {
        symbol: 'BTCUSDT',
        version_id: 'v2',
        trained_at: '2026-08-10T00:00:00Z',
        timeframe: '1m',
        has_checkpoint: true,
        accuracy: 0.61,
      },
      { symbol: 'ADAUSD', version_id: 'vX', trained_at: '2026-08-12T00:00:00Z' },
    ],
  };

  it('excludes the target symbol, uppercases, and sorts newest first', () => {
    const rows = normalizeDonorList(payload, 'ADAUSD');
    expect(rows.map((r) => r.symbol)).toEqual(['BTCUSDT', 'ETHUSDT']);
    expect(rows[0].versionId).toBe('v2');
    expect(rows[0].accuracy).toBe(0.61);
    expect(rows[1].hasCheckpoint).toBe(true);
    expect(rows[1].meanReturnPct).toBe(2.5);
  });

  it('tolerates malformed payloads', () => {
    expect(normalizeDonorList(null, 'ADAUSD')).toEqual([]);
    expect(normalizeDonorList({}, 'ADAUSD')).toEqual([]);
    expect(normalizeDonorList({ donors: 'nope' }, 'ADAUSD')).toEqual([]);
    expect(normalizeDonorList({ donors: [null, { symbol: 'BTCUSDT' }] }, 'ADAUSD')).toEqual([
      {
        symbol: 'BTCUSDT',
        versionId: null,
        trainedAt: null,
        timeframe: null,
        hasCheckpoint: false,
        meanReturnPct: null,
        accuracy: null,
      },
    ]);
  });
});

describe('buildDonorConfig', () => {
  it('returns null when disabled or incomplete', () => {
    expect(buildDonorConfig({ enabled: false, strategy: 'RL_PPO_AGENT', donorSymbol: 'BTCUSDT' })).toBeNull();
    expect(buildDonorConfig({ enabled: true, strategy: 'RL_PPO_AGENT' })).toBeNull();
    expect(buildDonorConfig({ enabled: true, strategy: 'MEAN_REVERSION', donorSymbol: 'BTCUSDT' })).toBeNull();
  });

  it('shapes the RL payload with scaler strategy and version', () => {
    expect(
      buildDonorConfig({
        enabled: true,
        strategy: 'RL_PPO_AGENT',
        donorSymbol: 'btcusdt',
        donorVersionId: 'v2',
        scalerStrategy: 'carry',
      }),
    ).toEqual({ symbol: 'BTCUSDT', version_id: 'v2', scaler_strategy: 'carry' });
  });

  it('defaults the RL scaler strategy to recompute and ignores junk values', () => {
    expect(
      buildDonorConfig({ enabled: true, strategy: 'RL_PPO_AGENT', donorSymbol: 'BTCUSDT' }),
    ).toEqual({ symbol: 'BTCUSDT', scaler_strategy: 'recompute' });
    expect(
      buildDonorConfig({
        enabled: true,
        strategy: 'RL_PPO_AGENT',
        donorSymbol: 'BTCUSDT',
        scalerStrategy: 'banana',
      }),
    ).toEqual({ symbol: 'BTCUSDT', scaler_strategy: 'recompute' });
  });

  it('includes freeze_trunk for deep nets only when requested', () => {
    expect(
      buildDonorConfig({
        enabled: true,
        strategy: 'LSTM_DIRECTION',
        donorSymbol: 'BTCUSDT',
        freezeTrunk: true,
      }),
    ).toEqual({ symbol: 'BTCUSDT', freeze_trunk: true });
    expect(
      buildDonorConfig({
        enabled: true,
        strategy: 'LSTM_DIRECTION',
        donorSymbol: 'BTCUSDT',
      }),
    ).toEqual({ symbol: 'BTCUSDT' });
  });

  it('omits NN/RL-only keys for GBM recipe transfer', () => {
    expect(
      buildDonorConfig({
        enabled: true,
        strategy: 'ML_SIGNAL_BOOST',
        donorSymbol: 'BTCUSDT',
        scalerStrategy: 'carry',
        freezeTrunk: true,
      }),
    ).toEqual({ symbol: 'BTCUSDT' });
  });
});

describe('formatTransferBadge', () => {
  it('formats donor symbol and date', () => {
    expect(
      formatTransferBadge({ donor_symbol: 'btcusdt', donor_trained_at: '2026-08-10T12:00:00Z' }),
    ).toBe('from BTCUSDT · 2026-08-10');
  });

  it('marks recipe transfer and tolerates missing dates', () => {
    expect(formatTransferBadge({ donor_symbol: 'BTCUSDT', method: 'recipe_transfer' })).toBe(
      'from BTCUSDT · recipe',
    );
    expect(formatTransferBadge({ donor_symbol: 'BTCUSDT', donor_trained_at: 'not-a-date' })).toBe(
      'from BTCUSDT',
    );
  });

  it('returns null without a donor symbol', () => {
    expect(formatTransferBadge(null)).toBeNull();
    expect(formatTransferBadge({})).toBeNull();
    expect(formatTransferBadge('BTCUSDT')).toBeNull();
  });
});

describe('donorDisabledReason', () => {
  it('prioritizes backend-disabled, then unsupported, then empty donors', () => {
    expect(donorDisabledReason({ enabled: false, supported: true, donors: [{}] })).toMatch(
      /disabled on the backend/,
    );
    expect(donorDisabledReason({ enabled: true, supported: false, donors: [{}] })).toMatch(
      /does not support/,
    );
    expect(donorDisabledReason({ enabled: true, supported: true, donors: [] })).toMatch(
      /No compatible donors/,
    );
    expect(donorDisabledReason({ enabled: true, supported: true, donors: [{}] })).toBeNull();
  });
});
