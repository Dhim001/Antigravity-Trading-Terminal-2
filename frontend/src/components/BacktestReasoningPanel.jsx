/**
 * Post-hoc LLM trade explanations from backtest results.reasoning.
 */
import { useMemo } from 'react';
import { Brain, Loader2, Sparkles } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { useStore } from '../store/useStore';
import LlmAttribution from './LlmAttribution';
import {
  enrichReasoningTrades,
  fmtReasoningTime,
  resolveReasoningRunContext,
} from '@/lib/backtestReasoningDisplay';
import { stripLlmReasoning } from '@/lib/llmText';

function sideVariant(side) {
  const s = String(side ?? '').toUpperCase();
  if (s === 'BUY') return 'buy';
  if (s === 'SELL') return 'sell';
  return 'secondary';
}

function runKindBadgeVariant(kind) {
  if (kind === 'walk_forward') return 'outline';
  if (kind === 'sweep') return 'secondary';
  return 'outline';
}

export default function BacktestReasoningPanel({
  reasoning,
  reasoningRequested = false,
  entryCount = 0,
  tradeLog = [],
  results = null,
  className,
}) {
  const backtestRunning = useStore((s) => s.backtestRunning);
  const progress = useStore((s) => s.backtestProgress);
  const reasoningPhase = backtestRunning && progress?.phase === 'reasoning';

  const requested = reasoningRequested || reasoning?.requested || reasoning?.available != null;

  const runContext = useMemo(
    () => resolveReasoningRunContext(results, reasoning),
    [results, reasoning],
  );

  const trades = useMemo(
    () => enrichReasoningTrades(reasoning, tradeLog),
    [reasoning, tradeLog],
  );

  if (!requested && !reasoning) return null;

  const hasRows = trades.length > 0;
  const tradeCount = reasoning?.trade_count ?? trades.length;
  const panelModel = reasoning?.model || trades.find((t) => t.model)?.model;
  const panelProvider = reasoning?.provider || trades.find((t) => t.provider)?.provider;

  return (
    <section className={cn('llm-backtest-reasoning algo-backtest-lab__section', className)}>
      <header className="llm-backtest-reasoning__header">
        <div className="llm-backtest-reasoning__title-row">
          <span className="llm-backtest-reasoning__icon-wrap">
            <Brain className="size-3.5" aria-hidden />
          </span>
          <div>
            <h4 className="llm-backtest-reasoning__title algo-backtest-section__title">LLM trade explanations</h4>
            <p className="llm-backtest-reasoning__subtitle">{runContext.scope}</p>
          </div>
        </div>
        <div className="llm-backtest-reasoning__meta">
          <Badge
            variant={runKindBadgeVariant(runContext.kind)}
            className="h-5 px-1.5 text-[0.55rem] capitalize"
          >
            {runContext.title}
          </Badge>
          {reasoningPhase && (
            <Badge variant="outline" className="llm-badge-explained h-5 px-1.5 text-[0.55rem]">
              <Loader2 className="size-3 animate-spin" aria-hidden />
              Generating…
            </Badge>
          )}
          {!reasoningPhase && hasRows && (
            <Badge variant="outline" className="llm-badge-explained h-5 px-1.5 text-[0.55rem]">
              <Sparkles className="size-3" aria-hidden />
              {tradeCount} entries
            </Badge>
          )}
          {!reasoningPhase && (panelProvider || panelModel) && (
            <LlmAttribution provider={panelProvider} model={panelModel} variant="chip" />
          )}
        </div>
      </header>

      {reasoningPhase && (
        <p className="llm-backtest-reasoning__alert llm-backtest-reasoning__alert--loading">
          {progress?.message || 'Calling local/cloud LLM for each entry trade…'}
        </p>
      )}

      {!reasoningPhase && !reasoning && requested && (
        <p className="llm-backtest-reasoning__alert">
          Explanations requested — waiting for backtest to finish…
        </p>
      )}

      {!reasoningPhase && reasoning?.error && (
        <p className="llm-backtest-reasoning__alert llm-backtest-reasoning__alert--error">
          {reasoning.error}
        </p>
      )}

      {!reasoningPhase && reasoning?.message && !hasRows && !reasoning?.error && (
        <p className="llm-backtest-reasoning__alert">{reasoning.message}</p>
      )}

      {!reasoningPhase && !reasoning && !backtestRunning && !reasoningRequested && entryCount > 0 && (
        <p className="llm-backtest-reasoning__alert llm-backtest-reasoning__alert--hint">
          Check &quot;Generate trade explanations&quot; above and re-run the backtest to populate this section
          ({entryCount} entry fill{entryCount !== 1 ? 's' : ''} in last run).
        </p>
      )}

      {hasRows && (
        <div className="algo-backtest-table-scroll algo-backtest-table-scroll--reasoning">
          <table className="terminal-table algo-backtest-table llm-backtest-reasoning__table m-0">
            <thead>
              <tr>
                <th>#</th>
                <th>Time</th>
                <th>Side</th>
                <th className="text-right">Price</th>
                <th>Reason</th>
                <th>Explanation</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((row) => (
                <tr key={row._rowKey}>
                  <td className="num-mono text-muted-foreground whitespace-nowrap">
                    {row.entry_ordinal ?? row.trade_index ?? '—'}
                  </td>
                  <td className="num-mono whitespace-nowrap text-muted-foreground">
                    {fmtReasoningTime(row.bar_time ?? row.time)}
                  </td>
                  <td className="whitespace-nowrap">
                    <Badge variant={sideVariant(row.side)} className="h-5 px-1.5 text-[0.55rem]">
                      {row.side ?? '—'}
                    </Badge>
                  </td>
                  <td className="num-mono text-right whitespace-nowrap">
                    {row.price != null ? Number(row.price).toFixed(4) : '—'}
                  </td>
                  <td className="text-muted-foreground whitespace-nowrap max-w-[5rem] truncate" title={row.reason}>
                    {row.reason ?? 'ENTRY'}
                  </td>
                  <td className="llm-backtest-reasoning__explain-cell">
                    {row.insight_snapshot?.signal && (
                      <Badge variant="outline" className="h-4 px-1 text-[0.5rem] mb-0.5 mr-1">
                        {row.insight_snapshot.signal}
                        {row.insight_snapshot.confidence != null
                          ? ` ${Math.round(row.insight_snapshot.confidence * 100)}%`
                          : ''}
                      </Badge>
                    )}
                    {(() => {
                      const cleaned = stripLlmReasoning(row.narrative);
                      return cleaned ? (
                        <p className="llm-backtest-reasoning__explain-text">{cleaned}</p>
                      ) : (
                        <span className="llm-backtest-reasoning__explain-empty">No narrative returned</span>
                      );
                    })()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="algo-backtest-table-scroll__footer">
            {tradeCount} entry trade{tradeCount !== 1 ? 's' : ''} · {runContext.title}
            {reasoning?.run_kind === 'walk_forward' && ' · not in-sample sweep trials'}
          </p>
        </div>
      )}
    </section>
  );
}
