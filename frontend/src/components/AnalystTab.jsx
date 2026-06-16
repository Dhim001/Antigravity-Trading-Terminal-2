/**
 * Chart Analyst insight history — Phase 5b dock tab.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Bot, Brain, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { fetchAgentInsights } from '../api/endpoints';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import SubReportCards from './SubReportCards';
import InsightOrderPreviewDialog from './InsightOrderPreviewDialog';
import { buildOrderDraftFromInsight } from '../lib/insightOrderDraft';
import { WidgetEmpty } from './WidgetShell';
import { cn } from '@/lib/utils';

function displayLabel(insight) {
  const score = insight?.score ?? 0;
  if (score >= 4) return 'STRONG BUY';
  if (score >= 2) return 'BUY';
  if (score <= -4) return 'STRONG SELL';
  if (score <= -2) return 'SELL';
  return 'NEUTRAL';
}

function signalVariant(insight) {
  const label = displayLabel(insight);
  if (label.includes('BUY')) return 'buy';
  if (label.includes('SELL')) return 'sell';
  return 'secondary';
}

function formatBarTime(barTime) {
  if (!barTime) return '—';
  const ms = barTime > 1e11 ? barTime : barTime * 1000;
  try {
    return new Date(ms).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return String(barTime);
  }
}

function reasonPreview(reasons) {
  if (!reasons?.length) return '—';
  const first = reasons[0];
  return reasons.length > 1 ? `${first} (+${reasons.length - 1})` : first;
}

export default function AnalystTab() {
  const activeSymbol = useStore((s) => s.activeSymbol);
  const symbolsList = useStore((s) => s.symbolsList);
  const agentInsights = useStore((s) => s.agentInsights);
  const agentInsightHistory = useStore((s) => s.agentInsightHistory);
  const setActiveSymbol = useStore((s) => s.setActiveSymbol);
  const [symbol, setSymbol] = useState(activeSymbol);
  const [loading, setLoading] = useState(false);
  const [expandedId, setExpandedId] = useState(null);
  const [previewDraft, setPreviewDraft] = useState(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const setOrderPrefill = useStore((s) => s.setOrderPrefill);
  const tickerData = useStore((s) => s.tickerData);
  const visionReports = useStore((s) => s.visionReports);
  const [visionLoading, setVisionLoading] = useState(false);

  useEffect(() => {
    setSymbol(activeSymbol);
  }, [activeSymbol]);

  const rows = useMemo(
    () => agentInsightHistory[symbol] ?? [],
    [agentInsightHistory, symbol],
  );

  const latest = agentInsights[symbol];

  const stats = useMemo(() => {
    let buy = 0;
    let sell = 0;
    let withNarrative = 0;
    for (const row of rows) {
      if (row.signal === 'BUY') buy += 1;
      if (row.signal === 'SELL') sell += 1;
      if (row.narrative) withNarrative += 1;
    }
    return { buy, sell, withNarrative };
  }, [rows]);

  const loadHistory = useCallback(async () => {
    setLoading(true);
    try {
      await fetchAgentInsights(symbol, useStore.getState(), 40);
    } catch (err) {
      toast.error(err?.message || 'Failed to load analyst history');
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const runAnalyze = () => {
    sendAction(Action.CHART_ANALYZE, { symbol, force_llm: false });
    toast.message(`Analysis requested for ${symbol}`);
  };

  const openAlgo = () => {
    window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'algo' }));
  };

  const requestVision = () => {
    setVisionLoading(true);
    const handler = (e) => {
      window.removeEventListener('chart-capture-ready', handler);
      const { image, bar_time } = e.detail || {};
      if (!image) {
        setVisionLoading(false);
        toast.error('Chart capture failed');
        return;
      }
      sendAction(Action.CHART_VISION, {
        symbol,
        timeframe: '4h',
        image_base64: image.replace(/^data:image\/png;base64,/, ''),
        bar_time: bar_time || latest?.bar_time || Math.floor(Date.now() / 1000),
      }).finally(() => setVisionLoading(false));
    };
    window.addEventListener('chart-capture-ready', handler);
    setActiveSymbol(symbol);
    window.dispatchEvent(new CustomEvent('chart-capture-request', {
      detail: { symbol, bar_time: latest?.bar_time },
    }));
    setTimeout(() => {
      window.removeEventListener('chart-capture-ready', handler);
      setVisionLoading(false);
    }, 5000);
  };

  const visionKey = `${symbol}:4h`;
  const visionReport = visionReports[visionKey];

  return (
    <div className="dock-panel-tab">
      <header className="dock-panel-tab__toolbar">
        <div className="dock-panel-tab__toolbar-lead">
          <div className="dock-panel-tab__toolbar-icon" aria-hidden>
            <Brain size={14} />
          </div>
          <div className="dock-panel-tab__toolbar-copy">
            <span className="dock-panel-tab__toolbar-title">Chart Analyst</span>
            <span className="dock-panel-tab__toolbar-subtitle num-mono">
              {rows.length} insight{rows.length === 1 ? '' : 's'}
              {rows.length > 0 && (
                <> · {stats.buy} buy · {stats.sell} sell</>
              )}
            </span>
          </div>
        </div>
        <div className="dock-panel-tab__toolbar-actions flex items-center gap-2">
          <Select value={symbol} onValueChange={setSymbol}>
            <SelectTrigger className="h-7 w-[7.5rem] text-xs" aria-label="Analyst symbol">
              <SelectValue />
            </SelectTrigger>
            <SelectContent position="popper">
              {symbolsList.map((sym) => (
                <SelectItem key={sym} value={sym} className="text-xs">
                  {sym}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            disabled={loading}
            onClick={loadHistory}
          >
            <RefreshCw className={cn('size-3', loading && 'animate-spin')} data-icon="inline-start" />
            Refresh
          </Button>
          <Button variant="secondary" size="sm" className="h-7 text-xs" onClick={runAnalyze}>
            Analyze
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            disabled={visionLoading}
            onClick={requestVision}
            title="Requires AGENT_VISION_ENABLED and OpenRouter key"
          >
            {visionLoading ? 'Capturing…' : 'Describe 4H'}
          </Button>
        </div>
      </header>

      {latest && (
        <div className="border-b border-border/50 bg-muted/20 px-3 py-2 text-xs">
          <span className="text-muted-foreground">Latest · </span>
          <Badge variant={signalVariant(latest)} className="mr-2 h-5 px-1.5 text-[0.58rem]">
            {displayLabel(latest)}
          </Badge>
          <span className="num-mono text-muted-foreground">
            {Math.round((latest.confidence ?? 0) * 100)}% conf
            {latest.score != null && (
              <> · score {latest.score > 0 ? '+' : ''}{latest.score}</>
            )}
          </span>
          {latest.narrative && (
            <p className="mt-1.5 leading-relaxed text-foreground/85">{latest.narrative}</p>
          )}
          {visionReport && (
            <div className="mt-2 rounded border border-border/50 bg-background/40 p-2">
              <p className="text-[0.58rem] font-semibold uppercase text-muted-foreground">
                Structure notes (not a signal)
              </p>
              <p className="mt-1 text-xs">{visionReport.structure}</p>
              {visionReport.patterns?.length > 0 && (
                <p className="mt-1 text-[0.62rem] text-muted-foreground">
                  {visionReport.patterns.join(' · ')}
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {rows.length === 0 ? (
        <div className="dock-panel-tab__empty">
          <WidgetEmpty
            icon={Brain}
            message={`No analyst insights for ${symbol} yet. Run Analyze on the chart badge or click Analyze above.`}
          />
          <div className="flex justify-center pb-4">
            <Button size="sm" className="h-7 text-xs" onClick={runAnalyze}>
              Analyze {symbol}
            </Button>
          </div>
        </div>
      ) : (
        <>
          <div className="dock-panel-tab__table-wrap scroll-panel-y scroll-panel-y-0">
            <table className="terminal-table dock-panel-tab__table min-w-[720px] text-[0.62rem]">
              <thead>
                <tr>
                  <th>Bar time</th>
                  <th>Signal</th>
                  <th className="text-right">Score</th>
                  <th className="text-right">Conf</th>
                  <th>Reasons</th>
                  <th>LLM</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const id = row.insight_id || `${row.symbol}:${row.bar_time}`;
                  const isExpanded = expandedId === id;
                  const isLatest = latest?.insight_id === row.insight_id;
                  return (
                    <React.Fragment key={id}>
                      <tr
                        className={cn(
                          'cursor-pointer hover:bg-muted/30',
                          isLatest && 'bg-primary/5',
                        )}
                        onClick={() => setExpandedId(isExpanded ? null : id)}
                      >
                        <td className="num-mono whitespace-nowrap">{formatBarTime(row.bar_time)}</td>
                        <td>
                          <Badge variant={signalVariant(row)} className="h-5 px-1.5 text-[0.55rem]">
                            {displayLabel(row)}
                          </Badge>
                        </td>
                        <td className="num-mono text-right">
                          {row.score > 0 ? '+' : ''}{row.score ?? 0}
                        </td>
                        <td className="num-mono text-right">
                          {Math.round((row.confidence ?? 0) * 100)}%
                        </td>
                        <td className="max-w-[200px] truncate text-muted-foreground" title={row.reasons?.join(' · ')}>
                          {reasonPreview(row.reasons)}
                        </td>
                        <td>
                          {row.narrative ? (
                            <Badge variant="outline" className="h-5 px-1 text-[0.55rem]">Yes</Badge>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr className="bg-muted/15">
                          <td colSpan={6} className="px-3 py-2 text-xs">
                            {row.sub_reports ? (
                              <div className="mb-2">
                                <SubReportCards subReports={row.sub_reports} />
                              </div>
                            ) : row.reasons?.length > 0 ? (
                              <ul className="mb-2 space-y-0.5">
                                {row.reasons.map((r, i) => (
                                  <li key={i} className="text-muted-foreground">• {r}</li>
                                ))}
                              </ul>
                            ) : null}
                            {row.narrative && (
                              <p className="rounded border border-border/50 bg-background/50 p-2 leading-relaxed">
                                {row.narrative}
                              </p>
                            )}
                            {row.levels?.stop_loss_distance != null && (
                              <p className="mt-2 num-mono text-[0.58rem] text-muted-foreground">
                                SL dist {row.levels.stop_loss_distance}
                                {row.levels.take_profit_price != null && (
                                  <> · TP {row.levels.take_profit_price}</>
                                )}
                              </p>
                            )}
                            {row.signal === 'BUY' || row.signal === 'SELL' ? (
                              <Button
                                variant="outline"
                                size="xs"
                                className="mt-2 h-6 text-[0.62rem]"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  const draft = buildOrderDraftFromInsight(row, {
                                    tickerPrice: tickerData[row.symbol]?.price,
                                  });
                                  if (draft) {
                                    setPreviewDraft(draft);
                                    setPreviewOpen(true);
                                  }
                                }}
                              >
                                Preview order
                              </Button>
                            ) : null}
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <footer className="dock-panel-tab__footer">
            <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => setActiveSymbol(symbol)}>
              View {symbol} chart
            </Button>
            <Button variant="ghost" size="sm" className="h-7 gap-1 text-xs" onClick={openAlgo}>
              <Bot className="size-3" />
              Deploy Chart Agent
            </Button>
          </footer>
        </>
      )}
      <InsightOrderPreviewDialog
        open={previewOpen}
        onOpenChange={setPreviewOpen}
        draft={previewDraft}
        onConfirm={() => {
          if (previewDraft) {
            setOrderPrefill(previewDraft);
            setActiveSymbol(previewDraft.symbol);
            toast.message('Order ticket prefilled');
          }
          setPreviewOpen(false);
        }}
      />
    </div>
  );
}
