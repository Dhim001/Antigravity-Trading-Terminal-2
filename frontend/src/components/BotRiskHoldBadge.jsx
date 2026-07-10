import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import {
  riskHoldBadgeLabel,
  useEffectiveRiskHold,
} from '../lib/botRiskHold';

/**
 * Persistent risk-hold indicator for loss-streak cooloff / streak limit.
 * Pass remainingSec from a parent useEffectiveRiskHold to avoid a second timer.
 * @param {{ hold?: import('../lib/botRiskHold').BotRiskHold | null, className?: string, compact?: boolean, remainingSec?: number | null }} props
 */
export default function BotRiskHoldBadge({
  hold,
  className,
  compact = false,
  remainingSec = null,
}) {
  const controlled = remainingSec != null;
  const local = useEffectiveRiskHold(controlled ? null : hold);
  const active = controlled
    ? (hold?.kind === 'cooloff' && remainingSec <= 0 ? null : hold)
    : local.hold;
  const remaining = controlled ? remainingSec : local.remaining;
  const label = riskHoldBadgeLabel(active, remaining);

  if (!label) return null;

  const isCooloff = active?.kind === 'cooloff';

  return (
    <Badge
      variant={isCooloff ? 'outline' : 'secondary'}
      className={cn(
        'algo-bot-risk-hold',
        isCooloff && 'algo-bot-risk-hold--cooloff',
        active?.kind === 'streak_limit' && 'algo-bot-risk-hold--streak',
        active?.kind === 'drawdown' && 'algo-bot-risk-hold--drawdown',
        compact && 'algo-bot-risk-hold--compact',
        className,
      )}
      title={active?.reason || label}
    >
      {label}
    </Badge>
  );
}
