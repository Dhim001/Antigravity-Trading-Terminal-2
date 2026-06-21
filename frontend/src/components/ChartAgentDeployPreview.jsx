/**
 * Pre-deploy signal preview for CHART_AGENT — last cached insight + estimated qty.
 */
import React, { useMemo } from 'react';
import { Badge } from '@/components/ui/badge';
import SubReportCards from './SubReportCards';
import { selectAgentInsight } from '@/lib/agentInsights';
import { buildOrderDraftFromInsight } from '@/lib/insightOrderDraft';
import { formatBarTimeframeLabel } from '@/lib/barTimeframes';

export default function ChartAgentDeployPreview({
  symbol,
  timeframe,
  agentInsights,
  allocation,
  tickerPrice,
}) {
  const insight = useMemo(
    () => selectAgentInsight(agentInsights, symbol, timeframe),
    [agentInsights, symbol, timeframe],
  );

  const draft = useMemo(() => {
    if (!insight) return null;
    return buildOrderDraftFromInsight(insight, {
      tickerPrice,
      defaultAllocation: allocation || 500,
    });
  }, [insight, tickerPrice, allocation]);

  if (!symbol) return null;

  return (
    <div className="rounded-md border border-border/60 bg-muted/20 p-2.5 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[0.62rem] font-medium text-muted-foreground uppercase tracking-wide">
          Signal preview
        </span>
        <span className="text-[0.58rem] text-muted-foreground">
          {symbol} · {formatBarTimeframeLabel(timeframe)}
        </span>
      </div>
      {!insight ? (
        <p className="text-xs text-muted-foreground">
          No cached insight yet — open Analyst or wait for the next closed bar.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={insight.signal === 'BUY' ? 'buy' : insight.signal === 'SELL' ? 'sell' : 'secondary'}>
              {insight.signal || 'NONE'}
            </Badge>
            {insight.confidence != null && (
              <span className="text-xs text-muted-foreground">
                {Math.round(insight.confidence * 100)}% conf
              </span>
            )}
            {insight.score != null && (
              <span className="text-xs text-muted-foreground">score {insight.score}</span>
            )}
          </div>
          {insight.sub_reports ? (
            <SubReportCards subReports={insight.sub_reports} compact />
          ) : null}
          {draft && insight.signal && insight.signal !== 'NONE' ? (
            <p className="text-xs text-muted-foreground">
              Est. qty <span className="num-mono text-foreground">{draft.quantity}</span>
              {draft.sizeFactor != null && draft.sizeFactor !== 1 ? (
                <span> · size factor {Math.round(draft.sizeFactor * 100)}%</span>
              ) : null}
              {draft.notional != null ? (
                <span> · ~${draft.notional.toLocaleString()}</span>
              ) : null}
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}
