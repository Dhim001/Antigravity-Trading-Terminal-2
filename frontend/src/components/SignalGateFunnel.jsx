/**
 * Signal gate funnel — pass/reject counts per pipeline stage.
 *
 * @param {{
 *   stages?: Array<{ name: string; passed: number; rejected: number }>;
 *   className?: string;
 * }} props
 */
import { cn } from '@/lib/utils';

export default function SignalGateFunnel({ stages = [], className }) {
  const rows = (stages || []).filter((s) => s && s.name);

  if (!rows.length) {
    return (
      <p className={cn('gate-funnel gate-funnel--empty text-xs text-muted-foreground', className)}>
        No gate funnel data.
      </p>
    );
  }

  const firstIn = Math.max(
    Number(rows[0].passed) + Number(rows[0].rejected),
    Number(rows[0].passed),
    1,
  );
  const lastPassed = Number(rows[rows.length - 1].passed);
  const conversion = firstIn > 0 ? (lastPassed / firstIn) * 100 : 0;

  return (
    <div className={cn('gate-funnel', className)} role="list" aria-label="Signal gate funnel">
      <div className="gate-funnel__header">
        <span className="text-[0.55rem] uppercase text-muted-foreground">Conversion</span>
        <span className="num-mono text-sm font-semibold">{conversion.toFixed(1)}%</span>
      </div>
      <ul className="gate-funnel__list">
        {rows.map((stage) => {
          const passed = Number(stage.passed) || 0;
          const rejected = Number(stage.rejected) || 0;
          const total = Math.max(passed + rejected, 1);
          const passPct = (passed / total) * 100;
          const rejectPct = (rejected / total) * 100;
          return (
            <li key={stage.name} className="gate-funnel__row" role="listitem">
              <div className="gate-funnel__meta">
                <span className="gate-funnel__name">{stage.name}</span>
                <span className="gate-funnel__counts num-mono text-muted-foreground">
                  {passed} pass · {rejected} reject
                </span>
              </div>
              <div className="gate-funnel__bar" title={`${passPct.toFixed(0)}% passed`}>
                <div className="gate-funnel__pass" style={{ width: `${passPct}%` }} />
                <div className="gate-funnel__reject" style={{ width: `${rejectPct}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
