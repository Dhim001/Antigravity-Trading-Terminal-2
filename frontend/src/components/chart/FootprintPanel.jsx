/**
 * Footprint panel for FlexLayout Trade tabset (was TradingPanel footprint tab).
 */
import { useEffect, useMemo, useState } from 'react';
import { useStore } from '../../store/useStore';
import { footprintPriceStep } from '../../hooks/useOrderBookDepth';
import FootprintChartWidget from './FootprintChartWidget';

const WINDOW_MS = 60 * 60 * 1000;
const REFRESH_MS = 30_000;

export default function FootprintPanel() {
  const symbol = useStore((s) => s.activeSymbol) || 'BTCUSDT';
  const last = useStore((s) => {
    const t = s.tickerData[symbol];
    const book = s.orderBooks[symbol];
    const bid = book?.bids?.[0]?.[0];
    const ask = book?.asks?.[0]?.[0];
    if (bid && ask) return (Number(bid) + Number(ask)) / 2;
    return Number(t?.price) || 0;
  });
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), REFRESH_MS);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    setNow(Date.now());
  }, [symbol]);

  const priceStep = useMemo(
    () => footprintPriceStep(last, symbol, { price: last }),
    [last, symbol],
  );

  return (
    <div className="w-full h-full min-h-0">
      <FootprintChartWidget
        symbol={symbol}
        fromTs={now - WINDOW_MS}
        toTs={now}
        priceStep={priceStep}
        timeBucketMs={60000}
      />
    </div>
  );
}
