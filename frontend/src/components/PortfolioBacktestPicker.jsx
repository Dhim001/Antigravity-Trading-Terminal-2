import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Layers } from 'lucide-react';
import { Action } from '../api/protocol';
import { invokeHttpAction } from '../api/transport';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import {
  PORTFOLIO_BACKTEST_MAX,
  PORTFOLIO_BACKTEST_MIN,
  canRunPortfolioBacktest,
  defaultPortfolioSymbols,
  portfolioModeBlocked,
  togglePortfolioSymbol,
  uniqueSymbols,
} from '@/lib/portfolioBacktest';

export default function PortfolioBacktestPicker({
  enabled,
  onEnabledChange,
  selectedSymbols,
  onSelectedChange,
  watchlist,
  activeSymbol,
  oos,
  walkForward,
  runEstimate = null,
}) {
  const blocked = portfolioModeBlocked({ oos, walkForward });
  const watchlistOptions = useMemo(
    () => uniqueSymbols([activeSymbol, ...(watchlist || [])]),
    [activeSymbol, watchlist],
  );
  const selected = useMemo(() => uniqueSymbols(selectedSymbols), [selectedSymbols]);
  const runnable = canRunPortfolioBacktest(selected);

  const [correlation, setCorrelation] = useState(null);
  const [corrLoading, setCorrLoading] = useState(false);

  const loadCorrelation = useCallback(async (symbols) => {
    if (symbols.length < PORTFOLIO_BACKTEST_MIN) {
      setCorrelation(null);
      return;
    }
    setCorrLoading(true);
    try {
      const res = await invokeHttpAction(Action.RISK_BASKET_CORRELATION, { symbols });
      setCorrelation(res?.data?.basket_correlation ?? null);
    } catch {
      setCorrelation(null);
    } finally {
      setCorrLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled || blocked) {
      setCorrelation(null);
      return;
    }
    const timer = setTimeout(() => loadCorrelation(selected), 350);
    return () => clearTimeout(timer);
  }, [enabled, blocked, selected, loadCorrelation]);

  const handleToggleEnabled = (checked) => {
    onEnabledChange(checked);
    if (checked && selected.length < PORTFOLIO_BACKTEST_MIN) {
      onSelectedChange(defaultPortfolioSymbols(activeSymbol, watchlist));
    }
  };

  const handleChipClick = (sym) => {
    onSelectedChange(togglePortfolioSymbol(selected, sym));
  };

  return (
    <div className="space-y-2">
      <label
        className={cn(
          'flex items-center gap-2 text-[0.62rem] cursor-pointer',
          blocked ? 'text-muted-foreground/50 cursor-not-allowed' : 'text-muted-foreground',
        )}
        title={
          blocked
            ? 'Disable hold-out test and walk-forward before portfolio backtest'
            : undefined
        }
      >
        <Checkbox
          checked={enabled && !blocked}
          disabled={blocked}
          onCheckedChange={(v) => handleToggleEnabled(Boolean(v))}
          className="size-3.5"
        />
        <Layers size={12} aria-hidden />
        Portfolio backtest — same strategy across multiple symbols
      </label>

      {enabled && !blocked && (
        <div className="rounded-md border border-border/60 bg-muted/20 p-2 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <Label className="text-[0.58rem] text-muted-foreground">
              Symbols ({selected.length}/{PORTFOLIO_BACKTEST_MAX}, min {PORTFOLIO_BACKTEST_MIN})
            </Label>
            {!runnable && (
              <Badge variant="outline" className="text-[0.55rem]">Pick {PORTFOLIO_BACKTEST_MIN}+ symbols</Badge>
            )}
          </div>
          <div className="flex flex-wrap gap-1">
            {watchlistOptions.map((sym) => {
              const active = selected.includes(sym);
              const atCap = !active && selected.length >= PORTFOLIO_BACKTEST_MAX;
              return (
                <button
                  key={sym}
                  type="button"
                  disabled={atCap}
                  onClick={() => handleChipClick(sym)}
                  className={cn(
                    'rounded px-1.5 py-0.5 text-[0.58rem] num-mono border transition-colors',
                    active
                      ? 'border-primary bg-primary/15 text-foreground'
                      : 'border-border/60 text-muted-foreground hover:border-primary/40',
                    atCap && 'opacity-40 cursor-not-allowed',
                  )}
                >
                  {sym}
                </button>
              );
            })}
          </div>

          {(correlation?.warning || corrLoading || runEstimate) && (
            <Alert variant={correlation?.warning ? 'destructive' : 'default'} className="py-1.5 px-2">
              <AlertTriangle className="size-3.5" />
              <AlertDescription className="text-[0.58rem] leading-snug">
                {corrLoading
                  ? 'Checking basket correlation…'
                  : correlation?.warning
                    ? (correlation?.message || 'High correlation detected in basket.')
                    : runEstimate}
              </AlertDescription>
            </Alert>
          )}
        </div>
      )}
    </div>
  );
}
