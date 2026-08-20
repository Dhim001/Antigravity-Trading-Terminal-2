import { describe, expect, it } from 'vitest';
import { floorTunedBudgetKnobs, tunedBudgetFloors } from './mlTunedApply';

describe('tunedBudgetFloors', () => {
  it('derives floors from the Lab train defaults', () => {
    expect(tunedBudgetFloors('RL_PPO_AGENT').total_timesteps).toBe(200_000);
    expect(tunedBudgetFloors('LSTM_DIRECTION').epochs).toBe(100);
    expect(tunedBudgetFloors('TRANSFORMER_SIGNAL').epochs).toBe(80);
    expect(tunedBudgetFloors('GNN_CROSS_ASSET').epochs).toBe(60);
    expect(tunedBudgetFloors('VAE_REGIME_DETECTOR').epochs).toBe(120);
    expect(tunedBudgetFloors('ML_SIGNAL_BOOST').gbm_max_iter).toBe(300);
    expect(tunedBudgetFloors('LSTM_DIRECTION').early_stop_patience).toBe(10);
  });
});

describe('floorTunedBudgetKnobs', () => {
  it('floors a fidelity-shrunk RL budget at the Lab default', () => {
    const out = floorTunedBudgetKnobs('RL_PPO_AGENT', {
      total_timesteps: 16384,
      learning_rate: 0.0005,
      hidden_dim: 128,
    });
    expect(out.total_timesteps).toBe(200_000);
    // Architecture / regularization knobs pass through verbatim.
    expect(out.learning_rate).toBe(0.0005);
    expect(out.hidden_dim).toBe(128);
  });

  it('keeps tuned budgets that exceed the Lab default', () => {
    const out = floorTunedBudgetKnobs('LSTM_DIRECTION', { epochs: 150 });
    expect(out.epochs).toBe(150);
  });

  it('floors deep-net epochs and patience tuned below default', () => {
    const out = floorTunedBudgetKnobs('LSTM_DIRECTION', {
      epochs: 30,
      early_stop_patience: 5,
      lookback: 90,
    });
    expect(out.epochs).toBe(100);
    expect(out.early_stop_patience).toBe(10);
    expect(out.lookback).toBe(90);
  });

  it('floors GBM iterations but leaves depth and lr alone', () => {
    const out = floorTunedBudgetKnobs('ML_SIGNAL_BOOST', {
      gbm_max_iter: 100,
      gbm_max_depth: 7,
      gbm_learning_rate: 0.03,
    });
    expect(out.gbm_max_iter).toBe(300);
    expect(out.gbm_max_depth).toBe(7);
    expect(out.gbm_learning_rate).toBe(0.03);
  });

  it('replaces non-numeric budget values with the floor', () => {
    const out = floorTunedBudgetKnobs('RL_PPO_AGENT', { total_timesteps: 'abc' });
    expect(out.total_timesteps).toBe(200_000);
  });

  it('does not add budget keys the sweep never tuned', () => {
    const out = floorTunedBudgetKnobs('LSTM_DIRECTION', { learning_rate: 0.002 });
    expect(out).toEqual({ learning_rate: 0.002 });
  });

  it('never mutates the input', () => {
    const hp = { epochs: 30 };
    floorTunedBudgetKnobs('LSTM_DIRECTION', hp);
    expect(hp.epochs).toBe(30);
  });
});
