import { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw, Bot } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Popover,
  PopoverContent,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from '@/components/ui/popover';
import { cn } from '@/lib/utils';
import { Action } from '../api/protocol';
import { sendAction } from '../api/transport';
import { useStore } from '../store/useStore';
import { getCandles } from '../services/candleBuffer';
import { generateSignal } from '../utils/indicators';

const SIGNAL_STYLES = {
  'STRONG BUY': { color: '#22c55e', bg: 'rgba(34,197,94,0.12)', border: 'rgba(34,197,94,0.35)', dot: '#22c55e' },
  BUY: { color: '#4ade80', bg: 'rgba(74,222,128,0.1)', border: 'rgba(74,222,128,0.3)', dot: '#4ade80' },
  NEUTRAL: { color: '#94a3b8', bg: 'rgba(148,163,184,0.08)', border: 'rgba(148,163,184,0.25)', dot: '#94a3b8' },
  SELL: { color: '#f87171', bg: 'rgba(248,113,113,0.1)', border: 'rgba(248,113,113,0.3)', dot: '#f87171' },
  'STRONG SELL': { color: '#ef4444', bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.35)', dot: '#ef4444' },
};

function displaySignalFromInsight(insight) {
  if (!insight) return null;
  const score = insight.score ?? 0;
  if (score >= 4) return 'STRONG BUY';
  if (score >= 2) return 'BUY';
  if (score <= -4) return 'STRONG SELL';
  if (score <= -2) return 'SELL';
  return 'NEUTRAL';
}

function fallbackFromCandles(candles) {
  const local = generateSignal(candles);
  return {
    signal: local.signal,
    score: local.score,
    reasons: local.reasons,
    confidence: Math.min(1, Math.abs(local.score) / 4),
    narrative: null,
    source: 'local',
  };
}

/**
 * Backend-authoritative chart analyst badge with local fallback while loading/offline.
 */
export default function ChartAnalystBadge({ symbol, onDeployAgent }) {
  const agentInsight = useStore((state) => state.agentInsights[symbol]);
  const [refreshing, setRefreshing] = useState(false);
  const lastCandleTime = useStore((state) => {
    const rev = state.candleRevision[symbol];
    if (!rev) return 0;
    const candles = getCandles(symbol);
    return candles.length > 0 ? candles[candles.length - 1].time : 0;
  });

  const localFallback = useMemo(() => {
    const candles = getCandles(symbol);
    if (candles.length < 30) return null;
    return fallbackFromCandles(candles);
  }, [lastCandleTime, symbol]);

  const display = useMemo(() => {
    if (agentInsight) {
      return {
        ...agentInsight,
        signal: displaySignalFromInsight(agentInsight) || agentInsight.signal || 'NEUTRAL',
        source: 'backend',
      };
    }
    return localFallback;
  }, [agentInsight, localFallback]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      sendAction(Action.CHART_ANALYZE, { symbol, force_llm: false });
      toast.message('Chart analysis requested…');
    } catch (err) {
      toast.error(err?.message || 'Failed to request analysis');
    } finally {
      setTimeout(() => setRefreshing(false), 800);
    }
  }, [symbol]);

  useEffect(() => {
    if (!agentInsight && symbol && lastCandleTime) {
      sendAction(Action.CHART_ANALYZE, { symbol });
    }
  }, [symbol, lastCandleTime, agentInsight]);

  if (!display) return null;

  const sigStyle = SIGNAL_STYLES[display.signal] || SIGNAL_STYLES.NEUTRAL;
  const isStrong = display.signal.startsWith('STRONG');
  const confidencePct = Math.round((display.confidence ?? 0) * 100);

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-7 shrink-0 gap-1.5 rounded-full px-3 text-xs font-bold tracking-wide"
          style={{
            borderColor: sigStyle.border,
            color: sigStyle.color,
            backgroundColor: sigStyle.bg,
          }}
        >
          <span
            className={cn('size-1.5 rounded-full', isStrong && 'animate-pulse')}
            style={{
              background: sigStyle.dot,
              boxShadow: isStrong ? `0 0 8px ${sigStyle.dot}` : undefined,
            }}
          />
          {display.signal}
          <span className="text-[0.62rem] opacity-70">
            ({display.score > 0 ? '+' : ''}{display.score})
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-3" style={{ borderColor: sigStyle.border }}>
        <PopoverHeader className="gap-1">
          <PopoverTitle className="text-[0.62rem] uppercase tracking-wide text-muted-foreground">
            Chart Analyst {display.source === 'backend' ? '· Server' : '· Local'}
          </PopoverTitle>
        </PopoverHeader>

        <div className="mb-2">
          <div className="mb-1 flex justify-between text-[0.62rem] text-muted-foreground">
            <span>Confidence</span>
            <span>{confidencePct}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full transition-all"
              style={{ width: `${confidencePct}%`, background: sigStyle.dot }}
            />
          </div>
        </div>

        {display.reasons?.length ? (
          <ul className="mb-3 space-y-1 text-xs">
            {display.reasons.map((r, i) => (
              <li key={i} className="flex gap-2" style={{ color: sigStyle.color }}>
                <span className="opacity-40">•</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mb-3 text-xs text-muted-foreground">No detailed reasons available.</p>
        )}

        {display.narrative ? (
          <p className="mb-3 rounded-md border border-border/60 bg-muted/30 p-2 text-xs leading-relaxed text-foreground/90">
            {display.narrative}
          </p>
        ) : null}

        <div className="flex gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="h-7 flex-1 gap-1 text-xs"
            disabled={refreshing}
            onClick={handleRefresh}
          >
            <RefreshCw className={cn('size-3', refreshing && 'animate-spin')} />
            Refresh
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 flex-1 gap-1 text-xs"
            onClick={() => window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'analyst' }))}
          >
            History
          </Button>
          {onDeployAgent ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 flex-1 gap-1 text-xs"
              onClick={onDeployAgent}
            >
              <Bot className="size-3" />
              Deploy Agent
            </Button>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}
