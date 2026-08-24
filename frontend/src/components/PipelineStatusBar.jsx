/**
 * Horizontal pipeline stepper — Train → Validate → Backtest → Gate → Deploy.
 * Completed stages are clickable to review results; expandable transition log.
 */
import { useEffect, useState, useSyncExternalStore } from 'react';
import { CheckCircle2, ChevronDown, Circle, Loader2, XCircle } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  getMlPipeline,
  isPipelineActive,
  PIPELINE_STEPPER_STAGES,
  subscribeMlPipeline,
} from '@/lib/mlPipeline';
import { navigatePipelineStageReview } from '@/lib/pipelineNav';
import { formatElapsed } from '@/components/ml-lab/MlLabConstants';

function stageStatus(pipeline, stageId) {
  const order = PIPELINE_STEPPER_STAGES.map((s) => s.id);
  const current = pipeline.stage;
  if (current === 'ERROR' || current === 'GATE_FAILED') {
    if (current === 'GATE_FAILED' && (stageId === 'GATE_CHECK' || stageId === 'READY_TO_DEPLOY')) {
      return 'failed';
    }
    const failIdx = order.indexOf(
      pipeline.errors?.[pipeline.errors.length - 1]?.stage || current,
    );
    const idx = order.indexOf(stageId);
    if (failIdx >= 0 && idx === failIdx) return 'failed';
    if (failIdx >= 0 && idx < failIdx) return 'done';
    return 'pending';
  }
  if (current === 'IDLE') return 'pending';
  if (stageId === 'SEARCH') {
    if (pipeline.profile === 'retrain') return 'done';
    if (!pipeline.ownedByServer && current !== 'SEARCH') return 'done';
  }
  if (current === 'DEPLOYED') {
    return stageId === 'DEPLOYED' || order.indexOf(stageId) < order.indexOf('DEPLOYED')
      ? 'done'
      : 'pending';
  }
  const curIdx = order.indexOf(current === 'READY_TO_DEPLOY' ? 'READY_TO_DEPLOY' : current);
  const idx = order.indexOf(stageId);
  if (idx < 0) return 'pending';
  // Early completion (stop-after-validate): mark reached stages done, not spinning.
  if (pipeline.completedAt != null) {
    return idx <= curIdx ? 'done' : 'pending';
  }
  if (idx < curIdx) return 'done';
  if (idx === curIdx) return 'active';
  return 'pending';
}

function StageElapsed({ pipeline, stageId, status }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (status !== 'active') return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [status, pipeline.stageStartedAt]);

  if (status === 'active' && pipeline.stageStartedAt) {
    return <span className="num-mono">{formatElapsed(now - pipeline.stageStartedAt)}…</span>;
  }
  const stored = pipeline.stageElapsed?.[stageId];
  if (stored != null && status === 'done') {
    return <span className="num-mono">{formatElapsed(stored)}</span>;
  }
  return null;
}

function formatLogTime(ts) {
  if (ts == null) return '—';
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '—';
  }
}

function stageLabel(id) {
  if (!id) return '—';
  if (String(id).endsWith(':done')) {
    const base = String(id).replace(/:done$/, '');
    const hit = PIPELINE_STEPPER_STAGES.find((s) => s.id === base);
    return hit ? `${hit.label} done` : id;
  }
  return PIPELINE_STEPPER_STAGES.find((s) => s.id === id)?.label || id;
}

function PipelineTransitionLog({ pipeline }) {
  const log = pipeline.transitionLog || [];
  if (!log.length) return null;
  return (
    <details className="pipeline-status-bar__log">
      <summary className="pipeline-status-bar__log-summary">
        <ChevronDown size={10} aria-hidden />
        Pipeline log ({log.length})
      </summary>
      <ol className="pipeline-status-bar__log-list">
        {[...log].reverse().map((entry, i) => (
          <li key={`${entry.timestamp}-${entry.to}-${i}`} className="pipeline-status-bar__log-item">
            <span className="num-mono text-muted-foreground">{formatLogTime(entry.timestamp)}</span>
            <span>
              {stageLabel(entry.from)} → {stageLabel(entry.to)}
              {entry.elapsedMs != null ? (
                <span className="num-mono text-muted-foreground"> · {formatElapsed(entry.elapsedMs)}</span>
              ) : null}
              {entry.error ? (
                <span className="text-destructive" title={entry.error}> · {entry.error}</span>
              ) : null}
            </span>
          </li>
        ))}
      </ol>
    </details>
  );
}

export default function PipelineStatusBar({ compact = false, className }) {
  const pipeline = useSyncExternalStore(
    subscribeMlPipeline,
    getMlPipeline,
    getMlPipeline,
  );

  if (!pipeline.pipelineId || pipeline.stage === 'IDLE') {
    return null;
  }

  const active = isPipelineActive(pipeline);
  const stages = PIPELINE_STEPPER_STAGES.filter((s) => s.id !== 'DEPLOYED' || pipeline.stage === 'DEPLOYED');

  if (compact) {
    const label = PIPELINE_STEPPER_STAGES.find((s) => s.id === pipeline.stage)?.label
      || pipeline.stage;
    return (
      <div
        className={cn('pipeline-status-bar pipeline-status-bar--compact', className)}
        title={pipeline.lastError || `${pipeline.strategy} / ${pipeline.symbol}`}
      >
        {active ? (
          <Loader2 size={12} className="animate-spin shrink-0" aria-hidden />
        ) : pipeline.stage === 'ERROR' || pipeline.stage === 'GATE_FAILED' ? (
          <XCircle size={12} className="text-destructive shrink-0" aria-hidden />
        ) : (
          <CheckCircle2 size={12} className="text-emerald-500 shrink-0" aria-hidden />
        )}
        <span className="text-[0.65rem]">
          Pipeline · {label}
          {pipeline.symbol ? ` · ${pipeline.symbol}` : ''}
        </span>
      </div>
    );
  }

  return (
    <div
      className={cn('pipeline-status-bar', className)}
      role="status"
      aria-live="polite"
      aria-label="ML pipeline progress"
    >
      <div className="pipeline-status-bar__meta text-[0.6rem] text-muted-foreground num-mono">
        {pipeline.strategy || '—'} · {pipeline.symbol || '—'} · {pipeline.timeframe || '—'}
        {pipeline.lastError ? (
          <span className="text-destructive ml-2" title={pipeline.lastError}>
            {pipeline.lastError}
          </span>
        ) : null}
      </div>
      <ol className="pipeline-status-bar__steps">
        {stages.map((step, i) => {
          const status = stageStatus(pipeline, step.id);
          const clickable = status === 'done';
          const Tag = clickable ? 'button' : 'span';
          return (
            <li
              key={step.id}
              className={cn(
                'pipeline-status-bar__step',
                status === 'done' && 'pipeline-status-bar__step--done',
                status === 'active' && 'pipeline-status-bar__step--active',
                status === 'failed' && 'pipeline-status-bar__step--failed',
                clickable && 'pipeline-status-bar__step--clickable',
              )}
            >
              {i > 0 && <span className="pipeline-status-bar__arrow" aria-hidden>→</span>}
              <Tag
                type={clickable ? 'button' : undefined}
                className="pipeline-status-bar__hit"
                title={clickable ? `Review ${step.label} results` : undefined}
                onClick={clickable ? () => navigatePipelineStageReview(step.id) : undefined}
              >
                <span className="pipeline-status-bar__icon" aria-hidden>
                  {status === 'done' && <CheckCircle2 size={12} />}
                  {status === 'active' && <Loader2 size={12} className="animate-spin" />}
                  {status === 'failed' && <XCircle size={12} />}
                  {status === 'pending' && <Circle size={12} />}
                </span>
                <span className="pipeline-status-bar__label">{step.label}</span>
                <StageElapsed pipeline={pipeline} stageId={step.id} status={status} />
              </Tag>
            </li>
          );
        })}
      </ol>
      <PipelineTransitionLog pipeline={pipeline} />
    </div>
  );
}
