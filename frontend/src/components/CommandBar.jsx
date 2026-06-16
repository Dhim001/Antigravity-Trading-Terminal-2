/**
 * CommandBar — merged aux band, portfolio summary, and market strip (UX-4).
 */
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
import MarketOverviewStrip from './MarketOverviewStrip';
import PortfolioSummaryBar from './PortfolioSummaryBar';
import StaleDataBanner from './StaleDataBanner';
import { cn } from '@/lib/utils';

export default function CommandBar() {
  const activeSymbol = useStore((s) => s.activeSymbol);
  const tickerData = useStore((s) => s.tickerData);
  const layoutMode = useSettingsStore((s) => s.settings.workspace?.layoutMode || 'trade');

  const tick = tickerData[activeSymbol];
  const change = tick?.change_24h ?? 0;
  const isUp = change >= 0;

  return (
    <div className="command-bar" data-layout-mode={layoutMode}>
      <div className="command-bar__lead">
        <div className="command-bar__symbol">
          <span className="command-bar__symbol-name">{activeSymbol}</span>
          {tick?.price != null && (
            <span className="command-bar__symbol-price num-mono">
              {tick.price.toLocaleString(undefined, { maximumFractionDigits: 6 })}
            </span>
          )}
          {tick?.change_24h != null && (
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
