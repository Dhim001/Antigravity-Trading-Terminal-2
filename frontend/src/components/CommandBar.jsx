/**
 * CommandBar — merged aux band, portfolio summary, and market strip (UX-4).
 */
import { memo } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
import { selectActiveSymbolTick } from '../store/selectors';
import MarketOverviewStrip from './MarketOverviewStrip';
import PortfolioSummaryBar from './PortfolioSummaryBar';
import StaleDataBanner from './StaleDataBanner';
import { cn } from '@/lib/utils';
import { formatPrice } from '@/lib/formatPrice';

function CommandBar() {
  const { symbol, price, change } = useStore(useShallow(selectActiveSymbolTick));
  const layoutMode = useSettingsStore((s) => s.settings.workspace?.layoutMode || 'trade');

  const isUp = (change ?? 0) >= 0;

  return (
    <div className="command-bar" data-layout-mode={layoutMode}>
      <div className="command-bar__lead">
        <div className="command-bar__symbol">
          <span className="command-bar__symbol-name">{symbol}</span>
          {price != null && (
            <span className="command-bar__symbol-price num-mono">
              {formatPrice(symbol, price)}
            </span>
          )}
          {change != null && (
            <span className={cn(
              'command-bar__symbol-change num-mono',
              isUp ? 'text-trading-up' : 'text-trading-down',
            )}>
              {isUp ? '+' : ''}{change.toFixed(2)}%
            </span>
          )}
        </div>
        <StaleDataBanner inline />
      </div>
      <div className="command-bar__strip">
        <MarketOverviewStrip compact />
      </div>
      <div className="command-bar__metrics">
        <PortfolioSummaryBar compact />
      </div>
    </div>
  );
}

export default memo(CommandBar);
