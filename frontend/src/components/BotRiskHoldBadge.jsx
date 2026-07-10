import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import {
  riskHoldBadgeLabel,
  useRiskHoldRemaining,
} from '../lib/botRiskHold';

/**
 * Persistent risk-hold indicator for loss-streak cooloff / streak limit.
 * @param {{ hold?: import('../lib/botRiskHold').BotRiskHold | null, className?: string, compact?: boolean }} props
 */
export default function BotRiskHoldBadge({ hold, className, compact = false }) {
  const remaining = useRiskHoldRemaining(hold?.kind === 'cooloff' ? hold : null);
  const label = riskHoldBadgeLabel(hold, remaining);

  if (!label) return null;

  const isCooloff = hold?.kind === 'cooloff';

  return (
    <Badge
      variant={isCooloff ? 'outline' : 'secondary'}
      className={cn(
        'algo-bot-risk-hold',
        isCooloff && 'algo-bot-risk-hold--cooloff',
        hold?.kind === 'streak_limit' && 'algo-bot-risk-hold--streak',
        compact && 'algo-bot-risk-hold--compact',
        className,
      )}
      title={hold?.reason || label}
    >
      {label}
    </Badge>
  );
}
