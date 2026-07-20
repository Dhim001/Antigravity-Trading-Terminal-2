import { toUnixSeconds } from '../services/candleBuffer';
import { getStrategyMeta } from '../config/strategies';

/** Short bot id for table badges. */
export function shortBotId(id) {
  if (!id) return null;
  return String(id).slice(0, 8);
}

const BOT_STATUS_LABEL = {
  RUNNING: 'Running',
  PAUSED: 'Paused',
  STOPPED: 'Stopped',
};

/** Index bots by id and by symbol (prefers RUNNING over PAUSED). */
export function buildBotLookup(activeBots = [], botHistory = []) {
  const byId = {};
  const bySymbol = {};
  const seen = new Set();
  const all = [...(activeBots || []), ...(botHistory || [])];

  for (const bot of all) {
    if (!bot?.id) continue;
    if (seen.has(bot.id)) {
      const prev = byId[bot.id];
      const statusRank = (s) => (s === 'RUNNING' ? 3 : s === 'PAUSED' ? 2 : 1);
      const primary = statusRank(bot.status) > statusRank(prev.status) ? bot : prev;
      const secondary = primary === bot ? prev : bot;
      byId[bot.id] = {
        ...secondary,
        ...primary,
        strategy: primary.strategy || secondary.strategy,
        timeframe: primary.timeframe || secondary.timeframe,
      };
      continue;
    }
    seen.add(bot.id);
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

/**
 * Classify how a bot trade was triggered from signal_id + side.
 * @returns {{ type: 'risk'|'close'|'entry'|'unknown', label: string }}
 */
export function parseTradeTrigger(signalId, side) {
  if (!signalId || typeof signalId !== 'string') {
    if (side === 'SELL') return { type: 'close', label: 'Exit' };
    if (side === 'BUY') return { type: 'entry', label: 'Entry' };
    return { type: 'unknown', label: 'Bot order' };
  }

  const parts = signalId.split(':');
  if (parts.length >= 3 && parts[1] === 'sltp') {
    return { type: 'risk', label: 'Stop / TP' };
  }

  const kind = String(parts[2] || '').toUpperCase();
  if (kind === 'CLOSE') return { type: 'close', label: 'Strategy exit' };
  if (kind === 'BUY' || kind === 'SELL' || kind.startsWith('ENTRY')) {
    return { type: 'entry', label: 'Signal entry' };
  }
  if (side === 'SELL') return { type: 'close', label: 'Exit' };
  if (side === 'BUY') return { type: 'entry', label: 'Entry' };
  return { type: 'unknown', label: 'Bot order' };
}

/**
 * Rich attribution for a blotter row — strategy, trigger, bot lifecycle.
 * @returns {{
 *   category: 'manual'|'bot_signal'|'bot_close'|'bot_risk'|'bot_unknown',
 *   kind: 'manual'|'bot',
 *   label: string,
 *   sublabel: string,
 *   trigger: string,
 *   strategy: string|null,
 *   botId: string|null,
 *   botStatus: string|null,
 *   timeframe: string|null,
 * }}
 */
export function tradeSourceDetail(trade, botLookup) {
  if (!trade?.bot_id) {
    return {
      category: 'manual',
      kind: 'manual',
      label: 'Manual',
      sublabel: 'User-placed order',
      trigger: 'Manual',
      strategy: null,
      botId: null,
      botStatus: null,
      timeframe: null,
    };
  }

  const bot = botLookup?.byId?.[trade.bot_id];
  const strategy = bot?.strategy || null;
  const meta = getStrategyMeta(strategy);
  const trigger = parseTradeTrigger(trade.signal_id, trade.side);
  const timeframe = bot?.timeframe || null;
  const status = bot?.status || null;
  const statusLabel = BOT_STATUS_LABEL[status] || null;

  const category = trigger.type === 'risk'
    ? 'bot_risk'
    : trigger.type === 'close'
      ? 'bot_close'
      : trigger.type === 'entry'
        ? 'bot_signal'
        : 'bot_unknown';

  const sublabel = [
    trigger.label,
    timeframe,
    statusLabel,
    shortBotId(trade.bot_id),
  ].filter(Boolean).join(' · ');

  return {
    category,
    kind: 'bot',
    label: meta.label,
    sublabel,
    trigger: trigger.label,
    strategy,
    botId: trade.bot_id,
    botStatus: status,
    timeframe,
  };
}

/** @deprecated Prefer tradeSourceDetail — kept for CSV and legacy callers. */
export function tradeSourceLabel(trade, botLookup) {
  const detail = tradeSourceDetail(trade, botLookup);
  return {
    kind: detail.kind,
    label: detail.kind === 'bot' ? (detail.strategy || detail.label) : detail.label,
    botId: detail.botId,
  };
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

/**
 * Per-bot inventory size from OMS account payload (bot_owners / bot_id).
 * Does not fall back to the full symbol OMS size — that misattributes shared symbols.
 *
 * @param {string} botId
 * @param {string} symbol
 * @param {Record<string, { size?: number, bot_id?: string, bot_owners?: Array<{ bot_id?: string, botId?: string, size?: number }> }>|null|undefined} positions
 * @returns {number} signed size for this bot (0 if flat / unknown)
 */
export function getBotOwnedSize(botId, symbol, positions) {
  if (!botId || !symbol || !positions || typeof positions !== 'object') return 0;
  const pos = positions[symbol];
  if (!pos) return 0;

  const owners = Array.isArray(pos.bot_owners) ? pos.bot_owners : [];
  if (owners.length) {
    const mine = owners.find((o) => String(o?.bot_id || o?.botId || '') === String(botId));
    return mine ? Number(mine.size) || 0 : 0;
  }

  // Single-owner legacy field on the symbol position.
  if (pos.bot_id != null && String(pos.bot_id) === String(botId)) {
    return Number(pos.size) || 0;
  }

  return 0;
}

/**
 * Compact LONG/SHORT/FLAT view model for Algo Active rows.
 * @returns {{ size: number, side: 'LONG'|'SHORT'|null, label: 'LONG'|'SHORT'|'FLAT' }}
 */
export function getBotOwnedPositionView(botId, symbol, positions) {
  const size = getBotOwnedSize(botId, symbol, positions);
  if (!(Math.abs(size) > 0)) {
    return { size: 0, side: null, label: 'FLAT' };
  }
  const side = size > 0 ? 'LONG' : 'SHORT';
  return { size, side, label: side };
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
