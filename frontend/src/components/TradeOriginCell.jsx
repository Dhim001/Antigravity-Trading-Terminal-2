import React from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import StrategyBadge from './StrategyBadge';

const CATEGORY_VARIANT = {
  manual: 'outline',
  bot_signal: 'secondary',
  bot_close: 'secondary',
  bot_risk: 'outline',
  bot_unknown: 'secondary',
};

const RISK_CLASS = 'border-trading-warn/45 text-trading-warn';

/**
 * Two-line origin cell for the transaction history blotter.
 */
export default function TradeOriginCell({ detail, className }) {
  if (!detail) return <span className="text-muted-foreground">—</span>;

  if (detail.kind === 'manual') {
    return (
      <div className={cn('trade-origin', className)}>
        <Badge variant="outline" className="trade-origin__badge text-[0.58rem]">
          Manual
        </Badge>
        <span className="trade-origin__sub">{detail.sublabel}</span>
      </div>
    );
  }

  const isRisk = detail.category === 'bot_risk';

  return (
    <div className={cn('trade-origin', className)}>
      <div className="trade-origin__primary">
        {detail.strategy ? (
          <StrategyBadge strategy={detail.strategy} compact className="trade-origin__strategy" />
        ) : (
          <Badge
            variant={CATEGORY_VARIANT[detail.category] || 'secondary'}
            className="trade-origin__badge text-[0.58rem]"
          >
            {detail.label}
          </Badge>
        )}
        <Badge
          variant={isRisk ? 'outline' : 'secondary'}
          className={cn('trade-origin__trigger text-[0.55rem]', isRisk && RISK_CLASS)}
        >
          {detail.trigger}
        </Badge>
      </div>
      <span className="trade-origin__sub num-mono" title={detail.botId || undefined}>
        {detail.sublabel}
      </span>
    </div>
  );
}
