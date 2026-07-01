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

function ParamTable({ params }) {
  if (!params || Object.keys(params).length === 0) {
    return <p className="text-[0.65rem] text-muted-foreground">No parameter changes suggested.</p>;
  }
  return (
    <ul className="space-y-1 text-[0.65rem]">
      {Object.entries(params).map(([key, val]) => (
        <li key={key} className="flex justify-between gap-2">
          <span className="text-muted-foreground">{key}</span>
          <span className="num-mono">{String(val)}</span>
        </li>
      ))}
    </ul>
  );
}

function ComparisonBlock({ comparison }) {
  if (!comparison || comparison.error) {
    return comparison?.error ? (
      <p className="text-[0.62rem] text-trading-down">{comparison.error}</p>
    ) : null;
  }
  const baseline = comparison.baseline || {};
  const proposed = comparison.proposed || {};
  return (
    <div className="mt-2 space-y-1">
      <p className="text-[0.62rem] font-medium text-muted-foreground">
        Shadow backtest ({comparison.days}d)
      </p>
      <div className="grid grid-cols-3 gap-1 text-[0.62rem]">
        <span className="text-muted-foreground">Metric</span>
        <span className="text-muted-foreground text-right">Current</span>
        <span className="text-muted-foreground text-right">Proposed</span>
        {BACKTEST_COMPARE_METRICS.slice(0, 5).map(({ key, label, fmt, higherIsBetter }) => {
          const cur = metricValue(baseline, key);
          const next = metricValue(proposed, key);
          const delta = formatMetricDelta(cur, next, { fmt, higherIsBetter });
          return (
            <React.Fragment key={key}>
              <span>{label}</span>
              <span className="num-mono text-right">{formatSignedValue(cur, fmt)}</span>
              <span className={cn('num-mono text-right', TONE_CLASS[delta.tone])}>
                {formatSignedValue(next, fmt)}
                {delta.text ? ` (${delta.text})` : ''}
              </span>
            </React.Fragment>
          );
        })}
      </div>
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

  return (
    <div className={cn(
      'strategy-advisor rounded-md border border-primary/25 bg-primary/5 p-3',
      compact && 'p-2',
    )}>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <div>
          <span className="text-[0.65rem] font-semibold uppercase tracking-wide text-foreground">
            Strategy advisor
          </span>
          <p className="text-[0.6rem] text-muted-foreground mt-0.5">
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
      </div>

      {!effectiveBotId && (
        <div className="space-y-2 mb-2">
          <p className="text-[0.62rem] text-muted-foreground">
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
            <p className="text-[0.62rem] text-trading-warn">
              No bots yet — deploy from Algo tab, then return here.
            </p>
          )}
        </div>
      )}

      {effectiveBotId && !advisor && !loading && (
        <p className="text-[0.62rem] text-muted-foreground">
          Uses recent backtests, sentiment, and filter stats for bot{' '}
          <span className="num-mono">{effectiveBotId.slice(0, 10)}</span>
          {agentLlmAvailable ? ' (LLM)' : ' (rules)'}.
        </p>
      )}

      {advisor && (
        <div className="space-y-2">
          {advisor.rationale && (
            <p className="text-[0.65rem] leading-snug">{advisor.rationale}</p>
          )}
          <ParamTable params={advisor.suggested_params} />
          <ComparisonBlock comparison={advisor.backtest_comparison} />
          {advisor.validation_warnings?.length > 0 && (
            <p className="text-[0.6rem] text-muted-foreground">
              {advisor.validation_warnings.join('; ')}
            </p>
          )}
          {Object.keys(advisor.suggested_params || {}).length > 0 && (
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
          )}
        </div>
      )}
    </div>
  );
}
