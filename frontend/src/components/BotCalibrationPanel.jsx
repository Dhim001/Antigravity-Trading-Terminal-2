import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import FilterRejectsDashboard from './FilterRejectsDashboard';
import {
  applyCalibrationSuggestions,
  fetchBotCalibration,
  fetchFilterRejects,
} from '../api/endpoints';

function pct(value) {
  if (value == null || Number.isNaN(value)) return '—';
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function CalibrationTable({ buckets, emptyLabel }) {
  if (!buckets?.length) {
    return <p className="text-xs text-muted-foreground m-0">{emptyLabel}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="terminal-table m-0 w-full text-xs">
        <thead>
          <tr>
            <th>Setup</th>
            <th className="text-right">N</th>
            <th className="text-right">Win%</th>
            <th className="text-right">Wilson↓</th>
            <th className="text-right">Exp</th>
            <th className="text-right">PnL</th>
          </tr>
        </thead>
        <tbody>
          {buckets.map((row) => {
            const label = [
              row.symbol,
              row.timeframe,
              row.atr_regime,
              `s${row.score_bucket}`,
              row.confidence_bucket,
            ].filter(Boolean).join(' · ');
            const expPos = Number(row.expectancy) >= 0;
            return (
              <tr key={`${label}-${row.sample_size}`}>
                <td className="max-w-[12rem] truncate" title={label}>{label}</td>
                <td className="text-right num-mono">{row.sample_size}</td>
                <td className="text-right num-mono">{pct(row.win_rate)}</td>
                <td className="text-right num-mono">{pct(row.wilson_lower)}</td>
                <td className={cn('text-right num-mono', expPos ? 'text-trading-up' : 'text-trading-down')}>
                  {Number(row.expectancy).toFixed(2)}
                </td>
                <td className={cn('text-right num-mono', row.total_pnl >= 0 ? 'text-trading-up' : 'text-trading-down')}>
                  ${Number(row.total_pnl).toFixed(2)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function BotCalibrationPanel({
  botId,
  symbol,
  strategy,
  className,
}) {
  const [loading, setLoading] = useState(true);
  const [calibration, setCalibration] = useState(null);
  const [filterData, setFilterData] = useState(null);
  const [error, setError] = useState(null);
  const [applying, setApplying] = useState(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [cal, fr] = await Promise.all([
        fetchBotCalibration({ botId, symbol, minSamples: 3 }),
        fetchFilterRejects({ botId, symbol, strategy }),
      ]);
      setCalibration(cal);
      setFilterData(fr);
    } catch (e) {
      setError(e?.message || 'Failed to load calibration');
    } finally {
      setLoading(false);
    }
  }, [botId, symbol, strategy]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await loadData();
      if (cancelled) return;
    })();
    return () => { cancelled = true; };
  }, [loadData]);

  const handleApply = async ({ kinds, applyAll = false, label }) => {
    if (!botId || applying) return;
    setApplying(label || 'apply');
    try {
      const result = await applyCalibrationSuggestions({
        botId,
        symbol,
        kinds,
        applyAll,
      });
      const patchKeys = Object.keys(result.patch || {});
      if (patchKeys.length === 0) {
        toast.message(result.message || 'No suggestions to apply');
      } else {
        toast.success(`Applied: ${patchKeys.join(', ')}`);
        await loadData();
      }
    } catch (e) {
      toast.error(e?.message || 'Failed to apply suggestions');
    } finally {
      setApplying(null);
    }
  };

  if (loading) {
    return (
      <div className={cn('flex items-center gap-2 text-xs text-muted-foreground py-3', className)}>
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
        Loading calibration…
      </div>
    );
  }

  if (error) {
    return (
      <Alert variant="destructive" className={className}>
        <AlertDescription className="text-xs">{error}</AlertDescription>
      </Alert>
    );
  }

  const overall = calibration?.overall;
  const suggestions = calibration?.suggestions ?? [];
  const symbolThresholds = calibration?.symbol_thresholds ?? {};
  const liveRejects = filterData?.live;
  const backtestRejects = filterData?.backtest;

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      {overall && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div className="rounded-md border border-border/50 px-2 py-1.5">
            <span className="block text-xs text-muted-foreground">Closed trades</span>
            <strong className="text-sm num-mono">{overall.closed_trades}</strong>
          </div>
          <div className="rounded-md border border-border/50 px-2 py-1.5">
            <span className="block text-xs text-muted-foreground">Win rate</span>
            <strong className="text-sm num-mono">{pct(overall.win_rate)}</strong>
          </div>
          <div className="rounded-md border border-border/50 px-2 py-1.5">
            <span className="block text-xs text-muted-foreground">Wilson lower</span>
            <strong className="text-sm num-mono">{pct(overall.wilson_lower)}</strong>
          </div>
          <div className="rounded-md border border-border/50 px-2 py-1.5">
            <span className="block text-xs text-muted-foreground">Total PnL</span>
            <strong className={cn('text-sm num-mono', overall.total_pnl >= 0 ? 'text-trading-up' : 'text-trading-down')}>
              ${Number(overall.total_pnl).toFixed(2)}
            </strong>
          </div>
        </div>
      )}

      {suggestions.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium">Threshold suggestions</span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 text-xs gap-1"
              disabled={Boolean(applying)}
              onClick={() => handleApply({ applyAll: true, label: 'apply-all' })}
            >
              {applying === 'apply-all' ? (
                <Loader2 className="size-3 animate-spin" aria-hidden />
              ) : (
                <Sparkles className="size-3" aria-hidden />
              )}
              Apply all
            </Button>
          </div>
          {suggestions.map((s) => (
            <Alert key={`${s.symbol}-${s.kind}`} className="border-border/60 bg-muted/20 py-2">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <AlertDescription className="text-xs m-0 flex-1">{s.message}</AlertDescription>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="h-7 shrink-0 text-xs"
                  disabled={Boolean(applying)}
                  onClick={() => handleApply({
                    kinds: [s.kind],
                    label: `${s.symbol}-${s.kind}`,
                  })}
                >
                  {applying === `${s.symbol}-${s.kind}` ? (
                    <Loader2 className="size-3 animate-spin" aria-hidden />
                  ) : (
                    'Apply'
                  )}
                </Button>
              </div>
            </Alert>
          ))}
        </div>
      )}

      {Object.keys(symbolThresholds).length > 0 && (
        <section>
          <header className="mb-1.5 flex items-center gap-2">
            <span className="text-xs font-medium">Per-symbol thresholds</span>
            <Badge variant="secondary" className="text-xs">{Object.keys(symbolThresholds).length}</Badge>
          </header>
          <div className="flex flex-col gap-1">
            {Object.entries(symbolThresholds).map(([sym, row]) => (
              <div
                key={sym}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border/50 px-2 py-1.5 text-xs"
              >
                <span className="font-medium">{sym}</span>
                <span className="num-mono text-muted-foreground">
                  n={row.sample_size} · win {pct(row.win_rate)} · Wilson {pct(row.wilson_lower)}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <header className="mb-1.5 text-xs font-medium">Setup buckets</header>
        <CalibrationTable
          buckets={calibration?.buckets}
          emptyLabel="Not enough closed trades with insight context yet."
        />
      </section>

      {liveRejects?.total > 0 && (
        <FilterRejectsDashboard
          rejects={liveRejects.by_bucket}
          total={liveRejects.total}
          title="Live filter rejects"
          hint="Signals blocked at runtime by CHART_AGENT filters and calibration gate (from bot logs)."
        />
      )}

      {backtestRejects?.total > 0 && (
        <FilterRejectsDashboard
          rejects={backtestRejects.by_bucket}
          total={backtestRejects.total}
          title="Backtest filter rejects"
          hint={`Aggregated from ${backtestRejects.runs_aggregated ?? 0} recent backtest/optimizer runs.`}
        />
      )}
    </div>
  );
}
