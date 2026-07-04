import React from 'react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

const REGIME_TONE = {
  elevated: 'text-trading-warn',
  compressed: 'text-trading-accent',
  normal: 'text-muted-foreground',
  unknown: 'text-muted-foreground',
};

export default function BacktestRegimeSection({ regime, benchmarkOverlays }) {
  const r = regime ?? {};
  const tone = REGIME_TONE[r.dominant_regime] ?? REGIME_TONE.unknown;
  const spy = benchmarkOverlays?.SPY;
  const btc = benchmarkOverlays?.BTC;
  const bh = benchmarkOverlays?.symbol_bh;

  if (!r.dominant_regime && !spy && !btc && !bh) return null;

  return (
    <section className="algo-backtest-lab__section mb-3">
      <p className="algo-backtest-table-scroll__caption mb-1.5">Market context</p>
      <div className="flex flex-col gap-2 text-[0.62rem]">
        <div className="flex flex-wrap items-center gap-2">
        {r.dominant_regime && (
          <Badge variant="outline" className={cn('capitalize num-mono', tone)}>
            {r.label || r.dominant_regime}
          </Badge>
        )}
        {spy?.return_pct != null && (
          <span className="text-muted-foreground">
            SPY B&H <span className="num-mono">{Number(spy.return_pct).toFixed(1)}%</span>
          </span>
        )}
        {btc?.return_pct != null && (
          <span className="text-muted-foreground">
            BTC B&H <span className="num-mono">{Number(btc.return_pct).toFixed(1)}%</span>
          </span>
        )}
        </div>
        {r.breakdown_pct && (
          <div className="flex flex-col gap-1">
            {Object.entries(r.breakdown_pct).map(([key, pct]) => (
              <div key={key} className="backtest-regime-bar" title={`${key} volatility ${pct}% of bars`}>
                <span className="backtest-regime-bar__label capitalize">{key}</span>
                <span className="backtest-regime-bar__track">
                  <span
                    className={cn('backtest-regime-bar__fill', `backtest-regime-bar__fill--${key}`)}
                    style={{ width: `${Math.min(100, pct)}%` }}
                  />
                </span>
                <span className="backtest-regime-bar__pct num-mono">{pct}%</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
