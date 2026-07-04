/**
 * LLM strategy parameter advisor — suggest + compare + apply.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Loader2, Sparkles, Check } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  BACKTEST_COMPARE_METRICS,
  formatMetricDelta,
  formatSignedValue,
  metricValue,
  TONE_CLASS,
} from '@/lib/metricComparison';
import { fetchStrategySuggestion } from '@/api/endpoints';
import { invokeHttpAction } from '@/api/transport';
import { Action } from '@/api/protocol';

function formatParamLabel(key) {
  return String(key).replace(/_/g, ' ');
}

function ParamTable({ params }) {
  if (!params || Object.keys(params).length === 0) {
    return (
      <p className="strategy-advisor__empty text-muted-foreground m-0 px-0.5">
        No parameter changes suggested.
      </p>
    );
  }
  return (
    <div className="algo-backtest-table-scroll algo-backtest-table-scroll--advisor-params">
      <table className="terminal-table algo-backtest-table strategy-advisor__table m-0">
        <thead>
          <tr>
            <th>Parameter</th>
            <th className="text-right">Suggested</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(params).map(([key, val]) => (
            <tr key={key}>
              <td className="capitalize">{formatParamLabel(key)}</td>
              <td className="num-mono text-right">{String(val)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ComparisonBlock({ comparison }) {
  if (!comparison || comparison.error) {
    return comparison?.error ? (
      <p className="strategy-advisor__empty text-trading-down m-0 px-0.5">{comparison.error}</p>
    ) : null;
  }
  const baseline = comparison.baseline || {};
  const proposed = comparison.proposed || {};
  const metrics = BACKTEST_COMPARE_METRICS.slice(0, 6);

  return (
    <div className="algo-backtest-table-scroll algo-backtest-table-scroll--advisor-compare">
      <table className="terminal-table algo-backtest-table strategy-advisor__table m-0">
        <thead>
          <tr>
            <th>Metric</th>
            <th className="text-right">Current</th>
            <th className="text-right">Proposed</th>
            <th className="text-right">Δ</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map(({ key, label, prefix = '', suffix = '', higherIsBetter }) => {
            const cur = metricValue(baseline, key);
            const next = metricValue(proposed, key);
            const { text, tone } = formatMetricDelta(cur, next, { prefix, suffix, higherIsBetter });
            const curTone = (key === 'total_pnl' || key === 'return_pct')
              ? ((cur ?? 0) >= 0 ? 'up' : 'down')
              : 'neutral';
            return (
              <tr key={key}>
                <td>{label}</td>
                <td className={cn('num-mono text-right', TONE_CLASS[curTone] ?? TONE_CLASS.neutral)}>
                  {formatSignedValue(cur, { prefix, suffix })}
                </td>
                <td className="num-mono text-right text-muted-foreground">
                  {formatSignedValue(next, { prefix, suffix })}
                </td>
                <td className={cn('num-mono text-right', TONE_CLASS[tone])}>{text}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function StrategySuggestPanel({
  botId = null,
  candidateBots = [],
  backtestDays = 30,
  recentResults = null,
  agentLlmAvailable = false,
  compact = false,
  symbol = null,
}) {
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [advisor, setAdvisor] = useState(null);
  const [pickedBotId, setPickedBotId] = useState('');

  const sortedCandidates = useMemo(() => {
    const sym = String(symbol || recentResults?.meta?.symbol || '').toUpperCase();
    const list = [...(candidateBots || [])];
    if (sym) {
      list.sort((a, b) => {
        const am = String(a.symbol || '').toUpperCase() === sym ? 0 : 1;
        const bm = String(b.symbol || '').toUpperCase() === sym ? 0 : 1;
        return am - bm;
      });
    }
    return list;
  }, [candidateBots, symbol, recentResults?.meta?.symbol]);

  const effectiveBotId = botId || pickedBotId || '';

  useEffect(() => {
    if (botId) {
      setPickedBotId('');
      return;
    }
    if (sortedCandidates.length === 1) {
      setPickedBotId(sortedCandidates[0].id);
    }
  }, [botId, sortedCandidates]);

  const runSuggest = async () => {
    if (!effectiveBotId || loading) return;
    setLoading(true);
    try {
      const body = await fetchStrategySuggestion(effectiveBotId, {
        days: Number(backtestDays) || 30,
        runBacktest: true,
        useLlm: agentLlmAvailable,
        recentResults,
      });
      if (!body?.ok) throw new Error(body?.error || 'Advisor request failed');
      setAdvisor(body.advisor);
    } catch (err) {
      toast.error(err?.message || 'Could not load strategy suggestion');
    } finally {
      setLoading(false);
    }
  };

  const applySuggestion = async () => {
    const patch = advisor?.suggested_params;
    if (!effectiveBotId || !patch || Object.keys(patch).length === 0 || applying) return;
    setApplying(true);
    try {
      await invokeHttpAction(Action.BOT_UPDATE_CONFIG, {
        bot_id: effectiveBotId,
        config: patch,
      });
      toast.success('Suggested parameters applied to bot config');
    } catch (err) {
      toast.error(err?.message || 'Failed to apply config');
    } finally {
      setApplying(false);
    }
  };

  const hasParams = Object.keys(advisor?.suggested_params || {}).length > 0;
  const hasComparison = Boolean(advisor?.backtest_comparison && !advisor.backtest_comparison.error);

  return (
    <div className={cn('strategy-advisor', compact && 'strategy-advisor--compact')}>
      <header className="strategy-advisor__header">
        <div>
          <h4 className="strategy-advisor__title">Strategy advisor</h4>
          <p className="strategy-advisor__subtitle">
            LLM/heuristic params + shadow backtest comparison
          </p>
        </div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="h-7 text-[0.62rem] gap-1 shrink-0"
          onClick={runSuggest}
          disabled={loading || !effectiveBotId}
          title={effectiveBotId ? 'Suggest strategy parameters' : 'Select a bot first'}
        >
          {loading ? <Loader2 className="size-3 animate-spin" /> : <Sparkles className="size-3" />}
          Suggest strategy
        </Button>
      </header>

      {!effectiveBotId && (
        <div className="strategy-advisor__setup">
          <p className="text-muted-foreground m-0">
            Select a deployed bot to analyze. Run backtest from Algo with a bot row selected, or pick one below.
          </p>
          {sortedCandidates.length > 0 ? (
            <Select value={pickedBotId || undefined} onValueChange={setPickedBotId}>
              <SelectTrigger className="h-8 text-xs">
                <SelectValue placeholder="Choose bot…" />
              </SelectTrigger>
              <SelectContent>
                {sortedCandidates.map((b) => (
                  <SelectItem key={b.id} value={b.id} className="text-xs">
                    {b.symbol} · {b.strategy} · {b.id.slice(0, 8)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <p className="text-trading-warn m-0">
              No bots yet — deploy from Algo tab, then return here.
            </p>
          )}
        </div>
      )}

      {effectiveBotId && !advisor && !loading && (
        <p className="strategy-advisor__hint text-muted-foreground m-0">
          Uses recent backtests, sentiment, and filter stats for bot{' '}
          <span className="num-mono">{effectiveBotId.slice(0, 10)}</span>
          {agentLlmAvailable ? ' (LLM)' : ' (rules)'}.
        </p>
      )}

      {advisor && (
        <div className="strategy-advisor__results">
          {advisor.rationale && (
            <p className="strategy-advisor__rationale m-0">
              {advisor.rationale}
            </p>
          )}

          <div className={cn(
            'strategy-advisor__tables',
            (hasParams || hasComparison) && 'strategy-advisor__tables--split',
          )}
          >
            {hasParams && (
              <section className="strategy-advisor__panel">
                <p className="strategy-advisor__panel-title algo-backtest-section__title">
                  Suggested parameters ({Object.keys(advisor.suggested_params).length})
                </p>
                <ParamTable params={advisor.suggested_params} />
              </section>
            )}
            {hasComparison && (
              <section className="strategy-advisor__panel">
                <p className="strategy-advisor__panel-title algo-backtest-section__title">
                  Shadow backtest ({advisor.backtest_comparison.days}d)
                </p>
                <ComparisonBlock comparison={advisor.backtest_comparison} />
              </section>
            )}
          </div>

          {advisor.validation_warnings?.length > 0 && (
            <p className="strategy-advisor__warnings text-muted-foreground m-0">
              {advisor.validation_warnings.join('; ')}
            </p>
          )}
          {hasParams && (
            <div className="strategy-advisor__actions">
              <Button
                type="button"
                size="sm"
                variant="secondary"
                className="h-7 text-[0.62rem] gap-1"
                onClick={applySuggestion}
                disabled={applying}
              >
                {applying ? <Loader2 className="size-3 animate-spin" /> : <Check className="size-3" />}
                Apply to bot
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
