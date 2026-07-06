/**
 * DeployGatePanel — forward-test checklist shown before bot deploy.
 * Workflow: Backtest → OOS/WF validation → Deploy (paper/live).
 */
import React, { useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, Circle, XCircle } from 'lucide-react';
import { cn } from '@/lib/utils';
import { evaluateDeployGate } from '@/lib/deployGate';

const WORKFLOW_STEPS = [
  { id: 'backtest', label: 'Backtest' },
  { id: 'oos', label: 'OOS / WF' },
  { id: 'deploy', label: 'Deploy' },
];

function StepIcon({ active, done, blocked }) {
  if (blocked) return <XCircle className="size-3.5 text-trading-down shrink-0" aria-hidden />;
  if (done) return <CheckCircle2 className="size-3.5 text-trading-up shrink-0" aria-hidden />;
  if (active) return <Circle className="size-3.5 text-primary shrink-0" aria-hidden />;
  return <Circle className="size-3.5 text-muted-foreground/40 shrink-0" aria-hidden />;
}

function CheckIcon({ level, ok }) {
  if (level === 'block' && !ok) {
    return <XCircle className="size-3.5 text-trading-down shrink-0" aria-hidden />;
  }
  if (level === 'warn' && !ok) {
    return <AlertTriangle className="size-3.5 text-amber-500 shrink-0" aria-hidden />;
  }
  return <CheckCircle2 className="size-3.5 text-trading-up shrink-0" aria-hidden />;
}

export default function DeployGatePanel({
  results,
  symbol,
  strategy,
  timeframe,
  days,
  config,
  backtestConfig,
  snapshot,
  className,
  onGateChange,
  showForceOption = true,
  forceDeploy,
  onForceDeployChange,
}) {
  const gate = useMemo(
    () => evaluateDeployGate({
      results,
      symbol,
      config,
      backtestConfig,
      snapshot,
      days,
      timeframe,
      strategy,
    }),
    [results, symbol, config, backtestConfig, snapshot, days, timeframe, strategy],
  );

  React.useEffect(() => {
    onGateChange?.(gate);
  }, [gate, onGateChange]);

  const [localForce, setLocalForce] = useState(false);
  const force = forceDeploy ?? localForce;
  const setForce = onForceDeployChange ?? setLocalForce;

  const hasBacktest = Boolean(results?.run_id || results?.summary);
  const oosDone = gate.workflow_stage === 'oos_validated' || gate.workflow_stage === 'ready';
  const deployReady = gate.passed || force;

  const stepState = {
    backtest: { done: hasBacktest, active: !hasBacktest, blocked: false },
    oos: {
      done: oosDone && gate.passed,
      active: hasBacktest && !oosDone,
      blocked: gate.blocking,
    },
    deploy: { done: false, active: deployReady, blocked: gate.blocking && !force },
  };

  return (
    <section className={cn('deploy-gate', className)}>
      <p className="deploy-gate__eyebrow">Forward test before capital</p>
      <div className="deploy-gate__workflow" role="list" aria-label="Deploy workflow">
        {WORKFLOW_STEPS.map((step, idx) => {
          const st = stepState[step.id];
          return (
            <React.Fragment key={step.id}>
              {idx > 0 && (
                <span
                  className={cn(
                    'deploy-gate__connector',
                    st.done || st.active ? 'deploy-gate__connector--on' : '',
                  )}
                  aria-hidden
                />
              )}
              <div
                className={cn(
                  'deploy-gate__step',
                  st.active && 'deploy-gate__step--active',
                  st.blocked && 'deploy-gate__step--blocked',
                )}
                role="listitem"
              >
                <StepIcon active={st.active} done={st.done} blocked={st.blocked} />
                <span>{step.label}</span>
              </div>
            </React.Fragment>
          );
        })}
      </div>

      <ul className="deploy-gate__checks">
        {gate.checks.map((item) => (
          <li
            key={item.id}
            className={cn(
              'deploy-gate__check',
              item.level === 'block' && !item.ok && 'deploy-gate__check--block',
              item.level === 'warn' && !item.ok && 'deploy-gate__check--warn',
            )}
          >
            <CheckIcon level={item.level} ok={item.ok} />
            <div className="deploy-gate__check-body">
              <span>{item.message}</span>
              {item.detail && (
                <span className="deploy-gate__check-detail">{item.detail}</span>
              )}
            </div>
          </li>
        ))}
        {gate.checks.length === 0 && hasBacktest && (
          <li className="deploy-gate__check">
            <CheckIcon level="pass" ok />
            <span>Backtest results loaded — ready to deploy</span>
          </li>
        )}
      </ul>

      {gate.blocking && showForceOption && (
        <label className="deploy-gate__force">
          <input
            type="checkbox"
            checked={force}
            onChange={(e) => setForce(e.target.checked)}
          />
          <span>Deploy anyway (bypass gate — not recommended)</span>
        </label>
      )}
    </section>
  );
}

export function useDeployGateState(props) {
  const gate = useMemo(() => evaluateDeployGate(props), [props]);
  const [forceDeploy, setForceDeploy] = useState(false);
  const canDeploy = gate.passed || forceDeploy;
  return { gate, forceDeploy, setForceDeploy, canDeploy };
}
