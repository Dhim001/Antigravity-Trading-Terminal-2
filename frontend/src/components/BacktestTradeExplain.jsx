/**
 * Backtest trade explain — insight snapshot, sub-reports, meta-label transparency.
 */
import React, { useMemo } from 'react';
import { cn } from '@/lib/utils';
import SubReportCards from './SubReportCards';
import { isEntryTrade } from './TradeExplainCard';

function MetaLabelExplain({ explain }) {
  if (!explain) return null;
  const prob = explain.prob;
  const decision = explain.decision;
  const contributions = explain.contributions ?? [];

  return (
    <div className="algo-backtest-trade-explain__meta">
      <p className="algo-backtest-trade-explain__meta-title">Meta-label gate</p>
      <p className="text-[0.62rem] text-muted-foreground m-0 mb-1">
        P(win)
        {' '}
        <span className="num-mono font-semibold text-foreground">
          {prob != null ? `${Math.round(prob * 1000) / 10}%` : '—'}
        </span>
        {decision ? ` · ${decision}` : ''}
      </p>
      {contributions.length > 0 && (
        <ul className="algo-backtest-trade-explain__meta-list">
          {contributions.slice(0, 5).map((c) => (
            <li key={c.feature}>
              <span className="num-mono">{c.feature}</span>
              {' '}
              <span className="text-muted-foreground">
                {c.direction}
                {c.contribution != null ? ` (${c.contribution >= 0 ? '+' : ''}${c.contribution})` : ''}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function BacktestTradeExplain({ trade, strategy, className }) {
  const insight = useMemo(() => {
    if (!trade) return null;
    if (trade.insight_snapshot && typeof trade.insight_snapshot === 'object') {
      return trade.insight_snapshot;
    }
    return null;
  }, [trade]);

  if (!trade) return null;

  const isEntry = isEntryTrade(trade);
  const label = isEntry ? 'Why this entry' : 'Why this exit';
  const reasons = insight?.reasons ?? [];
  const hasMeta = Boolean(trade.meta_label_explain);
  const hasSubReports = Boolean(insight?.sub_reports);
  const hasContent = hasSubReports || reasons.length > 0 || hasMeta
    || (strategy === 'CHART_AGENT' && isEntry);

  if (!hasContent) return null;

  return (
    <section className={cn('algo-backtest-trade-explain', className)}>
      <p className="algo-backtest-trade-explain__title">{label}</p>
      {insight?.signal && (
        <p className="algo-backtest-trade-explain__signal text-[0.62rem] text-muted-foreground m-0 mb-1">
          Signal
          {' '}
          <strong className="text-foreground">{insight.signal}</strong>
          {insight.confidence != null ? ` · ${Math.round(insight.confidence * 100)}% conf` : ''}
          {insight.score != null ? ` · score ${insight.score}` : ''}
        </p>
      )}
      {hasSubReports && (
        <div className="algo-backtest-trade-explain__reports mb-2">
          <SubReportCards subReports={insight.sub_reports} compact />
        </div>
      )}
      {reasons.length > 0 && !hasSubReports && (
        <ul className="algo-backtest-trade-explain__reasons">
          {reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      <MetaLabelExplain explain={trade.meta_label_explain} />
      {trade.execution_chain?.length > 0 && (
        <div className="algo-backtest-trade-explain__chain">
          <p className="algo-backtest-trade-explain__meta-title">Execution chain</p>
          <ul className="algo-backtest-trade-explain__meta-list">
            {trade.execution_chain.map((step, i) => (
              <li key={i}>
                <span className="font-medium">{step.stage}</span>
                {' '}
                <span className={step.ok ? 'text-trading-up' : 'text-trading-down'}>
                  {step.ok ? 'ok' : 'blocked'}
                </span>
                {step.reason ? ` — ${step.reason}` : ''}
                {step.price != null ? ` @ ${step.price}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
      {strategy === 'CHART_AGENT' && isEntry && !insight && !hasMeta && (
        <p className="text-[0.58rem] text-muted-foreground m-0">
          No analyst snapshot stored for this bar. Load full trade history if this was a long run.
        </p>
      )}
    </section>
  );
}
