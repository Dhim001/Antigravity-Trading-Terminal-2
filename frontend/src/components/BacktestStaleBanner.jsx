/**
 * BacktestStaleBanner — promote config drift warning before re-run.
 */
import React, { useMemo } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { backtestFingerprint, isBacktestStale } from '@/lib/backtestDisplay';

export default function BacktestStaleBanner({
  snapshot,
  symbol,
  strategy,
  days,
  timeframe,
  config,
  onRerun,
  className,
}) {
  const stale = useMemo(() => {
    if (!snapshot) return false;
    const current = backtestFingerprint({
      symbol,
      strategy,
      days: String(days),
      timeframe,
      config,
    });
    return isBacktestStale(snapshot, current);
  }, [snapshot, symbol, strategy, days, timeframe, config]);

  if (!stale) return null;

  return (
    <Alert variant="default" className={className}>
      <AlertTriangle data-icon="inline-start" className="size-3.5" />
      <AlertDescription className="text-xs flex flex-wrap items-center gap-2">
        <span>Config changed since last backtest — results may not match deploy settings.</span>
        {onRerun && (
          <Button type="button" variant="outline" size="xs" className="h-6" onClick={onRerun}>
            Re-run
          </Button>
        )}
      </AlertDescription>
    </Alert>
  );
}
