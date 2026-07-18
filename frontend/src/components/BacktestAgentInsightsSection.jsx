/**
 * Agentic bot insight blocks for Backtest Lab Results (Phase 2).
 */
import { StatCard } from '@/components/StatCard';
import ConfidenceCalibrationChart from './ConfidenceCalibrationChart';
import SignalGateFunnel from './SignalGateFunnel';
import { cn } from '@/lib/utils';

function fmtPct(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const pct = n <= 1 && n >= 0 ? n * 100 : n;
  return `${pct.toFixed(1)}%`;
}

function RegimeCards({ regimes }) {
  const rows = (regimes || []).filter((r) => r && r.regime);
  if (!rows.length) return null;
  return (
    <div className="algo-backtest-stat-grid algo-backtest-stat-grid--compact">
      {rows.map((r) => (
        <StatCard
          key={r.regime}
          label={String(r.regime)}
          value={fmtPct(r.win_rate)}
          sub={`${r.trades ?? 0} trades · Sharpe ${r.sharpe != null ? Number(r.sharpe).toFixed(2) : '—'}`}
          tone={Number(r.avg_pnl) >= 0 ? 'up' : 'down'}
          tooltip={`Avg PnL ${r.avg_pnl != null ? Number(r.avg_pnl).toFixed(2) : '—'}; blocked ${r.signals_blocked ?? '—'}`}
        />
      ))}
    </div>
  );
}

export default function BacktestAgentInsightsSection({ results, compact = false }) {
  const am = results?.agent_metrics;
  const summary = results?.summary;

  // Fall back to filter-reject aggregates when agent_metrics is absent
  const generated = am?.signals_generated
    ?? summary?.filter_rejects_total
    ?? null;
  const filtered = am?.signals_filtered
    ?? summary?.filter_rejects_total
    ?? null;
  const executed = am?.signals_executed
    ?? summary?.total_trades
    ?? null;
  const successRate = am?.success_rate
    ?? (executed != null && generated != null && generated > 0
      ? executed / generated
      : summary?.win_rate != null
        ? Number(summary.win_rate) / 100
        : null);

  const hasRich = Boolean(
    am?.gate_funnel?.length
    || am?.confidence_calibration?.length
    || am?.regime_performance?.length
    || am?.signals_generated != null,
  );

  return (
    <section className={cn('algo-backtest-lab__section agent-insights', compact && 'agent-insights--compact')}>
      <p className="algo-backtest-table-scroll__caption mb-1.5">Agent decision breakdown</p>
      <div className="algo-backtest-stat-grid algo-backtest-stat-grid--compact mb-2">
        <StatCard label="Signals" value={generated != null ? String(generated) : '—'} tone="accent" />
        <StatCard label="Filtered" value={filtered != null ? String(filtered) : '—'} />
        <StatCard label="Executed" value={executed != null ? String(executed) : '—'} />
        <StatCard label="Success" value={fmtPct(successRate)} tone={Number(successRate) >= 0.5 ? 'up' : 'down'} />
      </div>

      {!hasRich && (
        <p className="text-xs text-muted-foreground mb-2">
          Full gate funnel and calibration charts appear when the run includes{' '}
          <code className="mx-0.5">agent_metrics</code>.
        </p>
      )}

      <div className={cn('agent-insights__viz-grid', compact && 'agent-insights__viz-grid--compact')}>
        {am?.gate_funnel?.length > 0 && (
          <div>
            <p className="algo-backtest-table-scroll__caption mb-1">Signal gate funnel</p>
            <SignalGateFunnel stages={am.gate_funnel} />
          </div>
        )}
        {am?.confidence_calibration?.length > 0 && (
          <div>
            <p className="algo-backtest-table-scroll__caption mb-1">Confidence calibration</p>
            <ConfidenceCalibrationChart
              calibration={am.confidence_calibration}
              compact={compact}
            />
          </div>
        )}
        {am?.regime_performance?.length > 0 && (
          <div>
            <p className="algo-backtest-table-scroll__caption mb-1">Regime performance</p>
            <RegimeCards regimes={am.regime_performance} />
          </div>
        )}
      </div>
    </section>
  );
}
