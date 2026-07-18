/**
 * Horizontal bar chart of ML feature importance rankings.
 *
 * @param {{
 *   features?: Array<{ name: string; importance: number; category?: 'price' | 'volume' | 'indicator' | 'sentiment' }>;
 *   maxBars?: number;
 *   compact?: boolean;
 *   className?: string;
 * }} props
 */
import { cn } from '@/lib/utils';

const CATEGORY_CLASS = {
  price: 'feature-importance__bar--price',
  volume: 'feature-importance__bar--volume',
  indicator: 'feature-importance__bar--indicator',
  sentiment: 'feature-importance__bar--sentiment',
};

export default function FeatureImportanceChart({
  features = [],
  maxBars = 10,
  compact = false,
  className,
}) {
  const rows = [...(features || [])]
    .filter((f) => f && f.name != null && Number.isFinite(Number(f.importance)))
    .sort((a, b) => Number(b.importance) - Number(a.importance))
    .slice(0, maxBars);

  if (!rows.length) {
    return (
      <p className={cn('feature-importance feature-importance--empty text-xs text-muted-foreground', className)}>
        No feature importance data.
      </p>
    );
  }

  const max = Math.max(...rows.map((r) => Number(r.importance)), 1e-9);

  return (
    <div
      className={cn(
        'feature-importance',
        compact && 'feature-importance--compact',
        className,
      )}
      role="img"
      aria-label="Feature importance ranking"
    >
      <ul className="feature-importance__list">
        {rows.map((f) => {
          const pct = Math.max(2, (Number(f.importance) / max) * 100);
          const cat = f.category || 'indicator';
          return (
            <li key={f.name} className="feature-importance__row">
              <span className="feature-importance__label" title={f.name}>
                {f.name}
              </span>
              <div className="feature-importance__track">
                <div
                  className={cn('feature-importance__bar', CATEGORY_CLASS[cat] || CATEGORY_CLASS.indicator)}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span className="feature-importance__value num-mono">
                {Number(f.importance).toFixed(compact ? 2 : 3)}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
