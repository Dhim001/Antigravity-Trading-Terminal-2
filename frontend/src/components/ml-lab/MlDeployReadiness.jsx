import { CheckCircle2, FlaskConical, XCircle } from 'lucide-react';
import { cn } from '@/lib/utils';
import { fmtMetric } from '@/components/ml-lab/MlLabConstants';

/** Deploy-gate mirror: trained / walk-forward / PBO from model-status enrich. */
export function DeployReadinessStrip({ status, strategy }) {
  if (!status?.trained) return null;

  const strat = String(strategy || status?.strategy || '').toUpperCase();
  const isRl = strat === 'RL_PPO_AGENT';
  const wf = status.walk_forward && typeof status.walk_forward === 'object'
    ? status.walk_forward
    : null;
  const pbo = status.pbo && typeof status.pbo === 'object' ? status.pbo : null;
  const validatedAt = status.validated_at || wf?.validated_at || null;
  const cal = status.data_calendar && typeof status.data_calendar === 'object'
    ? status.data_calendar
    : null;

  const trainedOk = true;
  const wfOk = Boolean(wf?.ok && validatedAt);
  const wfMissing = !validatedAt || !wf?.ok;
  const pboSkipped = Boolean(pbo?.skipped);
  const pboPresent = pbo != null && pbo.pbo != null && !pboSkipped;
  const pboOk = pboPresent && pbo.ok === true;
  const pboWarn = pboPresent && pbo.ok === false;
  const holdoutOk = Boolean(cal?.holdout_days && cal?.fit_end_ts);

  const ageLabel = (() => {
    if (!validatedAt) return null;
    try {
      const d = new Date(validatedAt);
      if (Number.isNaN(d.getTime())) return null;
      return d.toLocaleString();
    } catch {
      return null;
    }
  })();

  const chip = (ok, warn, label, title) => (
    <span
      className={cn(
        'ml-training__ready-chip',
        ok && 'ml-training__ready-chip--ok',
        warn && 'ml-training__ready-chip--warn',
        !ok && !warn && 'ml-training__ready-chip--fail',
      )}
      title={title}
    >
      {ok ? <CheckCircle2 size={11} aria-hidden /> : warn ? <FlaskConical size={11} aria-hidden /> : <XCircle size={11} aria-hidden />}
      {label}
    </span>
  );

  return (
    <section className="ml-training__ready" aria-label="Deploy readiness">
      <div className="ml-training__ready-head">
        <h4 className="ml-training__section-title">Deploy readiness</h4>
        {ageLabel && (
          <span className="ml-training__header-meta num-mono">
            validated {ageLabel}
          </span>
        )}
      </div>
      <div className="ml-training__ready-chips">
        {chip(trainedOk, false, 'Trained', 'Model artifact on disk')}
        {chip(
          wfOk,
          false,
          wfOk
            ? `Walk-forward${
              wf?.mean_oos_return_pct != null
                ? ` · ${Number(wf.mean_oos_return_pct) >= 0 ? '+' : ''}${Number(wf.mean_oos_return_pct).toFixed(2)}%`
                : (wf?.mean_oos_accuracy != null ? ` · ${fmtMetric(wf.mean_oos_accuracy, 3, 'mean_oos_accuracy')}` : '')
            }`
            : 'Walk-forward',
          wfMissing
            ? (isRl
              ? 'Run Walk-forward (RL episode returns) before deploy — gate will block without it'
              : 'Run Walk-forward before deploy — gate will block without it')
            : (wf?.recommendation || 'Walk-forward validation passed'),
        )}
        {pboSkipped
          ? chip(false, true, 'PBO skipped', pbo?.error || 'PBO skipped for RL/deep interactive validate')
          : pboPresent
            ? chip(
              pboOk,
              pboWarn,
              `PBO ${fmtMetric(pbo.pbo, 3, 'pbo') ?? '—'}`,
              pboOk
                ? 'PBO under 50% — acceptable overfitting risk'
                : 'PBO ≥ 50% — elevated overfitting risk for deploy',
            )
            : chip(false, true, 'PBO', isRl
              ? 'RL Lab Validate skips PBO (set force_pbo for overnight CSCV)'
              : 'No PBO result yet — run Walk-forward + PBO')}
        {cal && chip(
          holdoutOk,
          false,
          holdoutOk ? `Holdout · ${cal.holdout_days}d` : 'Holdout',
          holdoutOk
            ? 'Champion FIT ends before locked holdout — use Algo BT on holdout only'
            : 'Train with ML_CALENDAR_HOLDOUT=1 to stamp FIT / holdout',
        )}
      </div>
    </section>
  );
}

export function DataCalendarStrip({ calendar, trainingWindow }) {
  const cal = calendar && typeof calendar === 'object' ? calendar : null;
  if (!cal?.fit_end_ts && !cal?.holdout_days) {
    const months = Number(trainingWindow) || 3;
    const holdout = months <= 1 ? 7 : Math.min(60, Math.max(14, Math.round(months * 30 * 0.15)));
    return (
      <p className="text-[10px] text-muted-foreground mt-1 leading-snug">
        Calendar (when <span className="font-mono">ML_CALENDAR_HOLDOUT=1</span>): FIT → embargo → HOLDOUT (~{holdout}d).
        Train/Validate use FIT only; Algo ML BT defaults to holdout.
      </p>
    );
  }
  const fitDays = cal.fit_days != null ? `${cal.fit_days}d` : '—';
  const embargo = cal.embargo_bars != null ? `${cal.embargo_bars} bars` : '—';
  const holdout = cal.holdout_days != null ? `${cal.holdout_days}d` : '—';
  return (
    <p className="text-[10px] text-muted-foreground mt-1 leading-snug" title="Locked OOS holdout after FIT">
      <span className="text-foreground/80">FIT</span> ~{fitDays}
      {' · '}
      <span className="text-foreground/80">EMBARGO</span> {embargo}
      {' · '}
      <span className="text-foreground/80">HOLDOUT</span> {holdout}
    </p>
  );
}
