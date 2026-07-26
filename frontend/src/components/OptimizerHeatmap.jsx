/**
 * 2D heatmap — selectable axes among swept params vs objective metric.
 */
import React, { useMemo, useState } from 'react';
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

function listSweepAxes(results, paramDefs) {
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
  const labelFor = (key) => paramDefs.find((d) => d.key === key)?.label ?? key;
  return ranked.map(([key, vals]) => ({
    key,
    label: labelFor(key),
    values: [...vals].map((v) => JSON.parse(v)).sort((a, b) => {
      if (typeof a === 'number' && typeof b === 'number') return a - b;
      return String(a).localeCompare(String(b));
    }),
  }));
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
  const allAxes = useMemo(
    () => listSweepAxes(sweep?.results, paramDefs),
    [sweep?.results, paramDefs],
  );

  const [xKey, setXKey] = useState(null);
  const [yKey, setYKey] = useState(null);
  const [zFilterKey, setZFilterKey] = useState('');
  const [zFilterVal, setZFilterVal] = useState('');

  const resolvedX = xKey || allAxes[0]?.key;
  const resolvedY = yKey || allAxes[1]?.key || allAxes[0]?.key;
  const xAxis = allAxes.find((a) => a.key === resolvedX) || allAxes[0];
  const yAxis = allAxes.find((a) => a.key === resolvedY) || allAxes[1] || allAxes[0];
  const zAxis = zFilterKey ? allAxes.find((a) => a.key === zFilterKey) : null;

  const hasResults = (sweep?.results?.length ?? 0) > 0;

  const grid = useMemo(() => {
    if (!xAxis || !yAxis || xAxis.key === yAxis.key) return null;
    const cells = new Map();
    let min = Infinity;
    let max = -Infinity;
    for (const row of sweep?.results ?? []) {
      if (row.error) continue;
      const cfg = row.config ?? {};
      if (zAxis && zFilterVal !== '' && String(cfg[zAxis.key]) !== String(zFilterVal)) {
        continue;
      }
      const val = metricValue(row, objective);
      if (val == null) continue;
      const xv = cfg[xAxis.key];
      const yv = cfg[yAxis.key];
      if (xv == null || yv == null) continue;
      const key = `${xv}|${yv}`;
      const prev = cells.get(key);
      if (!prev || Number(val) > Number(prev.val)) {
        cells.set(key, { val: Number(val), row });
      }
      min = Math.min(min, Number(val));
      max = Math.max(max, Number(val));
    }
    return {
      xValues: xAxis.values,
      yValues: yAxis.values,
      cells,
      min: Number.isFinite(min) ? min : 0,
      max: Number.isFinite(max) ? max : 0,
    };
  }, [sweep?.results, objective, xAxis, yAxis, zAxis, zFilterVal]);

  if (!hasResults) return null;

  if (!allAxes || allAxes.length < 2 || !grid) {
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
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <p className="algo-backtest-table-scroll__caption m-0 text-xs">
          Heatmap — {yAxis.label} × {xAxis.label} ({metricLabel})
        </p>
        <label className="text-[10px] text-muted-foreground flex items-center gap-1">
          X
          <select
            className="h-6 text-[10px] bg-background border rounded px-1"
            value={resolvedX}
            onChange={(e) => setXKey(e.target.value)}
          >
            {allAxes.map((a) => (
              <option key={a.key} value={a.key}>{a.label}</option>
            ))}
          </select>
        </label>
        <label className="text-[10px] text-muted-foreground flex items-center gap-1">
          Y
          <select
            className="h-6 text-[10px] bg-background border rounded px-1"
            value={resolvedY}
            onChange={(e) => setYKey(e.target.value)}
          >
            {allAxes.map((a) => (
              <option key={a.key} value={a.key}>{a.label}</option>
            ))}
          </select>
        </label>
        {allAxes.length >= 3 && (
          <>
            <label className="text-[10px] text-muted-foreground flex items-center gap-1">
              Slice
              <select
                className="h-6 text-[10px] bg-background border rounded px-1"
                value={zFilterKey}
                onChange={(e) => {
                  setZFilterKey(e.target.value);
                  setZFilterVal('');
                }}
              >
                <option value="">(none)</option>
                {allAxes
                  .filter((a) => a.key !== resolvedX && a.key !== resolvedY)
                  .map((a) => (
                    <option key={a.key} value={a.key}>{a.label}</option>
                  ))}
              </select>
            </label>
            {zAxis && (
              <label className="text-[10px] text-muted-foreground flex items-center gap-1">
                =
                <select
                  className="h-6 text-[10px] bg-background border rounded px-1"
                  value={zFilterVal}
                  onChange={(e) => setZFilterVal(e.target.value)}
                >
                  <option value="">all</option>
                  {zAxis.values.map((v) => (
                    <option key={String(v)} value={String(v)}>{String(v)}</option>
                  ))}
                </select>
              </label>
            )}
          </>
        )}
      </div>
      <div className="algo-backtest-table-scroll overflow-x-auto">
        <table className="terminal-table algo-backtest-table m-0 text-xs">
          <thead>
            <tr>
              <th className="text-left">{yAxis.label} ↓ / {xAxis.label} →</th>
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
                  const tip = cell?.row
                    ? `${cell.row.label || ''} · ${JSON.stringify(cell.row.config || {})}`
                    : '';
                  return (
                    <td
                      key={`${xv}-${yv}`}
                      className={cn(
                        'text-center num-mono whitespace-nowrap px-1',
                        cellTone(val, grid.min, grid.max),
                      )}
                      title={tip}
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
