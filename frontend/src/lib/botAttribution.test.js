import { describe, expect, it } from 'vitest';
import {
  buildBotLookup,
  parseTradeTrigger,
  tradeSourceDetail,
  getBotOwnedSize,
  getBotOwnedPositionView,
} from './botAttribution';

const lookup = buildBotLookup(
  [{ id: 'bot-live-1', symbol: 'BTCUSDT', strategy: 'MACD_RSI', status: 'RUNNING', timeframe: '5m' }],
  [{ id: 'bot-old-2', symbol: 'AAPL', strategy: 'CHART_AGENT', status: 'STOPPED', timeframe: '15m' }],
);

describe('parseTradeTrigger', () => {
  it('detects stop/take-profit exits', () => {
    expect(parseTradeTrigger('bot1:sltp:ord-9', 'SELL')).toEqual({
      type: 'risk',
      label: 'Stop / TP',
    });
  });

  it('detects strategy signal entries', () => {
    expect(parseTradeTrigger('bot1:1700000000:BUY', 'BUY')).toEqual({
      type: 'entry',
      label: 'Signal entry',
    });
  });

  it('detects strategy closes', () => {
    expect(parseTradeTrigger('bot1:1700000000:CLOSE', 'SELL')).toEqual({
      type: 'close',
      label: 'Strategy exit',
    });
  });
});

describe('tradeSourceDetail', () => {
  it('labels manual orders', () => {
    const d = tradeSourceDetail({ side: 'BUY' }, lookup);
    expect(d.kind).toBe('manual');
    expect(d.label).toBe('Manual');
  });

  it('resolves strategy and trigger for live bots', () => {
    const d = tradeSourceDetail({
      bot_id: 'bot-live-1',
      signal_id: 'bot-live-1:1700000000:BUY',
      side: 'BUY',
    }, lookup);
    expect(d.label).toBe('MACD + RSI');
    expect(d.trigger).toBe('Signal entry');
    expect(d.sublabel).toContain('5m');
    expect(d.sublabel).toContain('Running');
    expect(d.sublabel).toContain('bot-live');
  });

  it('resolves stopped bots from history', () => {
    const d = tradeSourceDetail({
      bot_id: 'bot-old-2',
      signal_id: 'bot-old-2:sltp:ord-1',
      side: 'SELL',
    }, lookup);
    expect(d.label).toBe('Chart Analyst Agent');
    expect(d.category).toBe('bot_risk');
    expect(d.trigger).toBe('Stop / TP');
    expect(d.sublabel).toContain('Stopped');
  });
});

describe('buildBotLookup', () => {
  it('dedupes active and history lists', () => {
    const l = buildBotLookup(
      [{ id: 'b1', symbol: 'X', status: 'RUNNING' }],
      [{ id: 'b1', symbol: 'X', status: 'STOPPED', strategy: 'MACD_RSI' }],
    );
    expect(l.byId.b1.strategy).toBe('MACD_RSI');
  });
});

describe('getBotOwnedSize / getBotOwnedPositionView', () => {
  it('reads this bot slice from bot_owners, not full OMS size', () => {
    const positions = {
      BTCUSDT: {
        size: 1.5,
        bot_owners: [
          { bot_id: 'bot-a', size: 0.5 },
          { bot_id: 'bot-b', size: 1.0 },
        ],
      },
    };
    expect(getBotOwnedSize('bot-a', 'BTCUSDT', positions)).toBe(0.5);
    expect(getBotOwnedPositionView('bot-a', 'BTCUSDT', positions)).toEqual({
      size: 0.5,
      side: 'LONG',
      label: 'LONG',
    });
    expect(getBotOwnedPositionView('bot-c', 'BTCUSDT', positions).label).toBe('FLAT');
  });

  it('does not attribute shared OMS size when owners omit this bot', () => {
    const positions = {
      AAPL: { size: 10, bot_owners: [{ bot_id: 'other', size: 10 }] },
    };
    expect(getBotOwnedSize('mine', 'AAPL', positions)).toBe(0);
  });

  it('falls back to single bot_id when owners absent', () => {
    const positions = {
      ETHUSDT: { size: -2, bot_id: 'solo' },
    };
    expect(getBotOwnedPositionView('solo', 'ETHUSDT', positions)).toEqual({
      size: -2,
      side: 'SHORT',
      label: 'SHORT',
    });
    expect(getBotOwnedSize('other', 'ETHUSDT', positions)).toBe(0);
  });
});
