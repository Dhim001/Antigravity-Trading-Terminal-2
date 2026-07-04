import { describe, it, expect } from 'vitest';
import { buildTradeMarkerPoints } from './backtestPdfChart';

const CANDLES = [
  { time: 1700000000, open: 100, high: 102, low: 99, close: 101 },
  { time: 1700000060, open: 101, high: 103, low: 100, close: 102 },
  { time: 1700000120, open: 102, high: 104, low: 101, close: 103 },
];

describe('buildTradeMarkerPoints', () => {
  it('maps entry and exit trades to candle bars', () => {
    const trades = [
      { time: 1700000060, side: 'BUY', is_exit: false, price: 100.5, reason: 'ENTRY_LONG' },
      { time: 1700000120, side: 'SELL', is_exit: true, price: 103, pnl: 12, reason: 'TAKE_PROFIT' },
    ];

    const markers = buildTradeMarkerPoints(CANDLES, trades, 60);
    expect(markers).toHaveLength(2);

    const entry = markers.find((m) => !m.isExit);
    const exit = markers.find((m) => m.isExit);
    expect(entry).toMatchObject({ idx: 1, side: 'BUY', isExit: false });
    expect(entry.yPrice).toBe(100);
    expect(exit).toMatchObject({ idx: 2, isExit: true, pnl: 12 });
    expect(exit.yPrice).toBe(103);
  });

  it('skips trades outside the candle window', () => {
    const trades = [
      { time: 1699999000, side: 'BUY', is_exit: false, price: 98 },
      { time: 1700001000, side: 'SELL', is_exit: true, price: 105, pnl: -1 },
    ];
    expect(buildTradeMarkerPoints(CANDLES, trades, 60)).toHaveLength(0);
  });

  it('treats is_exit=1 as exit marker', () => {
    const trades = [
      { time: 1700000060, side: 'SELL', is_exit: 1, price: 101, pnl: -2 },
    ];
    const [marker] = buildTradeMarkerPoints(CANDLES, trades, 60);
    expect(marker.isExit).toBe(true);
    expect(marker.idx).toBe(1);
  });
});
