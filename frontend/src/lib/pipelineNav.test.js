import { beforeEach, describe, expect, it } from 'vitest';
import { resolveDeployQueueAction } from './pipelineNav';
import { resetPipeline, startPipeline, advancePipeline, setAutoDeployMode } from './mlPipeline';

describe('resolveDeployQueueAction', () => {
  beforeEach(() => {
    resetPipeline();
    setAutoDeployMode('paper');
  });

  it('opens deploy when READY_TO_DEPLOY', () => {
    const id = startPipeline({ strategy: 'ML_SIGNAL_BOOST', symbol: 'BTCUSDT' });
    advancePipeline(id);
    advancePipeline(id);
    advancePipeline(id);
    advancePipeline(id);
    const r = resolveDeployQueueAction();
    expect(r.action).toBe('open_deploy');
    expect(r.stage).toBe('READY_TO_DEPLOY');
  });

  it('shows status when idle', () => {
    const r = resolveDeployQueueAction();
    expect(r.action).toBe('show_status');
    expect(r.stage).toBe('IDLE');
  });

  it('opens deploy when pendingApproval even mid-stage', () => {
    const r = resolveDeployQueueAction({
      stage: 'VALIDATING',
      pendingApproval: true,
      lastError: null,
      gateResult: null,
    });
    expect(r.action).toBe('open_deploy');
    expect(r.pendingApproval).toBe(true);
  });
});

describe('navigatePipelineStageReview', () => {
  it('is imported as a function', async () => {
    const mod = await import('./pipelineNav');
    expect(typeof mod.navigatePipelineStageReview).toBe('function');
    expect(typeof mod.openAlgoDeployDialog).toBe('function');
    expect(mod.ALGO_OPEN_DEPLOY_EVENT).toBe('algo-open-deploy');
  });
});
