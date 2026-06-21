/** Bot log → insight resolution (A2). */

import { normalizeAnalystTimeframe } from './agentInsights';

export function isSignalLog(log) {
  if (!log) return false;
  if (log.meta?.event_type === 'signal') return true;
  const msg = log.message || log.line || (typeof log === 'string' ? log : '');
  return /Entry (BUY|SELL)|Exit (BUY|SELL)|signal @/i.test(msg);
}

export function findInsightForLog(log, agentInsightHistory = {}) {
  const meta = log?.meta;
  if (!meta) return null;
  const symbol = (meta.symbol || '').toUpperCase();
  const tf = normalizeAnalystTimeframe(meta.timeframe);
  const barTime = meta.bar_time;
  if (symbol && barTime != null) {
    const history = agentInsightHistory[symbol] ?? [];
    const match = history.find(
      (i) => i.bar_time === barTime && normalizeAnalystTimeframe(i.timeframe) === tf,
    );
    if (match) return match;
  }
  if (meta.sub_reports || meta.reasons?.length || meta.confidence != null) {
    return {
      symbol: meta.symbol,
      timeframe: meta.timeframe,
      bar_time: meta.bar_time,
      signal: meta.side,
      confidence: meta.confidence,
      score: meta.score,
      reasons: meta.reasons,
      sub_reports: meta.sub_reports,
      insight_id: meta.insight_id,
    };
  }
  return null;
}

export function formatLogTimestamp(ts) {
  if (ts == null) return new Date().toLocaleTimeString();
  const raw = typeof ts === 'string' && !ts.endsWith('Z') && !/[+-]\d{2}:\d{2}$/.test(ts)
    ? `${ts}Z`
    : ts;
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString();
}

export function normalizeBotLogEntry(log, index = 0) {
  if (typeof log === 'string') {
    return {
      id: `legacy-${index}-${log.slice(0, 16)}`,
      bot_id: null,
      level: 'INFO',
      message: log.replace(/^\[[^\]]+\]\s*/, ''),
      timestamp: null,
      meta: null,
      line: log,
    };
  }
  const time = formatLogTimestamp(log.timestamp);
  const botTag = log.bot_id ? `[${String(log.bot_id).slice(0, 8)}] ` : '';
  const level = log.level ? `${log.level} - ` : '';
  const line = `[${time}] ${botTag}${level}${log.message || ''}`;
  return {
    id: log.id ?? `${log.bot_id ?? 'log'}-${log.timestamp ?? time}-${index}`,
    bot_id: log.bot_id ?? null,
    level: log.level ?? 'INFO',
    message: log.message ?? '',
    timestamp: log.timestamp ?? null,
    meta: log.meta ?? null,
    line,
  };
}

export function logLineClass(log) {
  const text = log?.line ?? log?.message ?? (typeof log === 'string' ? log : '');
  if (/BUY|SUCCESS/i.test(text)) return 'algo-log-line algo-log-line--success';
  if (/SELL|ERROR|STOP/i.test(text)) return 'algo-log-line algo-log-line--error';
  if (/WARN/i.test(text)) return 'algo-log-line algo-log-line--warn';
  if (/INFO|started/i.test(text)) return 'algo-log-line algo-log-line--info';
  return 'algo-log-line';
}
