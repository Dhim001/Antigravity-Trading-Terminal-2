/**
 * Chart Analyst insight history — Phase 5b dock tab.
 */
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Bot, Brain, RefreshCw, GitCompare } from 'lucide-react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
import { selectAgentInsight, normalizeAnalystTimeframe } from '../lib/agentInsights';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { fetchAgentInsights, withLlmModel } from '../api/endpoints';
import { useWindowedRows } from '../hooks/useWindowedRows';
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
import LlmNarrativeBlock from './LlmNarrativeBlock';
import LlmDeepReasoningBlock from './LlmDeepReasoningBlock';
import LlmFeatureHint from './LlmFeatureHint';
import LlmAttribution from './LlmAttribution';
import InsightOrderPreviewDialog from './InsightOrderPreviewDialog';
import { buildOrderDraftFromInsight } from '../lib/insightOrderDraft';
import { DockScrollPanel } from './WidgetShell';
import {
  DataTableRoot,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from './DataTableShell';
import {
  Empty,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
  EmptyDescription,
  EmptyContent,
} from '@/components/ui/empty';
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
  const [deepReasonLoading, setDeepReasonLoading] = useState(null);
  const agentLlmAvailable = useStore((s) => s.agentLlmAvailable);
  const agentLlmEnabled = useStore((s) => s.agentLlmEnabled);
  const agentVisionEnabled = useStore((s) => s.agentVisionEnabled);
  const agentDeepReasoning = useStore((s) => s.agentDeepReasoning);
  const [visionTf, setVisionTf] = useState('4h');
  const [compareMode, setCompareMode] = useState(false);
  const [compareSymbol, setCompareSymbol] = useState(
    () => symbolsList.find((s) => s !== activeSymbol) ?? symbolsList[0] ?? '',
  );
  const chartTf = useSettingsStore((s) => s.settings.chartLayout?.timeframe || '1m');
  const analysisTf = normalizeAnalystTimeframe(chartTf);

  useEffect(() => {
    setSymbol(activeSymbol);
  }, [activeSymbol]);

  useEffect(() => {
    const onFocus = (e) => {
      const { symbol: sym, expandLatest, preview } = e.detail || {};
      if (sym) {
        setSymbol(sym);
        setActiveSymbol(sym);
      }
      if (expandLatest) {
        const history = useStore.getState().agentInsightHistory[sym || symbol] ?? [];
        const first = history[0];
        if (first) {
          setExpandedId(first.insight_id || `${first.symbol}:${first.bar_time}`);
        }
      }
      if (preview && sym) {
        const row = (useStore.getState().agentInsightHistory[sym] ?? [])[0];
        if (row && (row.signal === 'BUY' || row.signal === 'SELL')) {
          const draft = buildOrderDraftFromInsight(row, {
            tickerPrice: useStore.getState().tickerData[sym]?.price,
          });
          if (draft) {
            setPreviewDraft(draft);
            setPreviewOpen(true);
          }
        }
      }
    };
    window.addEventListener('analyst-focus', onFocus);
    return () => window.removeEventListener('analyst-focus', onFocus);
  }, [setActiveSymbol, symbol]);

  useEffect(() => {
    if (compareSymbol === symbol) {
      const alt = symbolsList.find((s) => s !== symbol);
      if (alt) setCompareSymbol(alt);
    }
  }, [symbol, compareSymbol, symbolsList]);

  const rows = useMemo(
    () => agentInsightHistory[symbol] ?? [],
    [agentInsightHistory, symbol],
  );

  const latest = selectAgentInsight(agentInsights, symbol, analysisTf);

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

  const { onScroll, window: rowWindow } = useWindowedRows(rows, { rowHeight: 36 });

  const loadHistory = useCallback(async (signal) => {
    setLoading(true);
    try {
      await fetchAgentInsights(symbol, useStore.getState(), 40, analysisTf, { signal });
    } catch (err) {
      if (err?.name === 'AbortError') return;
      toast.error(err?.message || 'Failed to load analyst history');
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [symbol, analysisTf]);

  useEffect(() => {
    const ac = new AbortController();
    loadHistory(ac.signal);
    return () => ac.abort();
  }, [loadHistory]);

  const runAnalyze = () => {
    sendAction(Action.CHART_ANALYZE, withLlmModel({ symbol, timeframe: analysisTf, force_llm: false }));
    toast.message(`Analysis requested for ${symbol} (${chartTf})`);
  };

  const runDeepReason = (row) => {
    const id = row.insight_id || `${row.symbol}:${row.bar_time}`;
    setDeepReasonLoading(id);
    sendAction(Action.CHART_DEEP_REASON, withLlmModel({
      symbol,
      timeframe: analysisTf,
      insight_id: row.insight_id,
    })).finally(() => setDeepReasonLoading(null));
    toast.message('Deep reasoning requested…');
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
        timeframe: visionTf,
        image_base64: image.replace(/^data:image\/png;base64,/, ''),
        bar_time: bar_time || latest?.bar_time || Math.floor(Date.now() / 1000),
      }).finally(() => setVisionLoading(false));
    };
    window.addEventListener('chart-capture-ready', handler);
    setActiveSymbol(symbol);
    window.dispatchEvent(new CustomEvent('chart-capture-request', {
      detail: { symbol, bar_time: latest?.bar_time, timeframe: visionTf },
    }));
    setTimeout(() => {
      window.removeEventListener('chart-capture-ready', handler);
      setVisionLoading(false);
    }, 5000);
  };

  const visionKey = `${symbol}:${visionTf}`;
  const visionReport = visionReports[visionKey];
  const visionLabel = visionTf === '1h' ? '1H' : '4H';

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
              {rows.length} insight{rows.length === 1 ? '' : 's'} · {chartTf}
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
          <Button
            variant={compareMode ? 'secondary' : 'outline'}
            size="sm"
            className="h-7 text-xs"
            onClick={() => setCompareMode((v) => !v)}
          >
            <GitCompare className="size-3" data-icon="inline-start" />
            Compare
          </Button>
          <Button variant="secondary" size="sm" className="h-7 text-xs" onClick={runAnalyze}>
            Analyze
          </Button>
          <Select value={visionTf} onValueChange={setVisionTf}>
            <SelectTrigger className="h-7 w-[4.5rem] text-xs" aria-label="Vision timeframe">
              <SelectValue />
            </SelectTrigger>
            <SelectContent position="popper">
              <SelectItem value="1h" className="text-xs">1H</SelectItem>
              <SelectItem value="4h" className="text-xs">4H</SelectItem>
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            disabled={visionLoading || !agentVisionEnabled}
            onClick={requestVision}
            title={
              agentVisionEnabled
                ? `Describe ${visionLabel} structure (on-demand; not a signal)`
                : 'Requires AGENT_VISION_ENABLED=true and OPENROUTER_API_KEY'
            }
          >
            {visionLoading ? 'Capturing…' : `Describe ${visionLabel}`}
          </Button>
          {(!agentVisionEnabled || !agentLlmEnabled || !agentLlmAvailable) && (
            <LlmFeatureHint
              feature="Chart vision"
              enabled={agentVisionEnabled && agentLlmEnabled}
              available={agentLlmAvailable}
              envKeys={['AGENT_VISION_ENABLED', 'AGENT_LLM_ENABLED']}
              compact
              className="max-w-xs"
            />
          )}
        </div>
      </header>

      {compareMode && (
        <div className="grid gap-2 border-b border-border/50 bg-muted/10 px-3 py-2 sm:grid-cols-2">
          {[symbol, compareSymbol].map((sym) => {
            const insight = selectAgentInsight(agentInsights, sym, analysisTf)
              || (agentInsightHistory[sym] ?? []).find(
                (row) => normalizeAnalystTimeframe(row.timeframe) === analysisTf,
              )
              || (agentInsightHistory[sym] ?? [])[0];
            return (
              <div key={sym} className="rounded border border-border/50 p-2 text-xs">
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-bold">{sym}</span>
                  {insight ? (
                    <Badge variant={signalVariant(insight)} className="h-5 text-xs">
                      {displayLabel(insight)}
                    </Badge>
                  ) : (
                    <span className="text-muted-foreground">No insight</span>
                  )}
                </div>
                {insight?.sub_reports ? (
                  <SubReportCards subReports={insight.sub_reports} compact />
                ) : insight?.reasons?.[0] ? (
                  <p className="text-muted-foreground">{insight.reasons[0]}</p>
                ) : null}
              </div>
            );
          })}
          <div className="sm:col-span-2 flex justify-end">
            <Select value={compareSymbol} onValueChange={setCompareSymbol}>
              <SelectTrigger className="h-7 w-[8rem] text-xs">
                <SelectValue placeholder="Compare with" />
              </SelectTrigger>
              <SelectContent position="popper">
                {symbolsList.filter((s) => s !== symbol).map((sym) => (
                  <SelectItem key={sym} value={sym} className="text-xs">{sym}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      )}

      {latest && !compareMode && (
        <div className="border-b border-border/50 bg-muted/20 px-3 py-2 text-xs">
          <span className="text-muted-foreground">Latest · </span>
          <Badge variant={signalVariant(latest)} className="mr-2 h-5 px-1.5 text-xs">
            {displayLabel(latest)}
          </Badge>
          <span className="num-mono text-muted-foreground">
            {Math.round((latest.confidence ?? 0) * 100)}% conf
            {latest.score != null && (
              <> · score {latest.score > 0 ? '+' : ''}{latest.score}</>
            )}
          </span>
          {latest.narrative && (
            <LlmNarrativeBlock
              narrative={latest.narrative}
              model={latest.model}
              className="analyst-latest__narrative"
              compact
            />
          )}
          {visionReport && (
            <div className="mt-2 rounded border border-border/50 bg-background/40 p-2">
              <p className="text-xs font-semibold uppercase text-muted-foreground">
                Structure notes ({visionLabel} · not a signal)
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
          <Empty className="border-none bg-transparent py-6">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Brain />
              </EmptyMedia>
              <EmptyTitle>
                {loading ? 'Loading insights…' : 'No insights yet'}
              </EmptyTitle>
              <EmptyDescription>
                {loading
                  ? `Fetching ${chartTf} insight history for ${symbol}.`
                  : `No ${chartTf} insights for ${symbol} yet. Run Analyze or wait for the next closed bar.`}
              </EmptyDescription>
            </EmptyHeader>
            {!loading && (
              <EmptyContent>
                <Button size="sm" onClick={runAnalyze}>
                  Analyze {symbol}
                </Button>
              </EmptyContent>
            )}
          </Empty>
        </div>
      ) : (
        <>
          <DockScrollPanel onScroll={onScroll}>
            <DataTableRoot variant="dock" className="dock-panel-tab__table min-w-[720px] text-[0.62rem]">
              <caption className="sr-only">Chart analyst insight history for {symbol} at {chartTf}</caption>
              <DataTableHeader>
                <tr>
                  <DataTableHead>Bar time</DataTableHead>
                  <DataTableHead>Signal</DataTableHead>
                  <DataTableHead align="right">Score</DataTableHead>
                  <DataTableHead align="right">Conf</DataTableHead>
                  <DataTableHead>Reasons</DataTableHead>
                  <DataTableHead>LLM</DataTableHead>
                </tr>
              </DataTableHeader>
              <DataTableBody>
                {rowWindow.topPad > 0 && (
                  <tr aria-hidden><td colSpan={6} style={{ height: rowWindow.topPad, padding: 0, border: 0 }} /></tr>
                )}
                {rowWindow.slice.map((row) => {
                  const id = row.insight_id || `${row.symbol}:${row.bar_time}`;
                  const isExpanded = expandedId === id;
                  const isLatest = latest?.insight_id === row.insight_id;
                  return (
                    <Fragment key={id}>
                      <DataTableRow
                        rowVariant="dock"
                        deferred
                        className={cn('cursor-pointer', isLatest && 'row-active')}
                        onClick={() => setExpandedId(isExpanded ? null : id)}
                      >
                        <DataTableCell className="num-mono whitespace-nowrap">{formatBarTime(row.bar_time)}</DataTableCell>
                        <DataTableCell>
                          <Badge variant={signalVariant(row)} className="h-5 px-1.5 text-[0.55rem]">
                            {displayLabel(row)}
                          </Badge>
                        </DataTableCell>
                        <DataTableCell numeric align="right">
                          {row.score > 0 ? '+' : ''}{row.score ?? 0}
                        </DataTableCell>
                        <DataTableCell numeric align="right">
                          {Math.round((row.confidence ?? 0) * 100)}%
                        </DataTableCell>
                        <DataTableCell className="max-w-[200px] truncate text-muted-foreground" title={row.reasons?.join(' · ')}>
                          {reasonPreview(row.reasons)}
                        </DataTableCell>
                        <DataTableCell>
                          {row.narrative ? (
                            <LlmAttribution model={row.model} variant="chip" />
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </DataTableCell>
                      </DataTableRow>
                      {isExpanded && (
                        <DataTableRow rowVariant="dock" className="bg-muted/15 hover:bg-muted/15">
                          <DataTableCell colSpan={6} className="px-3 py-2 text-xs">
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
                              <LlmNarrativeBlock
                                narrative={row.narrative}
                                model={row.model}
                                className="analyst-row__narrative"
                                compact
                              />
                            )}
                            {(agentDeepReasoning[id]?.deep_reasoning || row.deep_reasoning) && (
                              <LlmDeepReasoningBlock
                                className="mt-2"
                                summary={
                                  agentDeepReasoning[id]?.deep_reasoning?.reasoning_summary
                                  || row.deep_reasoning?.reasoning_summary
                                }
                                riskNotes={
                                  agentDeepReasoning[id]?.deep_reasoning?.risk_notes
                                  || row.deep_reasoning?.risk_notes
                                }
                                provider={
                                  agentDeepReasoning[id]?.deep_reasoning?.provider
                                  || row.deep_reasoning?.provider
                                }
                                model={
                                  agentDeepReasoning[id]?.deep_reasoning?.model
                                  || row.deep_reasoning?.model
                                }
                              />
                            )}
                            {row.sub_reports && (
                              agentLlmEnabled && agentLlmAvailable ? (
                                <Button
                                  variant="ghost"
                                  size="xs"
                                  className="mt-2 h-6 text-[0.62rem]"
                                  disabled={deepReasonLoading === id}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    runDeepReason(row);
                                  }}
                                >
                                  {deepReasonLoading === id ? 'Reasoning…' : 'Deep reasoning'}
                                </Button>
                              ) : (
                                <LlmFeatureHint
                                  feature="Deep reasoning"
                                  enabled={agentLlmEnabled}
                                  available={agentLlmAvailable}
                                  envKeys={['AGENT_LLM_ENABLED']}
                                  compact
                                  className="mt-2"
                                />
                              )
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
                          </DataTableCell>
                        </DataTableRow>
                      )}
                    </Fragment>
                  );
                })}
                {rowWindow.bottomPad > 0 && (
                  <tr aria-hidden><td colSpan={6} style={{ height: rowWindow.bottomPad, padding: 0, border: 0 }} /></tr>
                )}
              </DataTableBody>
            </DataTableRoot>
          </DockScrollPanel>
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
