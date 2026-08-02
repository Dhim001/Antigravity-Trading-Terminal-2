/**
 * Colored model-health badge — click to retrain.
 */
import { RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { assessModelHealth } from '@/lib/modelHealth';

export function ModelHealthBadge({ bot, status, onClick, compact = false }) {
  const health = assessModelHealth(bot, status);
  const clickable = typeof onClick === 'function';

  return (
    <button
      type="button"
      className={cn(
        'model-health-badge',
        `model-health-badge--${health.level}`,
        compact && 'model-health-badge--compact',
        !clickable && 'model-health-badge--static',
      )}
      style={{ '--mh-color': health.color }}
      title={health.tooltip}
      disabled={!clickable}
      onClick={(e) => {
        e.stopPropagation();
        onClick?.(e);
      }}
      aria-label={`${health.label}: ${health.tooltip}`}
    >
      <span className="model-health-badge__dot" aria-hidden />
      {!compact && <span className="model-health-badge__label">{health.label}</span>}
      {clickable && !compact && (
        <RefreshCw size={10} className="model-health-badge__icon" aria-hidden />
      )}
    </button>
  );
}

export default ModelHealthBadge;
