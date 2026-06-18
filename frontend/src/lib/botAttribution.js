import { toUnixSeconds } from '../services/candleBuffer';

/** Short bot id for table badges. */
export function shortBotId(id) {
  if (!id) return null;
  return String(id).slice(0, 8);
}

/** Index active bots by id and by symbol (prefers RUNNING over PAUSED). */
export function buildBotLookup(activeBots = []) {
  const byId = {};
  const bySymbol = {};
  for (const bot of activeBots) {
    if (!bot?.id) continue;
    byId[bot.id] = bot;
    if (bot.status === 'STOPPED') continue;
    const prev = bySymbol[bot.symbol];
    if (!prev || (bot.status === 'RUNNING' && prev.status !== 'RUNNING')) {
      bySymbol[bot.symbol] = bot;
    }
  }
  return { byId, bySymbol };
}

/**
 * Closed-bar unix time that triggered a bot trade (not order-fill wall clock).
 * Prefer explicit signal_bar_time; fall back to signal_id payload.
 */
export function parseSignalBarTime(trade) {
  if (!trade) return null;
  const explicit = toUnixSeconds(trade.signal_bar_time);
  if (explicit != null) return explicit;

  const sid = trade.signal_id;
  if (!sid || typeof sid !== 'string') return null;
  const parts = sid.split(':');
  if (parts.length < 3 || parts[1] === 'sltp') return null;
  const sec = Number(parts[1]);
  return Number.isFinite(sec) && sec > 1e9 ? Math.floor(sec) : null;
}

function resolveOwnerBot(botId, symbol, size, activeBots) {
  const { byId } = buildBotLookup(activeBots);
  const live = byId[botId];
  if (live) {
    return { ...live, _size: size, _active: live.status === 'RUNNING' || live.status === 'PAUSED' };
  }
  return {
    id: botId,
    symbol,
    strategy: 'BOT',
    status: 'STOPPED',
    _size: size,
    _active: false,
  };
}

/** Best-effort bot owner(s) for an open position. */
export function getPositionBots(symbol, position, { activeBots = [], tradeHistory = [] } = {}) {
  if (!position || !position.size) return [];

  const owners = Array.isArray(position.bot_owners) ? position.bot_owners : [];
  if (owners.length) {
    return owners.map((o) => resolveOwnerBot(o.bot_id, symbol, o.size, activeBots));
  }

  const single = getPositionBot(symbol, position, { activeBots, tradeHistory });
  return single ? [single] : [];
}

/** Best-effort bot owner for an open position. */
export function getPositionBot(symbol, position, { activeBots = [], tradeHistory = [] } = {}) {
  if (!position || !position.size) return null;

  const owners = Array.isArray(position.bot_owners) ? position.bot_owners : [];
  if (owners.length === 1) {
    return resolveOwnerBot(owners[0].bot_id, symbol, owners[0].size, activeBots);
  }
  if (owners.length > 1) {
    return null;
  }
  const { bySymbol } = buildBotLookup(activeBots);
  if (bySymbol[symbol]) {
    return { ...bySymbol[symbol], _active: bySymbol[symbol].status === 'RUNNING' || bySymbol[symbol].status === 'PAUSED' };
  }

  const recent = [...tradeHistory]
    .filter((t) => t.symbol === symbol && t.bot_id && t.status === 'FILLED')
    .sort((a, b) => (b.timestamp ?? 0) - (a.timestamp ?? 0));
  if (!recent.length) return null;
  return resolveOwnerBot(recent[0].bot_id, symbol, null, activeBots);
}

export function tradeSourceLabel(trade, botLookup) {
  if (!trade?.bot_id) return { kind: 'manual', label: 'Manual' };
  const bot = botLookup.byId[trade.bot_id];
  const strat = bot?.strategy || 'Bot';
  return { kind: 'bot', label: strat, botId: trade.bot_id };
}

/** Parse ISO / epoch / naive UTC timestamps from backend. */
export function parseTradeTimestamp(ts) {
  if (ts == null || ts === '') return null;
  if (typeof ts === 'number') {
    return new Date(ts < 1e11 ? ts * 1000 : ts);
  }
  const s = String(ts);
  if (s.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(s)) {
    const d = new Date(s);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const d = new Date(`${s}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}
