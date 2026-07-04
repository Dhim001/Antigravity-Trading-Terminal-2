import React, { useMemo, useState } from 'react';
import BacktestMiniChart from './BacktestMiniChart';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

function indexBenchmarkCurve(benchmarkCurve, equityCurve) {
  if (!benchmarkCurve?.length || !equityCurve?.length) return [];
  return equityCurve.map((pt, i) => {
    let best = benchmarkCurve[0];
    let bestDiff = Math.abs(best.time - pt.time);
    for (const row of benchmarkCurve) {
      const diff = Math.abs(row.time - pt.time);
      if (diff < bestDiff) {
        best = row;
        bestDiff = diff;
      }
    }
    return best?.equity ?? null;
  });
}

export default function BacktestEquityChart({
  equityCurve,
  drawdownCurve,
  totalPnl,
  trades,
  benchmarkOverlays,
  className,
  variant = 'compact',
}) {
  const [showSpy, setShowSpy] = useState(true);
  const [showBtc, setShowBtc] = useState(true);
  const [showSymbolBh, setShowSymbolBh] = useState(true);

  const overlays = useMemo(() => {
    const out = {};
    if (showSymbolBh && benchmarkOverlays?.symbol_bh_curve?.length) {
      out.symbolBh = indexBenchmarkCurve(benchmarkOverlays.symbol_bh_curve, equityCurve);
    }
    if (showSpy && benchmarkOverlays?.SPY?.curve?.length) {
      out.spy = indexBenchmarkCurve(benchmarkOverlays.SPY.curve, equityCurve);
    }
    if (showBtc && benchmarkOverlays?.BTC?.curve?.length) {
      out.btc = indexBenchmarkCurve(benchmarkOverlays.BTC.curve, equityCurve);
    }
    return out;
  }, [benchmarkOverlays, equityCurve, showSpy, showBtc, showSymbolBh]);

  const hasBench = Boolean(
    benchmarkOverlays?.SPY?.curve?.length
    || benchmarkOverlays?.BTC?.curve?.length
    || benchmarkOverlays?.symbol_bh_curve?.length,
  );

  const isLab = variant === 'lab';

  return (
    <div className={cn('backtest-equity-chart-wrap', isLab && 'backtest-equity-chart-wrap--lab')}>
      {isLab && (
        <div className="backtest-equity-chart-wrap__head">
          <span className="backtest-equity-chart-wrap__title">Equity &amp; drawdown</span>
        </div>
      )}
      {hasBench && (
        <div className="flex flex-wrap items-center gap-3 px-0.5 text-[0.58rem] text-muted-foreground">
          {benchmarkOverlays?.symbol_bh_curve?.length > 0 && (
            <label className="flex items-center gap-1.5 cursor-pointer">
              <Checkbox checked={showSymbolBh} onCheckedChange={(v) => setShowSymbolBh(Boolean(v))} className="size-3" />
              <Label className="text-[0.58rem] font-normal cursor-pointer">Symbol B&H</Label>
            </label>
          )}
          {benchmarkOverlays?.SPY?.curve?.length > 0 && (
            <label className="flex items-center gap-1.5 cursor-pointer">
              <Checkbox checked={showSpy} onCheckedChange={(v) => setShowSpy(Boolean(v))} className="size-3" />
              <Label className="text-[0.58rem] font-normal cursor-pointer">SPY</Label>
            </label>
          )}
          {benchmarkOverlays?.BTC?.curve?.length > 0 && (
            <label className="flex items-center gap-1.5 cursor-pointer">
              <Checkbox checked={showBtc} onCheckedChange={(v) => setShowBtc(Boolean(v))} className="size-3" />
              <Label className="text-[0.58rem] font-normal cursor-pointer">BTC</Label>
            </label>
          )}
        </div>
      )}
      <BacktestMiniChart
        equityCurve={equityCurve}
        drawdownCurve={drawdownCurve}
        totalPnl={totalPnl}
        trades={trades}
        benchmarkOverlays={overlays}
        className={className}
        variant={variant}
      />
    </div>
  );
}
