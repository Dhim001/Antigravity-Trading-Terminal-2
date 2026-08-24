/**
 * ML Lab ↔ Algo pipeline state machine.
 * Module-level (survives unmounts), same pattern as mlTrainingSession.js.
 */

const AUTO_DEPLOY_KEY = 'ml-pipeline-auto-deploy-mode';
const AUTO_ADVANCE_KEY = 'ml-pipeline-auto-advance';
/** Cap transition audit log to bound memory. */
export const TRANSITION_LOG_MAX = 40;

export const PIPELINE_STAGES = [
  'IDLE',
  'SEARCH',
  'TRAINING',
  'VALIDATING',
  'BACKTESTING',
  'GATE_CHECK',
  'READY_TO_DEPLOY',
  'DEPLOYED',
  'GATE_FAILED',
  'ERROR',
];

/** Linear advance path when autoAdvance is on. */
const STAGE_FLOW = {
  IDLE: 'TRAINING',
  SEARCH: 'TRAINING',
  TRAINING: 'VALIDATING',
  VALIDATING: 'BACKTESTING',
  BACKTESTING: 'GATE_CHECK',
  GATE_CHECK: 'READY_TO_DEPLOY',
  READY_TO_DEPLOY: 'DEPLOYED',
};

/** Presets that stop after validate (no backtest/deploy). */
const STOP_AFTER_VALIDATE = new Set(['ml_retrain_validate']);

const listeners = new Set();

let state = createIdleState();

function createIdleState(overrides = {}) {
  return {
    pipelineId: null,
    stage: 'IDLE',
    strategy: null,
    symbol: null,
    timeframe: null,
    trainingWindow: null,
    trainResult: null,
    validationResult: null,
    backtestResult: null,
    gateResult: null,
    searchResult: null,
    ownedByServer: false,
    profile: null,
    autoAdvance: readAutoAdvance(),
    autoDeployMode: readAutoDeployMode(),
    /** Optional preset id that may alter flow (e.g. stop after validate). */
    presetId: null,
    /** When true, pipeline stops after VALIDATING. */
    stopAfterValidate: false,
    startedAt: null,
    stageStartedAt: null,
    completedAt: null,
    stageElapsed: {},
    errors: [],
    lastError: null,
    pendingApproval: false,
    /** Capped audit trail of stage transitions. */
    transitionLog: [],
    ...overrides,
  };
}

function appendTransition(log, entry) {
  const next = [...(log || []), entry];
  if (next.length <= TRANSITION_LOG_MAX) return next;
  return next.slice(next.length - TRANSITION_LOG_MAX);
}

function pushTransition(partial, fromStage, toStage, extra = {}) {
  const timestamp = Date.now();
  const elapsedMs = state.stageStartedAt != null
    ? Math.max(0, timestamp - state.stageStartedAt)
    : null;
  return {
    ...partial,
    transitionLog: appendTransition(state.transitionLog, {
      from: fromStage,
      to: toStage,
      timestamp,
      elapsedMs,
      ...extra,
    }),
  };
}

function emit() {
  listeners.forEach((fn) => {
    try {
      fn(state);
    } catch {
      /* ignore */
    }
  });
}

function patch(partial) {
  state = { ...state, ...partial };
  emit();
  return state;
}

function newPipelineId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `pipe_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
}

export function readAutoDeployMode() {
  try {
    const v = window.localStorage.getItem(AUTO_DEPLOY_KEY);
    if (v === 'paper' || v === 'approval' || v === 'full_auto') return v;
  } catch {
    /* ignore */
  }
  return 'paper';
}

export function readAutoAdvance() {
  try {
    return window.localStorage.getItem(AUTO_ADVANCE_KEY) !== '0';
  } catch {
    /* ignore */
  }
  return true;
}

export function getMlPipeline() {
  return state;
}

export function subscribeMlPipeline(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setAutoDeployMode(mode) {
  const next = mode === 'approval' || mode === 'full_auto' ? mode : 'paper';
  try {
    window.localStorage.setItem(AUTO_DEPLOY_KEY, next);
  } catch {
    /* ignore */
  }
  return patch({ autoDeployMode: next });
}

export function getAutoDeployMode() {
  return state.autoDeployMode || readAutoDeployMode();
}

export function setAutoAdvance(enabled) {
  const next = Boolean(enabled);
  try {
    window.localStorage.setItem(AUTO_ADVANCE_KEY, next ? '1' : '0');
  } catch {
    /* ignore */
  }
  return patch({ autoAdvance: next });
}

export function getAutoAdvance() {
  return Boolean(state.autoAdvance);
}

/**
 * Start a new pipeline run. Cancels any prior active run.
 */
export function startPipeline({
  strategy,
  symbol,
  timeframe,
  trainingWindow,
  autoAdvance,
  autoDeployMode,
  presetId = null,
  stopAfterValidate = false,
  profile = null,
} = {}) {
  const pipelineId = newPipelineId();
  const now = Date.now();
  const stop = Boolean(stopAfterValidate) || STOP_AFTER_VALIDATE.has(presetId);
  const startStage = profile === 'research' ? 'SEARCH' : 'TRAINING';
  state = createIdleState({
    pipelineId,
    stage: startStage,
    strategy: strategy || null,
    symbol: symbol || null,
    timeframe: timeframe || null,
    trainingWindow: trainingWindow != null ? String(trainingWindow) : null,
    autoAdvance: autoAdvance != null ? Boolean(autoAdvance) : readAutoAdvance(),
    autoDeployMode: autoDeployMode || readAutoDeployMode(),
    presetId: presetId || null,
    stopAfterValidate: stop,
    profile: profile || (stop ? 'retrain' : null),
    ownedByServer: false,
    startedAt: now,
    stageStartedAt: now,
    stageElapsed: {},
    transitionLog: [{
      from: 'IDLE',
      to: startStage,
      timestamp: now,
      elapsedMs: null,
    }],
  });
  emit();
  return pipelineId;
}

function recordStageElapsed(prevStage, stageStartedAt, stageElapsed) {
  if (!prevStage || prevStage === 'IDLE' || stageStartedAt == null) return stageElapsed || {};
  const elapsed = Math.max(0, Date.now() - stageStartedAt);
  return { ...(stageElapsed || {}), [prevStage]: elapsed };
}

/**
 * Advance to the next stage. Optionally attach a result for the completed stage.
 */
export function advancePipeline(pipelineId, { result, toStage } = {}) {
  if (!pipelineId || state.pipelineId !== pipelineId) return state;
  const prev = state.stage;
  let nextStage = toStage || STAGE_FLOW[prev];
  if (!nextStage) return state;

  if (prev === 'VALIDATING' && state.stopAfterValidate && !toStage) {
    return completePipeline(pipelineId);
  }

  const stageElapsed = recordStageElapsed(prev, state.stageStartedAt, state.stageElapsed);
  const patchResult = {};
  if (result != null) {
    if (prev === 'TRAINING') patchResult.trainResult = result;
    else if (prev === 'VALIDATING') patchResult.validationResult = result;
    else if (prev === 'BACKTESTING') patchResult.backtestResult = result;
    else if (prev === 'GATE_CHECK') patchResult.gateResult = result;
  }

  return patch(pushTransition({
    stage: nextStage,
    stageStartedAt: Date.now(),
    stageElapsed,
    lastError: null,
    pendingApproval: nextStage === 'READY_TO_DEPLOY' && state.autoDeployMode === 'approval',
    ...patchResult,
  }, prev, nextStage));
}

export function failPipeline(pipelineId, { stage, error } = {}) {
  if (!pipelineId || state.pipelineId !== pipelineId) return state;
  const errStage = stage || state.stage;
  const message = error != null ? String(error) : 'Pipeline failed';
  const entry = { stage: errStage, message, timestamp: Date.now() };
  const stageElapsed = recordStageElapsed(state.stage, state.stageStartedAt, state.stageElapsed);
  const nextStage = errStage === 'GATE_CHECK' || state.stage === 'GATE_CHECK'
    ? 'GATE_FAILED'
    : 'ERROR';
  return patch(pushTransition({
    stage: nextStage,
    lastError: message,
    errors: [...(state.errors || []), entry],
    stageStartedAt: Date.now(),
    stageElapsed,
    completedAt: Date.now(),
  }, state.stage, nextStage, { error: message }));
}

export function completePipeline(pipelineId) {
  if (!pipelineId || state.pipelineId !== pipelineId) return state;
  const stageElapsed = recordStageElapsed(state.stage, state.stageStartedAt, state.stageElapsed);
  const nextStage = state.stage === 'READY_TO_DEPLOY' || state.stage === 'DEPLOYED'
    ? 'DEPLOYED'
    : state.stage;
  const base = {
    stage: nextStage,
    completedAt: Date.now(),
    stageElapsed,
    stageStartedAt: Date.now(),
    pendingApproval: false,
  };
  if (nextStage !== state.stage) {
    return patch(pushTransition(base, state.stage, nextStage));
  }
  // Soft complete (e.g. stop-after-validate) — still log a completion marker.
  return patch(pushTransition(base, state.stage, `${state.stage}:done`));
}

export function cancelPipeline(pipelineId) {
  if (pipelineId && state.pipelineId && state.pipelineId !== pipelineId) return state;
  if (state.ownedByServer && state.pipelineId) {
    import('@/lib/mlLabApi').then(({ cancelMlPipeline }) => {
      cancelMlPipeline(state.pipelineId).catch(() => {});
    });
  }
  stopPipelinePolling();
  return patch({
    ...createIdleState({
      autoAdvance: state.autoAdvance,
      autoDeployMode: state.autoDeployMode,
    }),
    lastError: 'cancelled',
  });
}

export function resetPipeline() {
  stopPipelinePolling();
  state = createIdleState({
    autoAdvance: readAutoAdvance(),
    autoDeployMode: readAutoDeployMode(),
  });
  emit();
  return state;
}

export function setPipelineTrainResult(pipelineId, result) {
  if (!pipelineId || state.pipelineId !== pipelineId) return state;
  return patch({ trainResult: result });
}

export function setPipelineValidationResult(pipelineId, result) {
  if (!pipelineId || state.pipelineId !== pipelineId) return state;
  return patch({ validationResult: result });
}

export function setPipelineBacktestResult(pipelineId, result) {
  if (!pipelineId || state.pipelineId !== pipelineId) return state;
  return patch({ backtestResult: result });
}

export function setPipelineGateResult(pipelineId, result) {
  if (!pipelineId || state.pipelineId !== pipelineId) return state;
  return patch({ gateResult: result });
}

export function approvePipelineDeploy(pipelineId) {
  if (!pipelineId || state.pipelineId !== pipelineId) return state;
  if (state.ownedByServer) {
    import('@/lib/mlLabApi').then(({ approveMlPipeline }) => {
      approveMlPipeline(pipelineId)
        .then((body) => {
          if (body?.pipeline) applyPipelineSnapshot(body.pipeline);
        })
        .catch(() => {});
    });
  }
  return patch({ pendingApproval: false, stage: 'READY_TO_DEPLOY' });
}

/** True when a non-terminal pipeline run is active. */
export function isPipelineActive(s = state) {
  if (!s?.pipelineId) return false;
  // completePipeline (e.g. stop-after-validate) keeps the last stage but sets completedAt.
  if (s.completedAt != null) return false;
  return !['IDLE', 'DEPLOYED', 'GATE_FAILED', 'ERROR'].includes(s.stage);
}

/** Stages shown in the stepper UI. */
export const PIPELINE_STEPPER_STAGES = [
  { id: 'SEARCH', label: 'Search' },
  { id: 'TRAINING', label: 'Train' },
  { id: 'VALIDATING', label: 'Validate' },
  { id: 'BACKTESTING', label: 'Backtest' },
  { id: 'GATE_CHECK', label: 'Gate' },
  { id: 'READY_TO_DEPLOY', label: 'Deploy' },
  { id: 'DEPLOYED', label: 'Deployed' },
];

function isoToMs(value) {
  if (value == null) return null;
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 1e12 ? value : value * 1000;
  }
  const t = Date.parse(value);
  return Number.isFinite(t) ? t : null;
}

/**
 * Project a server pipeline row onto the module-level stepper state.
 */
export function applyPipelineSnapshot(server) {
  if (!server || typeof server !== 'object') return state;
  const pipelineId = server.pipelineId || server.pipeline_id;
  if (!pipelineId) return state;
  const events = server.transitionLog || server.events || [];
  const startedAt = server.startedAt ?? isoToMs(server.started_at) ?? Date.now();
  const stageStartedAt = server.stageStartedAt ?? isoToMs(server.stage_started_at) ?? startedAt;
  const completedAt = server.completedAt ?? isoToMs(server.completed_at);
  state = {
    ...createIdleState(),
    pipelineId,
    stage: server.stage || 'IDLE',
    strategy: server.strategy || null,
    symbol: server.symbol || null,
    timeframe: server.timeframe || null,
    trainingWindow: server.trainingWindow ?? server.training_window ?? null,
    trainResult: server.trainResult ?? server.train_result ?? null,
    validationResult: server.validationResult ?? server.validation_result ?? null,
    backtestResult: server.backtestResult ?? server.backtest_result ?? null,
    gateResult: server.gateResult ?? server.gate_result ?? null,
    searchResult: server.searchResult ?? server.search_result ?? null,
    autoAdvance: true,
    autoDeployMode: server.autoDeployMode || server.auto_deploy_mode || readAutoDeployMode(),
    presetId: server.presetId || null,
    stopAfterValidate: Boolean(server.stopAfterValidate ?? server.stop_after_validate),
    ownedByServer: true,
    profile: server.profile || null,
    startedAt,
    stageStartedAt,
    completedAt: completedAt ?? null,
    stageElapsed: server.stageElapsed || server.stage_elapsed || {},
    errors: Array.isArray(server.errors) ? server.errors : [],
    lastError: server.lastError || server.last_error || null,
    pendingApproval: Boolean(server.pendingApproval ?? server.pending_approval),
    transitionLog: (Array.isArray(events) ? events : []).map((e) => ({
      from: e.from || e.from_stage,
      to: e.to || e.to_stage,
      timestamp: e.timestamp ?? isoToMs(e.created_at),
      elapsedMs: e.elapsedMs ?? e.elapsed_ms ?? null,
      error: e.error || null,
    })),
    sweepJobId: server.sweep_job_id || server.sweepJobId || null,
    trainJobId: server.train_job_id || server.trainJobId || null,
    validateJobId: server.validate_job_id || server.validateJobId || null,
    backtestJobId: server.backtest_job_id || server.backtestJobId || null,
    botId: server.botId || server.bot_id || null,
  };
  emit();
  if (isPipelineActive(state)) startPipelinePolling(pipelineId);
  else stopPipelinePolling();
  return state;
}

let _pollTimer = null;

function stopPipelinePolling() {
  if (_pollTimer != null) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
}

function startPipelinePolling(pipelineId) {
  stopPipelinePolling();
  if (typeof window === 'undefined' || typeof window.setInterval !== 'function') return;
  if (typeof process !== 'undefined' && process.env && process.env.VITEST) return;
  _pollTimer = window.setInterval(() => {
    import('@/lib/mlLabApi').then(({ fetchMlPipeline }) => {
      fetchMlPipeline(pipelineId)
        .then((body) => {
          if (body?.pipeline) applyPipelineSnapshot(body.pipeline);
        })
        .catch(() => {});
    });
  }, 2500);
}

/**
 * POST a durable server run and project it onto the stepper.
 */
export async function startServerPipeline({
  strategy,
  symbol,
  timeframe,
  trainingWindow,
  autoDeployMode,
  profile = 'research',
  stopAfterValidate = false,
  config = {},
  presetId = null,
} = {}) {
  const { submitMlPipeline } = await import('@/lib/mlLabApi');
  const body = await submitMlPipeline({
    strategy,
    symbol,
    timeframe,
    training_window: trainingWindow,
    profile,
    auto_deploy_mode: autoDeployMode || readAutoDeployMode(),
    stop_after_validate: stopAfterValidate,
    presetId,
    config: {
      ...config,
      timeframe,
      training_window_months: trainingWindow != null ? Number(trainingWindow) : undefined,
    },
  });
  const snap = body?.pipeline;
  if (!snap) {
    throw new Error(body?.error || 'Pipeline was not created');
  }
  applyPipelineSnapshot(snap);
  return snap.pipelineId || snap.pipeline_id;
}

/**
 * Re-attach to an in-flight server pipeline after Lab remount / reload.
 */
export async function hydrateMlPipeline({ symbol } = {}) {
  try {
    const { fetchActiveMlPipeline, fetchMlPipeline } = await import('@/lib/mlLabApi');
    if (state.ownedByServer && state.pipelineId) {
      const body = await fetchMlPipeline(state.pipelineId);
      if (body?.pipeline) {
        applyPipelineSnapshot(body.pipeline);
        return getMlPipeline();
      }
    }
    const active = await fetchActiveMlPipeline(symbol);
    if (active?.pipeline) {
      applyPipelineSnapshot(active.pipeline);
    }
  } catch {
    /* ignore hydrate failures — stepper stays idle */
  }
  return getMlPipeline();
}
