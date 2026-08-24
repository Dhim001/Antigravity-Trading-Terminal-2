/**
 * Navigation helpers for pipeline stage review / deploy queue.
 */
import { openModelTrainingDock } from '@/lib/workspaceNav';
import { openBacktestLabResults } from '@/lib/backtestLab';
import { getMlPipeline } from '@/lib/mlPipeline';

/** CustomEvent name — AlgoTab opens the deploy confirmation dialog. */
export const ALGO_OPEN_DEPLOY_EVENT = 'algo-open-deploy';

/**
 * Open the Algo deploy dialog (and Automation Studio when not already in context).
 * @param {{ openStudio?: boolean }} [opts]
 */
export function openAlgoDeployDialog({ openStudio = false } = {}) {
  if (typeof window === 'undefined') return;
  if (openStudio) {
    window.dispatchEvent(new CustomEvent('automation-studio-open'));
  }
  // Defer so Studio/AlgoTab can mount before the deploy listener runs.
  window.requestAnimationFrame(() => {
    window.dispatchEvent(new CustomEvent(ALGO_OPEN_DEPLOY_EVENT));
  });
}

/**
 * Navigate to the surface that shows results for a completed pipeline stage.
 * @param {string} stageId — stepper stage id (TRAINING, VALIDATING, …)
 */
export function navigatePipelineStageReview(stageId) {
  switch (stageId) {
    case 'SEARCH':
    case 'TRAINING':
    case 'VALIDATING':
      openModelTrainingDock();
      break;
    case 'BACKTESTING':
      openBacktestLabResults();
      break;
    case 'GATE_CHECK':
    case 'READY_TO_DEPLOY':
    case 'DEPLOYED':
      openAlgoDeployDialog({ openStudio: true });
      break;
    default:
      break;
  }
}

/**
 * Deploy-queue quick action: open deploy when ready, else toast-friendly status.
 * @returns {{ action: 'open_deploy'|'show_status', stage: string, pendingApproval: boolean, lastError: string|null, gateBlocking: boolean|null }}
 */
export function resolveDeployQueueAction(pipeline = getMlPipeline()) {
  const stage = pipeline?.stage || 'IDLE';
  const pendingApproval = Boolean(pipeline?.pendingApproval);
  const lastError = pipeline?.lastError || null;
  const gateBlocking = pipeline?.gateResult?.blocking ?? null;
  const openStages = new Set(['READY_TO_DEPLOY', 'GATE_CHECK', 'DEPLOYED']);
  if (openStages.has(stage) || pendingApproval) {
    return { action: 'open_deploy', stage, pendingApproval, lastError, gateBlocking };
  }
  return { action: 'show_status', stage, pendingApproval, lastError, gateBlocking };
}
