import { Loader2, Play } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { retrainQueueKey } from '@/hooks/mlLabStateHelpers';

export function MlRetrainQueue({
  retrainActions,
  retrainPending,
  retrainHistory,
  runNowKey,
  training,
  validating,
  busyElsewhere,
  onRunNow,
}) {
  if (
    retrainActions.length === 0
    && retrainPending.length === 0
    && retrainHistory.length === 0
  ) {
    return null;
  }

  return (
    <section className="ml-training__card ml-training__card--warn">
      <div className="ml-training__card-head">
        <h4 className="ml-training__section-title">Retrain audit</h4>
        <span className="ml-training__header-meta">
          {retrainActions.length} due · {retrainPending.length} pending
        </span>
      </div>

      {retrainActions.length > 0 && (
        <div className="ml-training__retrain-block">
          <p className="ml-training__subsection-label">Recommended</p>
          <ul className="ml-training__retrain-list">
            {retrainActions.slice(0, 8).map((a, i) => {
              const key = retrainQueueKey(a.symbol, a.strategy, a.timeframe || '1m');
              const running = runNowKey === key;
              return (
                <li key={`${key}-${i}`} className="ml-training__retrain-row">
                  <div className="ml-training__retrain-meta">
                    <span className="num-mono font-medium">{a.strategy} / {a.symbol}</span>
                    <span className="text-muted-foreground">
                      {a.reason || 'retrain'}
                      {a.model_age_hours != null ? ` · age ${a.model_age_hours}h` : ''}
                    </span>
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-7 text-[0.65rem] gap-1 shrink-0"
                    disabled={training || validating || busyElsewhere || Boolean(runNowKey)}
                    onClick={() => onRunNow(a.strategy, a.symbol)}
                  >
                    {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                    Run now
                  </Button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {retrainPending.length > 0 && (
        <div className="ml-training__retrain-block">
          <p className="ml-training__subsection-label">Queued (auto-drain or Run now)</p>
          <ul className="ml-training__retrain-list">
            {retrainPending.slice(0, 8).map((p) => {
              const running = runNowKey === p.key;
              return (
                <li key={p.key} className="ml-training__retrain-row">
                  <div className="ml-training__retrain-meta">
                    <span className="num-mono font-medium">{p.strategy} / {p.symbol}</span>
                    <span className="text-muted-foreground">
                      {(p.reasons && p.reasons[0]) || 'queued'}
                      {p.requested_at
                        ? ` · ${new Date(p.requested_at).toLocaleString()}`
                        : ''}
                    </span>
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-7 text-[0.65rem] gap-1 shrink-0"
                    disabled={training || validating || busyElsewhere || Boolean(runNowKey)}
                    onClick={() => onRunNow(p.strategy, p.symbol)}
                  >
                    {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                    Run now
                  </Button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {retrainHistory.length > 0 && (
        <div className="ml-training__retrain-block">
          <p className="ml-training__subsection-label">Recent requests</p>
          <ul className="space-y-1 text-[0.65rem] text-muted-foreground">
            {retrainHistory.slice(0, 8).map((h, i) => (
              <li key={`${h.key || h.source}-${h.requested_at || i}`} className="num-mono">
                {h.key || '—'}
                {h.source ? ` · ${h.source}` : ''}
                {h.reason ? ` — ${h.reason}` : ''}
                {h.requested_at
                  ? ` · ${new Date(h.requested_at).toLocaleString()}`
                  : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
