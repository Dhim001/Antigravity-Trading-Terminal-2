/**
 * Footprint panel for FlexLayout Trade tabset (was TradingPanel footprint tab).
 */
import { useMemo } from 'react';
import { useStore } from '../../store/useStore';
import FootprintChartWidget from './FootprintChartWidget';

export default function FootprintPanel() {
  const symbol = useStore((s) => s.activeSymbol) || 'BTCUSDT';

  // Stabilize timestamps so the widget does not refetch every render.
  const { fromTs, toTs } = useMemo(() => {
    const to = Date.now();
    return { toTs: to, fromTs: to - 3600 * 1000 };
  }, [symbol]);

  return (
    <div className="w-full h-full min-h-0">
      <FootprintChartWidget
        symbol={symbol}
        fromTs={fromTs}
        toTs={toTs}
        priceStep={0.5}
        timeBucketMs={60000}
      />
    </div>
  );
}
