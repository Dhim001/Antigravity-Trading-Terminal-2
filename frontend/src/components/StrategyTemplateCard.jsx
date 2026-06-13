import { cn } from '@/lib/utils';
import { getStrategyMeta } from '@/config/strategies';

/**
 * Deploy-panel strategy template card with icon, tagline, and allocation chips.
 */
export default function StrategyTemplateCard({ template, active, onSelect }) {
  const meta = getStrategyMeta(template.strategy);
  const Icon = meta.icon;
  const sl = template.config?.trailing_stop_percent ?? 0;

  return (
    <button
      type="button"
      onClick={() => onSelect(template)}
      className={cn('algo-template-btn', active && 'algo-template-btn--active')}
      title={meta.tagline}
    >
      <span
        className="algo-template-btn__icon"
        style={{ '--strategy-color': meta.color }}
        aria-hidden
      >
        <Icon size={16} strokeWidth={2} />
      </span>
      <span className="algo-template-btn__body">
        <span className="algo-template-btn__name">{template.name}</span>
        <span className="algo-template-btn__tagline">{meta.tagline}</span>
        <span className="algo-template-btn__chips">
          <span className="algo-template-chip">${template.allocation.toLocaleString()}</span>
          <span className="algo-template-chip">SL {sl}%</span>
        </span>
      </span>
    </button>
  );
}
