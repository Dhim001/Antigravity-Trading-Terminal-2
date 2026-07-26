/**
 * Alpha decay monitor — half-life + rolling Sharpe from ml_metrics.alpha_decay.
 */
import { StatCard } from '@/components/StatCard';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { openModelTrainingDock } from '@/lib/workspaceNav';
import { Wand2 } from 'lucide-react';

const DEFAULT_SWEEP_SUGGESTION =
  'Consider running a hyperparameter sweep before retrain — the model may need architectural / hyperparam changes.';

function fmtDays(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return `${Number(v).toFixed(1)}d`;
}

function fmtNum(v, digits = 2) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(digits);
}

function RollingSharpeSpark({ values }) {
  const pts = (values || []).map(Number).filter((n) => Number.isFinite(n));
  if (pts.length < 2) return null;
  const w = 240;
  const h = 40;
  const min = Math.min(...pts, 0);
  const max = Math.max(...pts, 0);
  const span = Math.max(max - min, 1e-9);
  const zeroY = h - ((0 - min) / span) * (h - 4) - 2;
  const d = pts
    .map((v, i) => {
      const x = (i / (pts.length - 1)) * w;
      const y = h - ((v - min) / span) * (h - 4) - 2;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="alpha-decay__spark" aria-hidden>
      <line x1={0} y1={zeroY} x2={w} y2={zeroY} className="alpha-decay__spark-zero" />
      <path d={d} className="alpha-decay__spark-path" fill="none" />
    </svg>
  );
}

export default function AlphaDecayMonitor({
  alphaDecay,
  compact = false,
  className,
  suggestion,
  showSweepCta = true,
}) {
  if (!alphaDecay || (alphaDecay.half_life_days == null && !alphaDecay.rolling_sharpe?.length)) {
    return null;
  }

  const stale = alphaDecay.half_life_days != null && Number(alphaDecay.half_life_days) < 7;
  const rolling = alphaDecay.rolling_sharpe || [];
  const tip = suggestion
    || alphaDecay.suggestion
    || (stale ? DEFAULT_SWEEP_SUGGESTION : null);

  return (
    <section className={cn('alpha-decay', compact && 'alpha-decay--compact', className)}>
      <p className="algo-backtest-table-scroll__caption mb-1.5">Alpha decay</p>
      <div className="algo-backtest-stat-grid algo-backtest-stat-grid--compact mb-2">
        <StatCard
          label="Half-life"
          value={fmtDays(alphaDecay.half_life_days)}
          tone={stale ? 'down' : 'neutral'}
        />
        <StatCard label="Early Sharpe" value={fmtNum(alphaDecay.early_sharpe)} />
        <StatCard label="Late Sharpe" value={fmtNum(alphaDecay.late_sharpe)} tone={stale ? 'down' : 'neutral'} />
        {alphaDecay.window_bars != null && (
          <StatCard label="Window" value={`${alphaDecay.window_bars} bars`} />
        )}
      </div>
      {!compact && rolling.length > 1 && (
        <div>
          <p className="text-[0.5rem] uppercase text-muted-foreground mb-0.5">Rolling Sharpe</p>
          <RollingSharpeSpark values={rolling} />
        </div>
      )}
      {stale && (
        <p className="text-[0.65rem] text-amber-400/90 mt-1.5">
          Short half-life — edge may be fading; consider retraining.
        </p>
      )}
      {tip && (
        <div className="mt-1.5 rounded border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 space-y-1">
          <p className="text-[0.65rem] text-amber-200/90 leading-snug">{tip}</p>
          {showSweepCta && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-6 text-[0.6rem] gap-1 border-amber-500/40"
              onClick={() => openModelTrainingDock()}
            >
              <Wand2 size={11} aria-hidden />
              Open Auto-Tune
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
