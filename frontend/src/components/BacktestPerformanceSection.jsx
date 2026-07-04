/**
 * Performance tab — long/short split, hold stats, largest win/loss.
 */
import { StatCard } from '@/components/StatCard';

export default function BacktestPerformanceSection({ summary, results }) {
  const s = summary ?? {};
  const pnlTone = (s.total_pnl ?? results?.total_pnl ?? 0) >= 0 ? 'up' : 'down';

  return (
    <section className="algo-backtest-lab__section">
      <p className="algo-backtest-table-scroll__caption mb-2">Trade performance breakdown</p>
      <div className="algo-backtest-stat-grid">
        <StatCard
          label="Long trades"
          value={String(s.long_trades ?? 0)}
          sub={s.long_pnl != null ? `$${Number(s.long_pnl).toFixed(2)}` : undefined}
          tone={(s.long_pnl ?? 0) >= 0 ? 'up' : 'down'}
        />
        <StatCard
          label="Short trades"
          value={String(s.short_trades ?? 0)}
          sub={s.short_pnl != null ? `$${Number(s.short_pnl).toFixed(2)}` : undefined}
          tone={(s.short_pnl ?? 0) >= 0 ? 'up' : 'down'}
        />
        <StatCard
          label="Avg hold"
          value={s.avg_hold_hours ? `${Number(s.avg_hold_hours).toFixed(1)}h` : '—'}
        />
        <StatCard
          label="Time in market"
          value={s.time_in_market_pct != null ? `${Number(s.time_in_market_pct).toFixed(1)}%` : '—'}
        />
        <StatCard
          label="Largest win"
          value={`$${Number(s.largest_win ?? 0).toFixed(2)}`}
          tone="up"
        />
        <StatCard
          label="Largest loss"
          value={`$${Number(s.largest_loss ?? 0).toFixed(2)}`}
          tone="down"
        />
        <StatCard label="Avg win" value={`$${Number(s.avg_win ?? 0).toFixed(2)}`} tone="up" />
        <StatCard label="Avg loss" value={`$${Number(s.avg_loss ?? 0).toFixed(2)}`} tone="down" />
        <StatCard label="Expectancy" value={`$${Number(s.expectancy ?? 0).toFixed(2)}`} tone={pnlTone} />
        <StatCard
          label="Sortino"
          value={s.sortino_ratio != null ? Number(s.sortino_ratio).toFixed(2) : '—'}
        />
        <StatCard
          label="Calmar"
          value={s.calmar_ratio != null ? Number(s.calmar_ratio).toFixed(2) : '—'}
        />
        <StatCard
          label="Blocked entries"
          value={String(s.blocked_entries ?? 0)}
          sub="Risk gate"
        />
      </div>
    </section>
  );
}
