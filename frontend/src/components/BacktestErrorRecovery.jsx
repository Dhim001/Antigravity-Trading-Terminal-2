/**
 * BacktestErrorRecovery — retry shortcuts after a failed run.
 */
import React from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';

export default function BacktestErrorRecovery({
  error,
  lastRequest,
  onRetry,
  onDismiss,
  className,
}) {
  if (!error) return null;

  const days = parseInt(lastRequest?.days, 10) || 7;
  const hadReasoning = Boolean(lastRequest?.reasoning);
  const hadPortfolio = (lastRequest?.portfolio_symbols?.length ?? 0) >= 2;

  const actions = [];
  if (days > 7) {
    actions.push({
      id: 'fewer_days',
      label: `Retry ${Math.max(7, Math.floor(days / 2))}d`,
      patch: { days: String(Math.max(7, Math.floor(days / 2))) },
    });
  }
  if (hadReasoning) {
    actions.push({
      id: 'no_reasoning',
      label: 'Retry without LLM',
      patch: { reasoning: false },
    });
  }
  if (hadPortfolio) {
    actions.push({
      id: 'single_symbol',
      label: 'Retry single symbol',
      patch: { portfolio_symbols: undefined },
    });
  }
  actions.push({
    id: 'repeat',
    label: 'Retry same',
    patch: {},
  });

  return (
    <Alert variant="destructive" className={className}>
      <AlertTriangle data-icon="inline-start" className="size-3.5" />
      <AlertTitle className="text-xs">Backtest failed</AlertTitle>
      <AlertDescription className="text-[0.62rem] leading-snug flex flex-col gap-2">
        <p className="m-0">{error}</p>
        <div className="flex flex-wrap gap-1.5">
          {actions.map((action) => (
            <Button
              key={action.id}
              type="button"
              variant="outline"
              size="xs"
              className="h-6 text-[0.58rem]"
              onClick={() => onRetry?.({ ...lastRequest, ...action.patch })}
            >
              <RotateCcw data-icon="inline-start" />
              {action.label}
            </Button>
          ))}
          {onDismiss && (
            <Button
              type="button"
              variant="ghost"
              size="xs"
              className="h-6 text-[0.58rem]"
              onClick={onDismiss}
            >
              Dismiss
            </Button>
          )}
        </div>
      </AlertDescription>
    </Alert>
  );
}
