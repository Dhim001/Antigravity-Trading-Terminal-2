import { cn } from '@/lib/utils';
import { getStrategyMeta } from '@/config/strategies';

/**
 * Compact strategy label with tinted icon — for bot table and drawer headers.
 */
export default function StrategyBadge({ strategy, compact = false, className }) {
  const meta = getStrategyMeta(strategy);
  const Icon = meta.icon;

  return (
    <span
      className={cn('strategy-badge', compact && 'strategy-badge--compact', className)}
      title={compact ? undefined : meta.tagline}
    >
      <span
        className="strategy-badge__icon"
        style={{ '--strategy-color': meta.color }}
        aria-hidden
      >
        <Icon size={compact ? 11 : 12} strokeWidth={2} />
      </span>
      <span className="strategy-badge__label">
        {compact ? meta.shortLabel : meta.label}
      </span>
    </span>
  );
}
