/**
 * Post-trade explain card (A4) — shared by bot detail and history.
 */
import React, { useState } from 'react';
import { useStore } from '../store/useStore';
import { Action } from '../api/protocol';
import { invokeHttpAction } from '../api/transport';
import SubReportCards from './SubReportCards';
import { cn } from '@/lib/utils';
import { Loader2 } from 'lucide-react';
import { normalizeAnalystTimeframe, selectAgentInsight } from '@/lib/agentInsights';

export function isEntryTrade(trade) {
  if (!trade) return false;
  const v = trade.is_exit;
  return v === false || v === 0 || v === '0' || v == null;
}

export function tradeIdKey(trade) {
  if (trade?.id == null || trade.id === '') return null;
  return String(trade.id);
}

export function findInsightForTrade(trade, symbol, timeframe, agentInsights, agentInsightHistory) {
  if (!trade) return null;
  const tf = normalizeAnalystTimeframe(timeframe);
  const barTime = trade.signal_bar_time;
  if (barTime != null) {
    const history = agentInsightHistory[symbol] ?? [];
    const match = history.find(
      (i) => i.bar_time === barTime && normalizeAnalystTimeframe(i.timeframe) === tf,
    );
    if (match) return match;
  }
  const current = selectAgentInsight(agentInsights, symbol, tf);
  if (current && barTime != null && current.bar_time === barTime) {
    return current;
  }
  return null;
}

export default function TradeExplainCard({
  trade,
  symbol,
  botId,
  botStrategy,
  botTimeframe,
  agentInsights,
  agentInsightHistory,
  useLlm = false,
  compact = false,
}) {
  const tradeKey = tradeIdKey(trade);
  const explain = useStore((s) => (tradeKey ? s.tradeExplains[tradeKey] : null));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const isEntry = isEntryTrade(trade);
  const label = isEntry ? 'Why we entered' : 'Why we exited';

  const insight = explain?.insight
    ?? findInsightForTrade(trade, symbol, botTimeframe, agentInsights, agentInsightHistory);

  const fetchExplain = async () => {
    if (!tradeKey || !botId || loading || explain) return;
    setLoading(true);
    setError(null);
    try {
      await invokeHttpAction(Action.EXPLAIN_TRADE, {
        bot_id: botId,
        trade_id: tradeKey,
        use_llm: Boolean(useLlm),
      });
    } catch (err) {
      setError(err?.message || 'Could not load explanation');
    } finally {
      setLoading(false);
    }
  };

  const handleToggle = (e) => {
    e.stopPropagation();
    if (e.currentTarget.open) {
      void fetchExplain();
    }
  };

  const hasContent = Boolean(
    explain?.summary
    || explain?.narrative
    || insight?.reasons?.length
    || insight?.narrative
    || insight?.sub_reports
    || (explain?.recent_logs?.length > 0),
  );

  if (!trade) return null;

  return (
    <details
      className={cn('bot-trade-explain', compact && 'bot-trade-explain--compact')}
      onToggle={handleToggle}
      onClick={(e) => e.stopPropagation()}
    >
      <summary className="bot-trade-explain__summary" onClick={(e) => e.stopPropagation()}>
        <span>{label}</span>
        {loading && <Loader2 className="size-3 animate-spin shrink-0" aria-hidden />}
      </summary>
      <div className="bot-trade-explain__body">
        {loading && !hasContent && (
          <p className="bot-trade-explain__status">Loading explanation…</p>
        )}
        {error && <p className="bot-trade-explain__error">{error}</p>}
        {!tradeKey && (
          <p className="bot-trade-explain__status">Explanation unavailable for this fill.</p>
        )}
        {explain?.summary && (
          <p className="bot-trade-explain__summary-text">{explain.summary}</p>
        )}
        {explain?.narrative && (
          <p className="bot-trade-explain__narrative">{explain.narrative}</p>
        )}
        {explain?.sources?.length > 0 && (
          <p className="bot-trade-explain__sources text-muted-foreground">
            Sources: {explain.sources.join(', ')}
          </p>
        )}
        {insight?.sub_reports ? (
          <div className="bot-trade-explain__reports">
            <SubReportCards subReports={insight.sub_reports} />
          </div>
        ) : insight?.reasons?.length > 0 ? (
          <ul className="bot-trade-explain__reasons">
            {insight.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        ) : null}
        {!explain?.summary && insight?.narrative && (
          <p className="bot-trade-explain__narrative">{insight.narrative}</p>
        )}
        {explain?.recent_logs?.length > 0 && (
          <div className="bot-trade-explain__logs">
            <p className="bot-trade-explain__logs-title">Recent bot log</p>
            <ul className="bot-trade-explain__logs-list">
              {explain.recent_logs.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          </div>
        )}
        {!loading && !error && tradeKey && !hasContent && (
          <p className="bot-trade-explain__status">
            {botStrategy === 'CHART_AGENT'
              ? 'No analyst insight recorded for this bar.'
              : 'No detailed explanation available for this strategy.'}
          </p>
        )}
      </div>
    </details>
  );
}
