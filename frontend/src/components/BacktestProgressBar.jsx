/**
 * Backtest progress indicator (P2).
 */
import React from 'react';
import { cn } from '@/lib/utils';
import { useResearchStore } from '../store/useResearchStore';

const PHASE_LABELS = {
  resolve: 'Loading candles',
  indicators: 'Indicators',
  simulate: 'Simulating',
  portfolio: 'Portfolio symbols',
  meta_label_wf: 'Meta-label walk-forward',
  reasoning: 'LLM explanations',
  save: 'Saving',
  done: 'Complete',
};

export default function BacktestProgressBar({ className, compact = false }) {
  const running = useResearchStore((s) => s.backtestRunning);
  const progress = useResearchStore((s) => s.backtestProgress);

  if (!running) return null;

  const pct = Math.min(100, Math.max(0, Number(progress?.pct ?? 0)));
  const phase = progress?.phase ?? 'resolve';
  const label = progress?.message ?? PHASE_LABELS[phase] ?? 'Running…';
  const skipped = progress?.skipped_symbols;

  return (
    <div className={cn('algo-backtest-progress', compact && 'algo-backtest-progress--compact', className)} aria-live="polite">
      <div className="algo-backtest-progress__track" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="algo-backtest-progress__fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="algo-backtest-progress__meta">
        <span className="algo-backtest-progress__label">{label}</span>
        <span className="algo-backtest-progress__pct num-mono">{pct}%</span>
      </div>
      {Array.isArray(skipped) && skipped.length > 0 && (
        <p className="text-[0.55rem] text-muted-foreground mt-0.5 truncate" title={skipped.map((s) => `${s.symbol}: ${s.reason}`).join(', ')}>
          Skipped: {skipped.map((s) => s.symbol).join(', ')}
        </p>
      )}
    </div>
  );
}
