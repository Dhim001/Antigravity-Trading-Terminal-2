import { useCallback, useEffect, useMemo, useState } from 'react';
import { Bot, Radar, RefreshCw, Search } from 'lucide-react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { sendAction, runMarketScan } from '../api/transport';
import { Action } from '../api/protocol';
import { pipelineScanDeploy } from '../api/endpoints';
import { buildOrderDraftFromInsight } from '../lib/insightOrderDraft';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { WidgetEmpty, DockScrollPanel } from './WidgetShell';
import {
  Empty,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
  EmptyDescription,
  EmptyContent,
} from '@/components/ui/empty';
import InsightOrderPreviewDialog from './InsightOrderPreviewDialog';
import { useVirtualRows, VirtualTablePadding } from './VirtualTableBody';
import { cn } from '@/lib/utils';
import { focusAnalyst, openScannerHub } from '../lib/intelligenceEvents';
import { useSettingsStore } from '../store/useSettingsStore';
import { normalizeAnalystTimeframe } from '../lib/agentInsights';
import {
  DataTableRoot,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from './DataTableShell';

function signalClass(signal) {
  if (signal === 'BUY') return 'text-trading-up';
  if (signal === 'SELL') return 'text-trading-down';
  return 'text-muted-foreground';
}

function formatScannedAt(iso) {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return null;
  }
}

export default function ScannerTab() {
  const symbolsList = useStore((s) => s.symbolsList);
  const scanResults = useStore((s) => s.scanResults);
  const setActiveSymbol = useStore((s) => s.setActiveSymbol);
  const setOrderPrefill = useStore((s) => s.setOrderPrefill);
  const agentInsights = useStore((s) => s.agentInsights);
  const tickerData = useStore((s) => s.tickerData);
  const setBotStrategy = useStore((s) => s.setBotStrategy);
  const openBacktestLab = useStore((s) => s.openBacktestLab);
  const botConfig = useStore((s) => s.botConfig);
  const botTimeframe = useStore((s) => s.botTimeframe);

  const [loading, setLoading] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [search, setSearch] = useState('');
  const [signalFilter, setSignalFilter] = useState('any');
  const [previewDraft, setPreviewDraft] = useState(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const chartTf = useSettingsStore((s) => s.settings.chartLayout?.timeframe || '1m');
  const analysisTf = normalizeAnalystTimeframe(chartTf);

  const allRows = scanResults?.rows ?? [];

  const rows = useMemo(() => {
    const q = search.trim().toUpperCase();
    return allRows.filter((r) => {
      if (signalFilter !== 'any' && r.signal !== signalFilter) return false;
      if (q && !r.symbol.includes(q)) return false;
      return true;
    });
  }, [allRows, search, signalFilter]);

  const { onScroll: onScanScroll, window: scanWindow } = useVirtualRows(rows, {
    rowHeight: 32,
    overscan: 10,
  });

  const stats = useMemo(() => {
    let buy = 0;
    let sell = 0;
    let neutral = 0;
    for (const row of allRows) {
      if (row.signal === 'BUY') buy += 1;
      else if (row.signal === 'SELL') sell += 1;
      else neutral += 1;
    }
    return { buy, sell, neutral, total: allRows.length };
  }, [allRows]);

  const runScan = useCallback(async () => {
    const symbols = [...new Set((symbolsList || []).filter(Boolean))];
    if (symbols.length === 0) {
      toast.error('Watchlist is empty — add symbols before scanning');
      return;
    }

    setLoading(true);
    try {
      const data = await runMarketScan({
        symbols,
        signal_filter: 'any',
        sort_by: 'score',
      });
      const count = data?.count ?? data?.rows?.length ?? 0;
      if (count === 0) {
        toast.message('Scan complete — no symbols had enough candle history');
      } else {
        toast.success(`Scanned ${count} symbol${count === 1 ? '' : 's'}`);
      }
    } catch (err) {
      toast.error(err?.message || 'Scan failed');
    } finally {
      setLoading(false);
    }
  }, [symbolsList]);

  const deployTopBots = useCallback(async (dryRun = false) => {
    const symbols = [...new Set((symbolsList || []).filter(Boolean))];
    if (symbols.length === 0) {
      toast.error('Watchlist is empty');
      return;
    }
    setDeploying(true);
    try {
      const result = await pipelineScanDeploy({
        symbols,
        maxDeploy: 3,
        minConfidence: 0.6,
        minScore: 2,
        allocation: botConfig?.allocation ?? 1000,
        timeframe: botTimeframe || analysisTf,
        dryRun,
        config: botConfig,
      });
      const n = result?.deployed?.length ?? 0;
      if (dryRun) {
        toast.message(`Dry run: would deploy ${n} bot(s) from ${result?.candidates ?? 0} candidates`);
      } else if (n > 0) {
        toast.success(`Deployed ${n} CHART_AGENT bot(s) from scan`);
      } else {
        toast.message(result?.skipped?.[0]?.reason || 'No symbols met deploy criteria');
      }
    } catch (err) {
      toast.error(err?.message || 'Pipeline deploy failed');
    } finally {
      setDeploying(false);
    }
  }, [symbolsList, botConfig, botTimeframe, analysisTf]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const id = setInterval(() => {
      runScan();
    }, 60_000);
    return () => clearInterval(id);
  }, [autoRefresh, runScan]);

  const onRowClick = (row) => {
    if (!agentInsights[row.symbol]) {
      sendAction(Action.CHART_ANALYZE, { symbol: row.symbol, timeframe: analysisTf });
    }
    focusAnalyst({ symbol: row.symbol, expandLatest: true, openHub: false });
  };

  const onOpenInHub = (row) => {
    setActiveSymbol(row.symbol);
    if (!agentInsights[row.symbol]) {
      sendAction(Action.CHART_ANALYZE, { symbol: row.symbol, timeframe: analysisTf });
    }
    focusAnalyst({ symbol: row.symbol, expandLatest: true, openHub: true });
  };

  const onPreview = (row) => {
    const insight = agentInsights[row.symbol] || {
      symbol: row.symbol,
      signal: row.signal,
      confidence: row.confidence,
      levels: {},
      insight_id: row.insight_id,
      sub_reports: row.atr_regime
        ? { risk: { atr_regime: row.atr_regime, suggested_size_factor: 1 } }
        : null,
    };
    const draft = buildOrderDraftFromInsight(insight, { tickerPrice: tickerData[row.symbol]?.price });
    if (!draft) {
      toast.message('No actionable signal to preview');
      return;
    }
    setPreviewDraft(draft);
    setPreviewOpen(true);
  };

  const openAlgoForSymbol = (row, tab = 'results') => {
    setActiveSymbol(row.symbol);
    setBotStrategy('CHART_AGENT');
    window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'algo' }));
    openBacktestLab(tab);
    toast.message(`Algo opened for ${row.symbol}`);
  };

  const scannedLabel = formatScannedAt(scanResults?.scanned_at);

  return (
    <div className="dock-panel-tab">
      <header className="dock-panel-tab__toolbar">
        <div className="dock-panel-tab__toolbar-lead">
          <div className="dock-panel-tab__toolbar-icon" aria-hidden>
            <Radar size={14} />
          </div>
          <div className="dock-panel-tab__toolbar-copy">
            <span className="dock-panel-tab__toolbar-title">Market Scanner</span>
            <span className="dock-panel-tab__toolbar-subtitle num-mono">
              {stats.total > 0
                ? `${stats.total} ranked · ${stats.buy}B / ${stats.sell}S / ${stats.neutral}N`
                : 'Rank watchlist by analyst score'}
            </span>
          </div>
        </div>
        <div className="dock-panel-tab__toolbar-actions flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            className="h-7 gap-1 text-xs"
            disabled={loading}
            onClick={runScan}
          >
            {loading ? <RefreshCw className="size-3 animate-spin" /> : <Radar className="size-3" />}
            Scan watchlist
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1 text-xs"
            disabled={deploying || loading}
            onClick={() => deployTopBots(false)}
            title="Deploy up to 3 CHART_AGENT bots for top actionable scan signals"
          >
            {deploying ? <RefreshCw className="size-3 animate-spin" /> : <Bot className="size-3" />}
            Deploy top 3
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={openScannerHub}
            title="Open full scanner workspace (⌘I)"
          >
            Open Hub
          </Button>
          <Button
            variant={autoRefresh ? 'secondary' : 'outline'}
            size="sm"
            className="h-7 text-xs"
            onClick={() => setAutoRefresh((v) => !v)}
            title="Re-scan every 60s (respects 30s server limit)"
          >
            Auto {autoRefresh ? 'on' : 'off'}
          </Button>
          <Select value={signalFilter} onValueChange={setSignalFilter}>
            <SelectTrigger className="h-7 w-[120px] text-xs" aria-label="Signal filter">
              <SelectValue placeholder="Signal" />
            </SelectTrigger>
            <SelectContent position="popper">
              <SelectItem value="any">Any signal</SelectItem>
              <SelectItem value="BUY">BUY only</SelectItem>
              <SelectItem value="SELL">SELL only</SelectItem>
              <SelectItem value="NONE">Neutral</SelectItem>
            </SelectContent>
          </Select>
          <div className="relative min-w-[120px] flex-1">
            <Search className="absolute left-2 top-1/2 size-3 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="h-7 pl-7 text-xs"
              placeholder="Filter symbol…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
      </header>

      {rows.length === 0 ? (
        <div className="dock-panel-tab__empty">
          <Empty className="border-none bg-transparent py-6">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Radar />
              </EmptyMedia>
              <EmptyTitle>
                {allRows.length > 0 ? 'No matches' : 'No scan results yet'}
              </EmptyTitle>
              <EmptyDescription>
                {allRows.length > 0
                  ? 'No rows match the current filter.'
                  : 'Run a scan to rank watchlist symbols by analyst score.'}
              </EmptyDescription>
            </EmptyHeader>
            {allRows.length === 0 && (
              <EmptyContent>
                <Button size="sm" disabled={loading} onClick={runScan}>
                  Scan watchlist
                </Button>
              </EmptyContent>
            )}
          </Empty>
        </div>
      ) : (
        <>
          <DockScrollPanel onScroll={onScanScroll}>
            <DataTableRoot variant="dock" className="dock-panel-tab__table min-w-[640px] w-full text-xs">
              <caption className="sr-only">Market scanner ranked by analyst score</caption>
              <DataTableHeader>
                <tr className="border-b border-border hover:bg-transparent">
                  <DataTableHead scope="col">Symbol</DataTableHead>
                  <DataTableHead scope="col">Signal</DataTableHead>
                  <DataTableHead scope="col" align="right">Score</DataTableHead>
                  <DataTableHead scope="col" align="right">Conf.</DataTableHead>
                  <DataTableHead scope="col" align="right">RSI</DataTableHead>
                  <DataTableHead scope="col">MACD</DataTableHead>
                  <DataTableHead scope="col">ATR</DataTableHead>
                  <DataTableHead scope="col" align="right"><span className="sr-only">Actions</span></DataTableHead>
                </tr>
              </DataTableHeader>
              <DataTableBody>
                <VirtualTablePadding height={scanWindow.topPad} colSpan={8} />
                {scanWindow.slice.map((row) => (
                  <DataTableRow
                    key={row.insight_id || row.symbol}
                    rowVariant="dock"
                    deferred
                    className="cursor-pointer"
                    onClick={() => onRowClick(row)}
                  >
                    <DataTableCell className="font-semibold">{row.symbol.replace('USDT', '')}</DataTableCell>
                    <DataTableCell>
                      <Badge
                        variant={row.signal === 'BUY' ? 'buy' : row.signal === 'SELL' ? 'sell' : 'secondary'}
                        className="h-5 text-[0.62rem] font-bold"
                      >
                        {row.signal}
                      </Badge>
                    </DataTableCell>
                    <DataTableCell numeric align="right" className={cn('font-semibold', signalClass(row.signal))}>
                      {row.score > 0 ? '+' : ''}{row.score}
                    </DataTableCell>
                    <DataTableCell numeric align="right" className="text-muted-foreground">
                      {row.confidence != null ? `${Math.round(row.confidence * 100)}%` : '—'}
                    </DataTableCell>
                    <DataTableCell numeric align="right">{row.rsi ?? '—'}</DataTableCell>
                    <DataTableCell className="capitalize text-muted-foreground">{row.macd_cross ?? '—'}</DataTableCell>
                    <DataTableCell className="capitalize text-muted-foreground">{row.atr_regime ?? '—'}</DataTableCell>
                    <DataTableCell align="right" className="whitespace-nowrap">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => { e.stopPropagation(); onOpenInHub(row); }}
                      >
                        Analyst
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => { e.stopPropagation(); openAlgoForSymbol(row, 'results'); }}
                      >
                        Backtest
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => { e.stopPropagation(); openAlgoForSymbol(row, 'optimizer'); }}
                      >
                        Optimize
                      </Button>
                      {row.signal !== 'NONE' && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => { e.stopPropagation(); onPreview(row); }}
                        >
                          Preview
                        </Button>
                      )}
                      {row.signal !== 'NONE' && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={deploying}
                          onClick={async (e) => {
                            e.stopPropagation();
                            setDeploying(true);
                            try {
                              const result = await pipelineScanDeploy({
                                symbols: [row.symbol],
                                maxDeploy: 1,
                                minConfidence: 0.55,
                                minScore: 2,
                                allocation: botConfig?.allocation ?? 1000,
                                timeframe: botTimeframe || analysisTf,
                                config: botConfig,
                              });
                              if (result?.deployed?.length) {
                                toast.success(`Deployed bot for ${row.symbol}`);
                              } else {
                                toast.message(result?.skipped?.[0]?.reason || 'Deploy skipped');
                              }
                            } catch (err) {
                              toast.error(err?.message || 'Deploy failed');
                            } finally {
                              setDeploying(false);
                            }
                          }}
                        >
                          Bot
                        </Button>
                      )}
                    </DataTableCell>
                  </DataTableRow>
                ))}
                <VirtualTablePadding height={scanWindow.bottomPad} colSpan={8} />
              </DataTableBody>
            </DataTableRoot>
          </DockScrollPanel>

          <footer className="dock-panel-tab__footer">
            <span>
              Showing {rows.length} of {stats.total}
              {scannedLabel && <> · scanned {scannedLabel}</>}
            </span>
            <span className="dock-panel-tab__footer-highlight">
              Actionable:{' '}
              <span className="num-mono font-bold text-trading-up">{stats.buy}</span>
              {' buy · '}
              <span className="num-mono font-bold text-trading-down">{stats.sell}</span>
              {' sell'}
            </span>
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
