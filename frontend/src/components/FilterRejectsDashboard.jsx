import React from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  FILTER_REJECT_LABELS,
  filterRejectEntries,
  filterRejectTotal,
} from '@/lib/filterRejects';

/**
 * Bar-style dashboard for CHART_AGENT filter reject counts.
 * Works with live aggregates, backtest summary, or sweep row data.
 */
export default function FilterRejectsDashboard({
  rejects,
  total: totalProp,
  title = 'CHART_AGENT filter rejects',
  hint,
  className,
  compact = false,
}) {
  const total = totalProp ?? filterRejectTotal(rejects);
  const entries = filterRejectEntries(rejects);
  if (!total || entries.length === 0) return null;

  const max = Math.max(...entries.map(([, n]) => n), 1);

  return (
    <section className={cn('rounded-md border border-border/50 bg-muted/10 p-2.5', className)}>
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <Badge variant="outline" className="text-xs">
          {title}: {total}
        </Badge>
        {entries.map(([key, count]) => (
          <Badge key={key} variant="secondary" className="text-xs">
            {FILTER_REJECT_LABELS[key] ?? key}: {count}
          </Badge>
        ))}
      </div>
      {!compact && (
        <div className="flex flex-col gap-1.5">
          {entries.map(([key, count]) => (
            <div key={key} className="grid grid-cols-[4.5rem_1fr_2rem] items-center gap-2 text-xs">
              <span className="text-muted-foreground">{FILTER_REJECT_LABELS[key] ?? key}</span>
              <div className="h-2 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary/70"
                  style={{ width: `${Math.round((count / max) * 100)}%` }}
                />
              </div>
              <span className="num-mono text-right">{count}</span>
            </div>
          ))}
        </div>
      )}
      {hint && (
        <Alert className="mt-2 border-border/60 bg-transparent py-1.5">
          <AlertDescription className="text-xs text-muted-foreground">{hint}</AlertDescription>
        </Alert>
      )}
    </section>
  );
}
