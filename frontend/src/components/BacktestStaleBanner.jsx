/**
 * BacktestStaleBanner — promote config / model drift warning before re-run.
 */
import React, { useMemo } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { backtestFingerprint, backtestStaleReason, isBacktestStale } from '@/lib/backtestDisplay';

export default function BacktestStaleBanner({
  snapshot,
  symbol,
  strategy,
  days,
  timeframe,
  config,
  simMode,
  onRerun,
  className,
}) {
  const { stale, reason } = useMemo(() => {
    if (!snapshot) return { stale: false, reason: null };
    const current = backtestFingerprint({
      symbol,
      strategy,
      days: String(days),
      timeframe,
      config,
      simMode,
    });
    if (!isBacktestStale(snapshot, current)) return { stale: false, reason: null };
    return { stale: true, reason: backtestStaleReason(snapshot, current) };
  }, [snapshot, symbol, strategy, days, timeframe, config, simMode]);

  if (!stale) return null;

  const message = reason === 'model'
    ? 'Model changed since last backtest — results may not match the active artifact. Re-run.'
    : 'Config changed since last backtest — results may not match deploy settings.';

  return (
    <Alert variant="default" className={className}>
      <AlertTriangle data-icon="inline-start" className="size-3.5" />
      <AlertDescription className="text-xs flex flex-wrap items-center gap-2">
        <span>{message}</span>
        {onRerun && (
          <Button type="button" variant="outline" size="xs" className="h-6" onClick={onRerun}>
            Re-run
          </Button>
        )}
      </AlertDescription>
    </Alert>
  );
}
