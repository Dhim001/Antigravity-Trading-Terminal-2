/**
 * Confidence calibration: predicted bucket vs actual win rate.
 *
 * @param {{
 *   calibration?: Array<{ bucket: string; predicted: number; actual: number; count: number }>;
 *   compact?: boolean;
 *   className?: string;
 * }} props
 */
import { cn } from '@/lib/utils';

export default function ConfidenceCalibrationChart({
  calibration = [],
  compact = false,
  className,
}) {
  const points = (calibration || []).filter(
    (p) => p && Number.isFinite(Number(p.predicted)) && Number.isFinite(Number(p.actual)),
  );

  if (!points.length) {
    return (
      <p className={cn('calib-chart calib-chart--empty text-xs text-muted-foreground', className)}>
        No calibration data.
      </p>
    );
  }

  const w = compact ? 200 : 280;
  const h = compact ? 120 : 160;
  const pad = 28;
  const plotW = w - pad * 2;
  const plotH = h - pad * 2;

  const toX = (v) => pad + Math.min(1, Math.max(0, Number(v))) * plotW;
  const toY = (v) => pad + (1 - Math.min(1, Math.max(0, Number(v)))) * plotH;

  const maxCount = Math.max(...points.map((p) => Number(p.count) || 0), 1);

  return (
    <div className={cn('calib-chart', compact && 'calib-chart--compact', className)}>
      <svg
        viewBox={`0 0 ${w} ${h}`}
        className="calib-chart__svg"
        role="img"
        aria-label="Confidence calibration chart"
      >
        {/* diagonal reference */}
        <line
          x1={toX(0)}
          y1={toY(0)}
          x2={toX(1)}
          y2={toY(1)}
          className="calib-chart__diagonal"
        />
        {/* axes */}
        <line x1={pad} y1={pad} x2={pad} y2={h - pad} className="calib-chart__axis" />
        <line x1={pad} y1={h - pad} x2={w - pad} y2={h - pad} className="calib-chart__axis" />
        <text x={w / 2} y={h - 4} className="calib-chart__axis-label" textAnchor="middle">
          Predicted
        </text>
        <text
          x={10}
          y={h / 2}
          className="calib-chart__axis-label"
          textAnchor="middle"
          transform={`rotate(-90 10 ${h / 2})`}
        >
          Actual win%
        </text>
        {points.map((p, i) => {
          const cx = toX(p.predicted);
          const cy = toY(p.actual);
          const r = 3 + (Number(p.count) / maxCount) * 5;
          const over = Number(p.predicted) > Number(p.actual) + 0.05;
          const under = Number(p.actual) > Number(p.predicted) + 0.05;
          return (
            <g key={p.bucket || i}>
              <circle
                cx={cx}
                cy={cy}
                r={r}
                className={cn(
                  'calib-chart__point',
                  over && 'calib-chart__point--over',
                  under && 'calib-chart__point--under',
                )}
              >
                <title>
                  {p.bucket}: pred {(Number(p.predicted) * 100).toFixed(0)}%
                  {' → '}
                  actual {(Number(p.actual) * 100).toFixed(0)}%
                  {' (n='}
                  {p.count}
                  {')'}
                </title>
              </circle>
            </g>
          );
        })}
      </svg>
      <p className="calib-chart__legend text-[0.5rem] text-muted-foreground">
        Diagonal = perfect · above = underconfident · below = overconfident
      </p>
    </div>
  );
}
