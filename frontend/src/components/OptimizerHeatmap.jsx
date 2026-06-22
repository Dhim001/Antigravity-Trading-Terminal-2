/**
 * 2D heatmap — two most-varied sweep params vs objective metric.
 */
import React, { useMemo } from 'react';
import { cn } from '@/lib/utils';

const OBJECTIVE_LABELS = {
  total_pnl: 'PnL',
  sharpe_ratio: 'Sharpe',
  profit_factor: 'PF',
};

function metricValue(row, objective) {
  const summary = row?.summary ?? {};
  if (objective === 'sharpe_ratio') return summary.sharpe_ratio;
  if (objective === 'profit_factor') return summary.profit_factor;
  return row?.total_pnl ?? summary.total_pnl;
}

function findTopSweepAxes(results, paramDefs) {
  const counts = new Map();
  for (const row of results ?? []) {
    const cfg = row?.config ?? {};
    for (const [key, val] of Object.entries(cfg)) {
      if (val == null || key === 'sim_mode') continue;
      if (!counts.has(key)) counts.set(key, new Set());
      counts.get(key).add(JSON.stringify(val));
    }
  }
  const ranked = [...counts.entries()]
    .filter(([, vals]) => vals.size > 1)
    .sort((a, b) => b[1].size - a[1].size);
  if (ranked.length < 2) return null;
  const [xKey, xVals] = ranked[0];
  const [yKey, yVals] = ranked[1];
  const labelFor = (key) => paramDefs.find((d) => d.key === key)?.label ?? key;
  return {
    xKey,
    yKey,
    xLabel: labelFor(xKey),
    yLabel: labelFor(yKey),
    xValues: [...xVals].map((v) => JSON.parse(v)).sort((a, b) => {
      if (typeof a === 'number' && typeof b === 'number') return a - b;
      return String(a).localeCompare(String(b));
    }),
    yValues: [...yVals].map((v) => JSON.parse(v)).sort((a, b) => {
      if (typeof a === 'number' && typeof b === 'number') return a - b;
      return String(a).localeCompare(String(b));
    }),
  };
}

function cellTone(value, min, max) {
  if (value == null || Number.isNaN(value)) return 'bg-muted/30';
  if (max === min) return 'bg-primary/20';
  const t = (value - min) / (max - min);
  if (t >= 0.66) return 'bg-trading-up/30';
  if (t >= 0.33) return 'bg-primary/15';
  return 'bg-trading-down/20';
}

export default function OptimizerHeatmap({ sweep, paramDefs = [], objective = 'total_pnl' }) {
  const axes = useMemo(
    () => findTopSweepAxes(sweep?.results, paramDefs),
    [sweep?.results, paramDefs],
  );

  const hasResults = (sweep?.results?.length ?? 0) > 0;

  const grid = useMemo(() => {
    if (!axes) return null;
    const { xKey, yKey, xValues, yValues } = axes;
    const cells = new Map();
    let min = Infinity;
    let max = -Infinity;
    for (const row of sweep?.results ?? []) {
      if (row.error) continue;
      const cfg = row.config ?? {};
      const val = metricValue(row, objective);
      if (val == null) continue;
      const num = Number(val);
      if (!Number.isNaN(num)) {
        min = Math.min(min, num);
        max = Math.max(max, num);
      }
      cells.set(`${cfg[xKey]}|${cfg[yKey]}`, { row, val: num });
    }
    if (!Number.isFinite(min)) {
      min = 0;
      max = 0;
    }
    return { cells, min, max, xValues, yValues };
  }, [axes, sweep?.results, objective]);

  if (!hasResults) return null;

  if (!axes || !grid) {
    return (
      <section className="algo-backtest-heatmap mt-3">
        <p className="algo-backtest-table-scroll__caption m-0 text-xs text-muted-foreground">
          Heatmap needs at least two varied parameters — enable more sweep axes or add value ranges.
        </p>
      </section>
    );
  }

  const metricLabel = OBJECTIVE_LABELS[objective] ?? 'Metric';

  return (
    <section className="algo-backtest-heatmap mt-3">
      <p className="algo-backtest-table-scroll__caption m-0 mb-2">
        Heatmap — {axes.yLabel} × {axes.xLabel} ({metricLabel})
      </p>
      <div className="algo-backtest-table-scroll overflow-x-auto">
        <table className="terminal-table algo-backtest-table m-0 text-xs">
          <thead>
            <tr>
              <th className="text-left">{axes.yLabel} ↓ / {axes.xLabel} →</th>
              {grid.xValues.map((xv) => (
                <th key={String(xv)} className="text-center num-mono">{String(xv)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.yValues.map((yv) => (
              <tr key={String(yv)}>
                <td className="font-medium text-muted-foreground num-mono">{String(yv)}</td>
                {grid.xValues.map((xv) => {
                  const cell = grid.cells.get(`${xv}|${yv}`);
                  const val = cell?.val;
                  return (
                    <td
                      key={`${xv}-${yv}`}
                      className={cn(
                        'text-center num-mono whitespace-nowrap px-1',
                        cellTone(val, grid.min, grid.max),
                      )}
                      title={cell?.row?.label}
                    >
                      {val == null || Number.isNaN(val)
                        ? '—'
                        : objective === 'total_pnl'
                          ? `$${val.toFixed(0)}`
                          : val.toFixed(2)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
