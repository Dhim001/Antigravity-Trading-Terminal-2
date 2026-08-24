import { beforeEach, describe, expect, it } from 'vitest';
import {
  advancePipeline,
  applyPipelineSnapshot,
  cancelPipeline,
  completePipeline,
  failPipeline,
  getAutoDeployMode,
  getMlPipeline,
  isPipelineActive,
  PIPELINE_STEPPER_STAGES,
  resetPipeline,
  setAutoDeployMode,
  setPipelineTrainResult,
  startPipeline,
} from './mlPipeline';

describe('mlPipeline', () => {
  beforeEach(() => {
    resetPipeline();
    setAutoDeployMode('paper');
  });

  it('starts in TRAINING with a pipelineId', () => {
    const id = startPipeline({
      strategy: 'LSTM_DIRECTION',
      symbol: 'ETHUSDT',
      timeframe: '1m',
      trainingWindow: '3',
      autoAdvance: true,
      autoDeployMode: 'paper',
    });
    const s = getMlPipeline();
    expect(id).toBeTruthy();
    expect(s.pipelineId).toBe(id);
    expect(s.stage).toBe('TRAINING');
    expect(s.strategy).toBe('LSTM_DIRECTION');
    expect(s.symbol).toBe('ETHUSDT');
    expect(isPipelineActive(s)).toBe(true);
  });

  it('advances through the linear stage flow', () => {
    const id = startPipeline({ strategy: 'ML_SIGNAL_BOOST', symbol: 'BTCUSDT' });
    advancePipeline(id, { result: { ok: true } });
    expect(getMlPipeline().stage).toBe('VALIDATING');
    expect(getMlPipeline().trainResult).toEqual({ ok: true });

    advancePipeline(id, { result: { ok: true, mean_accuracy: 0.6 } });
    expect(getMlPipeline().stage).toBe('BACKTESTING');

    advancePipeline(id, { result: { total_pnl: 10 } });
    expect(getMlPipeline().stage).toBe('GATE_CHECK');

    advancePipeline(id, { result: { blocking: false } });
    expect(getMlPipeline().stage).toBe('READY_TO_DEPLOY');

    advancePipeline(id);
    expect(getMlPipeline().stage).toBe('DEPLOYED');
  });

  it('stops after validate when stopAfterValidate is set', () => {
    const id = startPipeline({
      strategy: 'LSTM_DIRECTION',
      symbol: 'ETHUSDT',
      stopAfterValidate: true,
      presetId: 'ml_retrain_validate',
    });
    advancePipeline(id, { result: { ok: true } });
    expect(getMlPipeline().stage).toBe('VALIDATING');
    advancePipeline(id, { result: { ok: true } });
    // completePipeline keeps terminal stage; stopAfterValidate completes without BACKTESTING
    expect(getMlPipeline().stage).toBe('VALIDATING');
    expect(getMlPipeline().completedAt).toBeTruthy();
    expect(isPipelineActive(getMlPipeline())).toBe(false);
  });

  it('records errors on failPipeline', () => {
    const id = startPipeline({ strategy: 'LSTM_DIRECTION', symbol: 'ETHUSDT' });
    failPipeline(id, { stage: 'TRAINING', error: 'boom' });
    const s = getMlPipeline();
    expect(s.stage).toBe('ERROR');
    expect(s.lastError).toBe('boom');
    expect(s.errors).toHaveLength(1);
    expect(isPipelineActive(s)).toBe(false);
  });

  it('marks GATE_FAILED from gate stage', () => {
    const id = startPipeline({ strategy: 'LSTM_DIRECTION', symbol: 'ETHUSDT' });
    advancePipeline(id);
    advancePipeline(id);
    advancePipeline(id);
    expect(getMlPipeline().stage).toBe('GATE_CHECK');
    failPipeline(id, { stage: 'GATE_CHECK', error: 'blocked' });
    expect(getMlPipeline().stage).toBe('GATE_FAILED');
  });

  it('ignores advance for mismatched pipelineId', () => {
    const id = startPipeline({ strategy: 'LSTM_DIRECTION', symbol: 'ETHUSDT' });
    advancePipeline('other-id', { result: { ok: true } });
    expect(getMlPipeline().stage).toBe('TRAINING');
    expect(getMlPipeline().pipelineId).toBe(id);
  });

  it('persists autoDeployMode via setter', () => {
    setAutoDeployMode('approval');
    expect(getAutoDeployMode()).toBe('approval');
    setAutoDeployMode('full_auto');
    expect(getAutoDeployMode()).toBe('full_auto');
    setAutoDeployMode('bogus');
    expect(getAutoDeployMode()).toBe('paper');
  });

  it('setPipelineTrainResult updates without advancing', () => {
    const id = startPipeline({ strategy: 'LSTM_DIRECTION', symbol: 'ETHUSDT' });
    setPipelineTrainResult(id, { metrics: { val_accuracy: 0.7 } });
    expect(getMlPipeline().stage).toBe('TRAINING');
    expect(getMlPipeline().trainResult.metrics.val_accuracy).toBe(0.7);
  });

  it('cancelPipeline resets to idle', () => {
    const id = startPipeline({ strategy: 'LSTM_DIRECTION', symbol: 'ETHUSDT' });
    cancelPipeline(id);
    expect(getMlPipeline().pipelineId).toBeNull();
    expect(getMlPipeline().stage).toBe('IDLE');
  });

  it('completePipeline from READY_TO_DEPLOY marks DEPLOYED', () => {
    const id = startPipeline({ strategy: 'LSTM_DIRECTION', symbol: 'ETHUSDT' });
    advancePipeline(id);
    advancePipeline(id);
    advancePipeline(id);
    advancePipeline(id);
    expect(getMlPipeline().stage).toBe('READY_TO_DEPLOY');
    completePipeline(id);
    expect(getMlPipeline().stage).toBe('DEPLOYED');
  });

  it('logs stage transitions with timestamps', () => {
    const id = startPipeline({ strategy: 'LSTM_DIRECTION', symbol: 'ETHUSDT' });
    expect(getMlPipeline().transitionLog[0]).toMatchObject({
      from: 'IDLE',
      to: 'TRAINING',
    });
    advancePipeline(id, { result: { ok: true } });
    const log = getMlPipeline().transitionLog;
    expect(log.some((e) => e.from === 'TRAINING' && e.to === 'VALIDATING')).toBe(true);
    expect(log.every((e) => typeof e.timestamp === 'number')).toBe(true);
  });

  it('records error on fail transitions', () => {
    const id = startPipeline({ strategy: 'LSTM_DIRECTION', symbol: 'ETHUSDT' });
    failPipeline(id, { stage: 'TRAINING', error: 'boom' });
    const last = getMlPipeline().transitionLog.at(-1);
    expect(last.to).toBe('ERROR');
    expect(last.error).toBe('boom');
  });

  it('includes Search in the stepper', () => {
    expect(PIPELINE_STEPPER_STAGES[0]).toEqual({ id: 'SEARCH', label: 'Search' });
  });

  it('starts research profile at SEARCH', () => {
    startPipeline({
      strategy: 'ML_SIGNAL_BOOST',
      symbol: 'BTCUSDT',
      profile: 'research',
    });
    expect(getMlPipeline().stage).toBe('SEARCH');
  });

  it('hydrates a server snapshot as the projection', () => {
    applyPipelineSnapshot({
      pipeline_id: 'srv-1',
      stage: 'VALIDATING',
      strategy: 'ML_SIGNAL_BOOST',
      symbol: 'ETHUSDT',
      profile: 'research',
      owned_by_server: true,
      auto_deploy_mode: 'paper',
      train_result: { ok: true },
      events: [{ from: 'TRAINING', to: 'VALIDATING', created_at: '2026-08-22T00:00:00Z' }],
    });
    const s = getMlPipeline();
    expect(s.pipelineId).toBe('srv-1');
    expect(s.ownedByServer).toBe(true);
    expect(s.stage).toBe('VALIDATING');
    expect(s.trainResult).toEqual({ ok: true });
    expect(s.transitionLog[0].from).toBe('TRAINING');
  });
});
