import { describe, expect, it } from 'vitest';
import { normalizeBotLogEntry } from './botLogInsight';

describe('normalizeBotLogEntry', () => {
  it('preserves server/DB ids', () => {
    const entry = normalizeBotLogEntry({
      id: 1268,
      bot_id: 'abc',
      level: 'INFO',
      message: 'hello',
      timestamp: '2026-08-08 11:45:00',
    });
    expect(entry.id).toBe('1268');
  });

  it('generates unique ids for same-ms live frames without server id', () => {
    const botId = '6dccb502-86d0-44fd-a305-a45390000c99';
    const a = normalizeBotLogEntry({ bot_id: botId, level: 'INFO', message: 'one' });
    const b = normalizeBotLogEntry({ bot_id: botId, level: 'SUCCESS', message: 'two' });
    expect(a.id).not.toBe(b.id);
  });
});
