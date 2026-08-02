import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyWorkflowPreset, WORKFLOW_PRESETS } from './BacktestWorkflowPresets';
import { clearMlLabRequest, takeMlLabRequest } from '@/lib/mlLabRequests';

function makeWindowStub() {
  const store = new Map();
  return {
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => true,
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    location: { search: '' },
  };
}

class FakeCustomEvent {
  constructor(type, opts = {}) {
    this.type = type;
    this.detail = opts.detail;
  }
}

function baseOpts(overrides = {}) {
  return {
    activeSymbol: 'ETHUSDT',
    botStrategy: 'LSTM_DIRECTION',
    botTimeframe: '1m',
    trainingWindow: '3',
    setBacktestDays: vi.fn(),
    setBacktestOos: vi.fn(),
    setBacktestReasoning: vi.fn(),
    setPortfolioBacktest: vi.fn(),
    setPortfolioSymbols: vi.fn(),
    setBacktestSimMode: vi.fn(),
    setBacktestLiveParity: vi.fn(),
    setMetaLabelWalkForward: vi.fn(),
    openBacktestLab: vi.fn(),
    ...overrides,
  };
}

describe('ML workflow presets', () => {
  beforeEach(() => {
    vi.stubGlobal('window', makeWindowStub());
    vi.stubGlobal('CustomEvent', FakeCustomEvent);
    clearMlLabRequest();
  });

  afterEach(() => {
    clearMlLabRequest();
    vi.unstubAllGlobals();
  });

  it('registers the three ML presets', () => {
    const ids = WORKFLOW_PRESETS.map((p) => p.id);
    expect(ids).toContain('ml_full_pipeline');
    expect(ids).toContain('ml_retrain_validate');
    expect(ids).toContain('ml_batch_train');
  });

  it('ml_full_pipeline starts an auto-advance pipeline and forwards the same pipelineId', () => {
    const startPipeline = vi.fn(() => 'pipe-123');
    const getAutoDeployMode = vi.fn(() => 'paper');
    const onMlPipelineTrain = vi.fn();
    const ok = applyWorkflowPreset('ml_full_pipeline', baseOpts({
      startPipeline, getAutoDeployMode, onMlPipelineTrain,
    }));
    expect(ok).toBe(true);
    expect(startPipeline).toHaveBeenCalledWith(expect.objectContaining({
      strategy: 'LSTM_DIRECTION',
      symbol: 'ETHUSDT',
      autoAdvance: true,
      autoDeployMode: 'paper',
      presetId: 'ml_full_pipeline',
    }));
    // The Lab must reuse this run — not start (and orphan) a second one.
    expect(onMlPipelineTrain).toHaveBeenCalledWith(expect.objectContaining({
      pipelineId: 'pipe-123',
      mode: 'full',
    }));
  });

  it('ml_retrain_validate never auto-deploys and stops after validate', () => {
    const startPipeline = vi.fn(() => 'pipe-456');
    const onMlPipelineTrain = vi.fn();
    applyWorkflowPreset('ml_retrain_validate', baseOpts({
      startPipeline,
      getAutoDeployMode: () => 'full_auto',
      onMlPipelineTrain,
    }));
    expect(startPipeline).toHaveBeenCalledWith(expect.objectContaining({
      autoDeployMode: 'approval',
      stopAfterValidate: true,
      presetId: 'ml_retrain_validate',
    }));
    expect(onMlPipelineTrain).toHaveBeenCalledWith(expect.objectContaining({
      pipelineId: 'pipe-456',
      mode: 'retrain_validate',
    }));
  });

  it('ml_batch_train opens the dialog and posts a mailbox request', () => {
    const openBatchTrainDialog = vi.fn();
    const ok = applyWorkflowPreset('ml_batch_train', baseOpts({ openBatchTrainDialog }));
    expect(ok).toBe(true);
    expect(openBatchTrainDialog).toHaveBeenCalledOnce();
    const req = takeMlLabRequest(['ml-lab-open-batch']);
    expect(req?.detail).toMatchObject({ scope: 'all', symbol: 'ETHUSDT', timeframe: '1m' });
  });

  it('non-ML presets still apply backtest settings', () => {
    const opts = baseOpts();
    expect(applyWorkflowPreset('quick_baseline', opts)).toBe(true);
    expect(opts.setBacktestDays).toHaveBeenCalledWith('7');
    expect(opts.setBacktestLiveParity).toHaveBeenCalledWith(true);
  });
});
