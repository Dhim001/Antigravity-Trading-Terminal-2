/**
 * Tiny inline equity sparkline for portfolio symbol rows.
 */
import { cn } from '@/lib/utils';

export default function BacktestSparkline({ values = [], tone = 'neutral', className }) {
  if (!values?.length) return <span className="text-muted-foreground text-[0.55rem]">—</span>;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 1e-9);
  const w = 48;
  const h = 16;
  const step = values.length > 1 ? w / (values.length - 1) : w;

  const points = values.map((v, i) => {
    const x = i * step;
    const y = h - ((v - min) / span) * (h - 2) - 1;
    return `${x},${y}`;
  }).join(' ');

  const up = values[values.length - 1] >= values[0];

  return (
    <svg
      className={cn('backtest-sparkline', `backtest-sparkline--${tone}`, up && 'backtest-sparkline--up', className)}
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      aria-hidden
    >
      <polyline points={points} fill="none" strokeWidth="1.5" />
    </svg>
  );
}
