import { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw, Bot, ShoppingCart, Layers } from 'lucide-react';
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
import { withLlmModel } from '../api/endpoints';
import { useStore } from '../store/useStore';
import LlmNarrativeBlock from './LlmNarrativeBlock';
import LlmDeepReasoningBlock from './LlmDeepReasoningBlock';
import LlmFeatureHint from './LlmFeatureHint';
import { getCandles } from '../services/candleBuffer';
import { generateSignal } from '../utils/indicators';
import SubReportCards from './SubReportCards';
import InsightOrderPreviewDialog from './InsightOrderPreviewDialog';
import { buildOrderDraftFromInsight } from '../lib/insightOrderDraft';
import { normalizeAnalystTimeframe, selectAgentInsight } from '../lib/agentInsights';

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

export default function ChartAnalystBadge({ symbol, timeframe = '1m', onDeployAgent }) {
  const agentInsights = useStore((state) => state.agentInsights);
  const agentInsight = selectAgentInsight(agentInsights, symbol, timeframe);
  const chartTf = normalizeAnalystTimeframe(timeframe);
  const agentDeepReasoning = useStore((state) => state.agentDeepReasoning);
  const agentLlmEnabled = useStore((state) => state.agentLlmEnabled);
  const agentLlmAvailable = useStore((state) => state.agentLlmAvailable);
  const setOrderPrefill = useStore((state) => state.setOrderPrefill);
  const ticker = useStore((state) => state.tickerData[symbol]);
  const [refreshing, setRefreshing] = useState(false);
  const [deepReasonLoading, setDeepReasonLoading] = useState(false);
  const [previewDraft, setPreviewDraft] = useState(null);
  const [previewOpen, setPreviewOpen] = useState(false);
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

  const handlePreviewOrder = useCallback(() => {
    const insight = agentInsight || display;
    const draft = buildOrderDraftFromInsight(
      { ...insight, symbol },
      { tickerPrice: ticker?.price },
    );
    if (!draft) {
      toast.message('No actionable BUY/SELL signal to preview');
      return;
    }
    setPreviewDraft(draft);
    setPreviewOpen(true);
  }, [agentInsight, display, symbol, ticker?.price]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      sendAction(Action.CHART_ANALYZE, { symbol, timeframe: chartTf, force_llm: false });
      toast.message(`Chart analysis requested (${timeframe})…`);
    } catch (err) {
      toast.error(err?.message || 'Failed to request analysis');
    } finally {
      setTimeout(() => setRefreshing(false), 800);
    }
  }, [symbol, chartTf, timeframe]);

  const handleDeepReason = useCallback(() => {
    if (!agentInsight?.insight_id) {
      toast.message('Run analysis first');
      return;
    }
    setDeepReasonLoading(true);
    sendAction(Action.CHART_DEEP_REASON, withLlmModel({
      symbol,
      timeframe: chartTf,
      insight_id: agentInsight.insight_id,
    })).finally(() => setDeepReasonLoading(false));
    toast.message('Deep reasoning requested…');
  }, [agentInsight, symbol, chartTf]);

  const deepFromStore = agentInsight?.insight_id
    ? agentDeepReasoning[agentInsight.insight_id]?.deep_reasoning
    : null;
  const deepReasoning = display?.deep_reasoning || deepFromStore;

  useEffect(() => {
    if (!agentInsight && symbol && lastCandleTime) {
      sendAction(Action.CHART_ANALYZE, { symbol, timeframe: chartTf });
    }
  }, [symbol, chartTf, lastCandleTime, agentInsight]);

  if (!display) return null;

  const sigStyle = SIGNAL_STYLES[display.signal] || SIGNAL_STYLES.NEUTRAL;
  const isStrong = display.signal.startsWith('STRONG');
  const confidencePct = Math.round((display.confidence ?? 0) * 100);

  return (
    <>
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
            title={`${display.signal} · ${confidencePct}% confidence · ${chartTf} timeframe`}
          >
            <span
              className={cn('chart-analyst-badge__pulse size-1.5 rounded-full', isStrong && 'animate-pulse')}
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
        <PopoverContent
          align="end"
          side="bottom"
          sideOffset={8}
          collisionPadding={16}
          className="w-80 max-w-[92vw] p-3"
          style={{ borderColor: sigStyle.border }}
        >
          <PopoverHeader className="gap-1">
            <PopoverTitle className="text-[0.62rem] uppercase tracking-wide text-muted-foreground">
              Chart Analyst {display.source === 'backend' ? '· Server' : '· Local'}
              {display.version >= 2 ? ' · v2' : ''}
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

          {display.sub_reports ? (
            <div className="mb-3">
              <SubReportCards subReports={display.sub_reports} compact />
            </div>
          ) : display.reasons?.length ? (
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
            <LlmNarrativeBlock
              narrative={display.narrative}
              model={display.model}
              className="chart-analyst__narrative"
              compact
            />
          ) : null}

          {deepReasoning ? (
            <LlmDeepReasoningBlock
              summary={deepReasoning.reasoning_summary}
              riskNotes={deepReasoning.risk_notes}
              provider={deepReasoning.provider}
              model={deepReasoning.model}
              className="mb-3"
            />
          ) : null}

          {display.sub_reports && !(agentLlmEnabled && agentLlmAvailable) && (
            <LlmFeatureHint
              feature="Deep reasoning"
              enabled={agentLlmEnabled}
              available={agentLlmAvailable}
              envKeys={['AGENT_LLM_ENABLED']}
              compact
              className="mb-3"
            />
          )}

          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="default"
              size="sm"
              className="h-7 w-full gap-1 text-xs"
              onClick={handlePreviewOrder}
              disabled={!display.signal?.includes('BUY') && !display.signal?.includes('SELL')}
            >
              <ShoppingCart className="size-3" />
              Preview order
            </Button>
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
            {display.sub_reports && agentLlmEnabled && agentLlmAvailable && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 flex-1 gap-1 text-xs"
                disabled={deepReasonLoading || display.source !== 'backend'}
                onClick={handleDeepReason}
              >
                <Layers className={cn('size-3', deepReasonLoading && 'animate-spin')} />
                {deepReasonLoading ? 'Reasoning…' : 'Deep reason'}
              </Button>
            )}
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

      <InsightOrderPreviewDialog
        open={previewOpen}
        onOpenChange={setPreviewOpen}
        draft={previewDraft}
        onConfirm={() => {
          if (previewDraft) {
            setOrderPrefill(previewDraft);
            toast.message(`Order ticket prefilled (${previewDraft.side})`);
          }
          setPreviewOpen(false);
        }}
      />
    </>
  );
}
