import { useCallback, useEffect, useMemo, useState } from 'react';
import { Gauge, Loader2, TrendingUp } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useStore } from '../store/useStore';
import {
  applyCostSuggestion,
  fetchCostSuggestions,
  fetchExecutionQuality,
} from '../api/endpoints';

/**
 * ExecutionQualityPanel — TCA dashboard (EXECUTION_RISK_INTELLIGENCE_PLAN Phase 2).
 * IS decomposition KPIs, daily IS trend, algo comparison, worst fills, and the
 * one-click "calibrate backtest costs from live" approval.
 */

function fmtBps(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  return `${Number(value).toFixed(1)}`;
}

function Kpi({ label, value, tone }) {
  return (
    <div className="rounded-md border border-border/60 bg-muted/30 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={cn('num-mono text-sm font-semibold', tone)}>{value}</div>
    </div>
  );
}

/** Minimal inline-SVG trend of daily avg IS (bps). No chart lib — drawer-safe. */
function IsTrend({ trend }) {
  const points = useMemo(() => {
    const rows = (trend || []).filter((r) => r.avg_is_bps != null);
    if (rows.length < 2) return null;
    const values = rows.map((r) => Number(r.avg_is_bps));
    const min = Math.min(...values, 0);
    const max = Math.max(...values, 0);
    const span = max - min || 1;
    const W = 100;
    const H = 28;
    const step = W / (rows.length - 1);
    const coords = rows.map((r, i) => {
      const x = (i * step).toFixed(2);
      const y = (H - ((Number(r.avg_is_bps) - min) / span) * H).toFixed(2);
      return `${x},${y}`;
    });
    const zeroY = (H - ((0 - min) / span) * H).toFixed(2);
    return { path: coords.join(' '), zeroY, min, max };
  }, [trend]);

  if (!points) {
    return <p className="m-0 text-xs text-muted-foreground">Not enough daily data for a trend yet.</p>;
  }
  return (
    <div>
      <svg viewBox={`0 0 100 ${28}`} className="h-16 w-full" preserveAspectRatio="none" aria-hidden>
        <line x1="0" x2="100" y1={points.zeroY} y2={points.zeroY} stroke="currentColor" strokeOpacity="0.25" strokeWidth="0.4" />
        <polyline points={points.path} fill="none" stroke="var(--color-accent, #6b9eff)" strokeWidth="1.2" />
      </svg>
      <div className="flex justify-between text-[10px] text-muted-foreground">
        <span className="num-mono">{points.min.toFixed(1)} bps</span>
        <span className="num-mono">{points.max.toFixed(1)} bps</span>
      </div>
    </div>
  );
}

export default function ExecutionQualityPanel({ botId, symbol, className }) {
  const [data, setData] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [applying, setApplying] = useState(null);

  const load = useCallback(async () => {
    try {
      const [dash, sugg] = await Promise.all([
        fetchExecutionQuality({ botId, symbol }),
        fetchCostSuggestions(),
      ]);
      setData(dash);
      setSuggestions(Array.isArray(sugg) ? sugg : []);
      setError(null);
    } catch (e) {
      setError(e.message || 'Execution quality unavailable');
    } finally {
      setLoading(false);
    }
  }, [botId, symbol]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await load();
      if (cancelled) return;
    })();
    return () => { cancelled = true; };
  }, [load]);

  const onApply = useCallback(
    async (row) => {
      setApplying(row.symbol);
      try {
        const patch = await applyCostSuggestion(row.symbol);
        useStore.getState().updateBotConfig({
          slippage_bps: patch.slippage_bps,
          latency_slippage_bps: patch.latency_slippage_bps,
        });
        toast.success(
          `Calibrated ${row.symbol}: ${patch.slippage_bps}bps slip + ${patch.latency_slippage_bps}bps latency applied to backtest config`,
        );
        setSuggestions((prev) =>
          prev.map((s) => (s.symbol === row.symbol ? { ...s, applied: true, applied_at: patch.applied_at } : s)),
        );
      } catch (e) {
        toast.error(e.message || 'Apply failed');
      } finally {
        setApplying(null);
      }
    },
    [],
  );

  if (loading) {
    return (
      <div className={cn('flex items-center gap-2 text-xs text-muted-foreground', className)}>
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Measuring execution quality…
      </div>
    );
  }
  if (error) {
    return <p className={cn('m-0 text-xs text-destructive', className)}>{error}</p>;
  }

  const kpis = data?.kpis || {};
  const n = Number(kpis.n || 0);
  if (n === 0) {
    return (
      <p className={cn('m-0 text-xs text-muted-foreground', className)}>
        No execution measurements yet — rows appear after the next filled order.
      </p>
    );
  }

  const visibleSuggestions = symbol
    ? suggestions.filter((s) => s.symbol === symbol || s.applied)
    : suggestions;

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-6">
        <Kpi label="Avg IS" value={`${fmtBps(kpis.avg_is_bps)} bps`} tone={Number(kpis.avg_is_bps) > 0 ? 'text-amber-500' : 'text-emerald-500'} />
        <Kpi label="Fills" value={String(n)} />
        <Kpi label="Delay" value={fmtBps(kpis.avg_delay_bps)} />
        <Kpi label="Spread" value={fmtBps(kpis.avg_spread_bps)} />
        <Kpi label="Impact" value={fmtBps(kpis.avg_impact_bps)} />
        <Kpi label="Opp." value={fmtBps(kpis.avg_opp_bps)} />
      </div>

      <div>
        <div className="mb-1 flex items-center gap-1.5 text-xs font-medium">
          <TrendingUp className="h-3.5 w-3.5" /> Daily IS trend
        </div>
        <IsTrend trend={data?.trend} />
      </div>

      {data?.by_algo?.length > 0 && (
        <div className="overflow-x-auto">
          <table className="terminal-table m-0 w-full text-xs">
            <thead>
              <tr>
                <th>Algo</th>
                <th>Side</th>
                <th className="text-right">N</th>
                <th className="text-right">IS</th>
                <th className="text-right">Delay</th>
                <th className="text-right">Spread</th>
                <th className="text-right">Impact</th>
              </tr>
            </thead>
            <tbody>
              {data.by_algo.map((row, i) => (
                <tr key={`${row.exec_algo}-${row.side}-${i}`}>
                  <td>{row.exec_algo || 'single'}</td>
                  <td>{row.side}</td>
                  <td className="num-mono text-right">{row.n}</td>
                  <td className={cn('num-mono text-right', Number(row.avg_is_bps) > 0 ? 'text-amber-500' : 'text-emerald-500')}>
                    {fmtBps(row.avg_is_bps)}
                  </td>
                  <td className="num-mono text-right">{fmtBps(row.avg_delay_bps)}</td>
                  <td className="num-mono text-right">{fmtBps(row.avg_spread_bps)}</td>
                  <td className="num-mono text-right">{fmtBps(row.avg_impact_bps)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data?.worst_fills?.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-medium">Worst fills</div>
          <div className="flex flex-col gap-1">
            {data.worst_fills.slice(0, 5).map((f, i) => (
              <div key={`${f.order_id}-${i}`} className="flex items-center justify-between rounded-md border border-border/50 px-2 py-1 text-[11px]">
                <span className="text-muted-foreground">
                  {f.symbol} {f.side} · {f.exec_algo || 'single'}
                </span>
                <span className="num-mono font-medium text-amber-500">{fmtBps(f.is_bps)} bps</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {visibleSuggestions.length > 0 && (
        <div>
          <div className="mb-1 flex items-center gap-1.5 text-xs font-medium">
            <Gauge className="h-3.5 w-3.5" /> Backtest cost calibration
          </div>
          <div className="flex flex-col gap-1">
            {visibleSuggestions.map((row) => (
              <div key={row.symbol} className="flex items-center justify-between gap-2 rounded-md border border-border/50 px-2 py-1 text-[11px]">
                <span>
                  <span className="font-medium">{row.symbol}</span>{' '}
                  <span className="text-muted-foreground">
                    measured {fmtBps(row.measured_exec_bps)} bps exec / {fmtBps(row.measured_delay_bps)} bps delay ({row.sample_size} fills)
                  </span>
                </span>
                {row.insufficient_data ? (
                  <Badge variant="outline" className="text-[10px]">collecting</Badge>
                ) : row.applied ? (
                  <Badge variant="secondary" className="text-[10px]">
                    applied {fmtBps(row.suggested_slippage_bps)}+{fmtBps(row.suggested_latency_bps)}bps
                  </Badge>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-6 px-2 text-[11px]"
                    disabled={applying === row.symbol}
                    onClick={() => onApply(row)}
                  >
                    {applying === row.symbol ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <>Apply {fmtBps(row.suggested_slippage_bps)}+{fmtBps(row.suggested_latency_bps)}bps</>
                    )}
                  </Button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
