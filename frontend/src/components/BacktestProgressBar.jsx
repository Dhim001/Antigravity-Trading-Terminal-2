/**
 * Backtest progress indicator (P2).
 */
import React, { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { useResearchStore } from '../store/useResearchStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { stopBacktestJobPolling } from '../lib/backtestPolling';
import { clearBacktestClientTimeout } from '../lib/backtestTimeouts';

const PHASE_LABELS = {
  resolve: 'Loading candles',
  indicators: 'Computing indicators',
  features: 'Building ML features',
  simulate: 'Running simulation',
  portfolio: 'Portfolio symbols',
  meta_label_wf: 'Meta-label walk-forward',
  reasoning: 'LLM explanations',
  queued: 'Queued in Jobs',
  save: 'Saving',
  sweep: 'Parameter sweep',
  done: 'Complete',
};

function formatEta(etaSec) {
  const n = Number(etaSec);
  if (!Number.isFinite(n) || n < 5) return null;
  if (n >= 3600) return `~${Math.round(n / 3600)}h left`;
  if (n >= 120) return `~${Math.round(n / 60)}m left`;
  return `~${Math.round(n)}s left`;
}

function shortJobId(jobId) {
  const id = String(jobId || '').trim();
  if (!id) return null;
  return id.length > 8 ? id.slice(0, 8) : id;
}

/** Clarify opaque "Starting…" / bar-0 simulate stalls for background jobs. */
export function formatBacktestProgressLabel(progress, { jobId } = {}) {
  const phase = progress?.phase ?? 'resolve';
  const pct = Math.min(100, Math.max(0, Number(progress?.pct ?? 0)));
  let label = progress?.message ?? PHASE_LABELS[phase] ?? 'Running…';

  // Soften client-side placeholder before first server progress arrives.
  if (/^starting (backtest|sweep|walk-forward)/i.test(String(label).trim()) && pct <= 0) {
    label = jobId
      ? 'Background job accepted — waiting for worker…'
      : /sweep|walk-forward/i.test(label)
        ? 'Starting optimizer — contacting server…'
        : 'Starting backtest — contacting server…';
  }

  // Early ML precompute maps onto the first ~10% of the bar span but still
  // arrives as phase=simulate / "Simulating: bar 0/…" — make that readable.
  if (
    phase === 'simulate'
    && pct < 12
    && /simulat/i.test(label)
    && /\bbar\s+0\b/i.test(label)
  ) {
    label = 'Building ML features / warming model…';
  }

  const etaLabel = formatEta(progress?.eta_sec);
  if (etaLabel && !/left|est\./i.test(label)) {
    label = `${label} · ${etaLabel}`;
  }
  return label;
}

export async function cancelWatchedBacktestJob() {
  stopBacktestJobPolling();
  clearBacktestClientTimeout();
  const store = useResearchStore.getState();
  store.setBacktestRunning(false);
  store.setBacktestProgress(null);
  const jobId = store.backtestJobId;
  if (jobId) {
    store.upsertBacktestJobSlot?.(jobId, { status: 'cancelled', running: false });
  }
  const { ok, error } = await sendAction(
    Action.CANCEL_BACKTEST,
    jobId ? { job_id: jobId } : {},
  );
  if (!ok) {
    toast.error(error || 'Cancel request could not be delivered — the run may still be going');
    return { ok: false, error };
  }
  toast.message('Backtest cancel requested');
  return { ok: true };
}

export default function BacktestProgressBar({
  className,
  compact = false,
  showCancel = true,
}) {
  const running = useResearchStore((s) => s.backtestRunning);
  const progress = useResearchStore((s) => s.backtestProgress);
  const jobId = useResearchStore((s) => s.backtestJobId);
  const lastRequest = useResearchStore((s) => s.backtestLastRequest);
  const [cancelling, setCancelling] = useState(false);

  if (!running) return null;

  const pct = Math.min(100, Math.max(0, Number(progress?.pct ?? 0)));
  const phase = progress?.phase ?? 'resolve';
  const label = formatBacktestProgressLabel(progress, { jobId });
  const skipped = progress?.skipped_symbols;
  const rate = Number(progress?.bars_per_sec);
  const rateLabel = Number.isFinite(rate) && rate > 0
    ? `${rate >= 10 ? Math.round(rate) : rate.toFixed(1)} bars/s`
    : null;

  const symbol = progress?.symbol || lastRequest?.symbol || null;
  const strategy = progress?.strategy || lastRequest?.strategy || null;
  const identity = [symbol, strategy].filter(Boolean).join(' · ');
  const jobShort = shortJobId(progress?.job_id || jobId);
  const isBackground = Boolean(jobShort);
  const isSweep = phase === 'sweep' || Boolean(lastRequest?.sweep);

  const onCancel = async () => {
    if (cancelling) return;
    setCancelling(true);
    try {
      await cancelWatchedBacktestJob();
    } finally {
      setCancelling(false);
    }
  };

  return (
    <div className={cn('algo-backtest-progress', compact && 'algo-backtest-progress--compact', className)} aria-live="polite">
      <div className="algo-backtest-progress__track" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="algo-backtest-progress__fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="algo-backtest-progress__meta">
        <span className="algo-backtest-progress__label">{label}</span>
        <span className="algo-backtest-progress__pct num-mono">
          {rateLabel ? `${rateLabel} · ` : ''}{pct}%
        </span>
      </div>
      {(identity || jobShort || isBackground || isSweep) && (
        <p className="algo-backtest-progress__context text-[0.55rem] text-muted-foreground mt-0.5 truncate">
          {identity || (isSweep ? 'Optimizer' : 'Backtest')}
          {jobShort ? ` · job ${jobShort}` : ''}
          {PHASE_LABELS[phase] ? ` · ${PHASE_LABELS[phase]}` : ''}
          {progress?.trial != null && progress?.max_trials != null
            ? ` · trial ${progress.trial}/${progress.max_trials}`
            : progress?.trial != null
              ? ` · trial ${progress.trial}`
              : ''}
          {progress?.best_score != null ? ` · best ${progress.best_score}` : ''}
          {isBackground ? ' · background (safe to switch tabs / Jobs)' : ''}
        </p>
      )}
      {showCancel && (
        <div className="mt-1 flex justify-end">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[0.6rem]"
            disabled={cancelling}
            onClick={onCancel}
          >
            {cancelling ? <Loader2 size={10} className="animate-spin" /> : null}
            Cancel
          </Button>
        </div>
      )}
      {phase === 'queued' && (
        <p className="text-[0.55rem] text-muted-foreground mt-0.5">
          Waiting for a free worker — another heavy job may be ahead. Track it under Jobs.
        </p>
      )}
      {Array.isArray(skipped) && skipped.length > 0 && (
        <p className="text-[0.55rem] text-muted-foreground mt-0.5 truncate" title={skipped.map((s) => `${s.symbol}: ${s.reason}`).join(', ')}>
          Skipped: {skipped.map((s) => s.symbol).join(', ')}
        </p>
      )}
    </div>
  );
}
