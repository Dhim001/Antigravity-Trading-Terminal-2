/**
 * Gate evaluation + auto-deploy orchestration for the ML pipeline.
 */
import { evaluateDeployGate } from './deployGate';
import { isPaperExecutionMode } from './massiveMarket';

/**
 * Run deploy gate evaluation and optionally auto-deploy based on mode.
 *
 * @param {object} params
 * @param {object} params.backtestResults
 * @param {object} params.config
 * @param {string} params.autoDeployMode — 'paper' | 'approval' | 'full_auto'
 * @param {string} [params.executionMode]
 * @param {string} [params.terminalMode]
 * @param {string} [params.symbol]
 * @param {string} [params.strategy]
 * @param {string} [params.timeframe]
 * @param {string|number} [params.days]
 * @param {string} [params.snapshot]
 * @param {object} [params.backtestConfig]
 * @param {function} [params.onGatePassed]
 * @param {function} [params.onGateFailed]
 * @param {function} [params.onApprovalNeeded]
 * @param {function} [params.onAutoDeploy]
 * @returns {{ gateResult: object, deployed: boolean, reason: string }}
 */
export function evaluateAndMaybeDeploy(params = {}) {
  const {
    backtestResults,
    config,
    autoDeployMode = 'paper',
    executionMode,
    terminalMode,
    symbol,
    strategy,
    timeframe,
    days,
    snapshot,
    backtestConfig,
    onGatePassed,
    onGateFailed,
    onApprovalNeeded,
    onAutoDeploy,
  } = params;

  const gateResult = evaluateDeployGate({
    results: backtestResults,
    symbol,
    config,
    strategy,
    timeframe,
    days,
    snapshot,
    backtestConfig,
  });

  if (gateResult.blocking) {
    onGateFailed?.(gateResult);
    return {
      gateResult,
      deployed: false,
      reason: gateResult.block_reason || 'Deploy gate blocked',
    };
  }

  onGatePassed?.(gateResult);

  switch (autoDeployMode) {
    case 'paper': {
      if (isPaperExecutionMode(terminalMode, executionMode)) {
        onAutoDeploy?.(gateResult);
        return {
          gateResult,
          deployed: true,
          reason: 'Auto-deployed (paper mode)',
        };
      }
      return {
        gateResult,
        deployed: false,
        reason: 'Gate passed but live mode — paper auto-deploy only',
      };
    }
    case 'approval': {
      onApprovalNeeded?.(gateResult);
      return {
        gateResult,
        deployed: false,
        reason: 'Awaiting user approval',
      };
    }
    case 'full_auto': {
      onAutoDeploy?.(gateResult);
      return {
        gateResult,
        deployed: true,
        reason: 'Auto-deployed (full auto mode)',
      };
    }
    default:
      return {
        gateResult,
        deployed: false,
        reason: 'Unknown deploy mode',
      };
  }
}

/** Whether paper auto-deploy would fire given current terminal/execution mode. */
export function canPaperAutoDeploy(terminalMode, executionMode) {
  return isPaperExecutionMode(terminalMode, executionMode);
}
