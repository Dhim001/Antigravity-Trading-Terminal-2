import { useCallback, useEffect, useMemo, useState } from 'react';
import { Radar, RefreshCw, Search } from 'lucide-react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { sendAction, runMarketScan } from '../api/transport';
import { Action } from '../api/protocol';
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
import { WidgetEmpty } from './WidgetShell';
import InsightOrderPreviewDialog from './InsightOrderPreviewDialog';
import { cn } from '@/lib/utils';

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

  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [signalFilter, setSignalFilter] = useState('any');
  const [previewDraft, setPreviewDraft] = useState(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const allRows = scanResults?.rows ?? [];

  const rows = useMemo(() => {
    const q = search.trim().toUpperCase();
    return allRows.filter((r) => {
      if (signalFilter !== 'any' && r.signal !== signalFilter) return false;
      if (q && !r.symbol.includes(q)) return false;
      return true;
    });
  }, [allRows, search, signalFilter]);

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

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const id = setInterval(() => {
      runScan();
    }, 60_000);
    return () => clearInterval(id);
  }, [autoRefresh, runScan]);

  const onRowClick = (row) => {
    setActiveSymbol(row.symbol);
    if (!agentInsights[row.symbol]) {
      sendAction(Action.CHART_ANALYZE, { symbol: row.symbol });
    }
    window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'analyst' }));
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
          <WidgetEmpty
            icon={Radar}
            message={
              allRows.length > 0
                ? 'No rows match the current filter.'
                : 'No scan results yet. Run a scan to rank symbols by analyst score.'
            }
          />
          {allRows.length === 0 && (
            <div className="flex justify-center pb-4">
              <Button size="sm" className="h-7 text-xs" disabled={loading} onClick={runScan}>
                Scan watchlist
              </Button>
            </div>
          )}
        </div>
      ) : (
        <>
          <div className="dock-panel-tab__table-wrap scroll-panel-y scroll-panel-y-0">
            <table className="terminal-table dock-panel-tab__table min-w-[640px] w-full text-xs">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Signal</th>
                  <th className="text-right">Score</th>
                  <th className="text-right">Conf.</th>
                  <th className="text-right">RSI</th>
                  <th>MACD</th>
                  <th>ATR</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.insight_id || row.symbol}
                    className="cursor-pointer hover:bg-muted/40"
                    onClick={() => onRowClick(row)}
                  >
                    <td className="font-semibold">{row.symbol.replace('USDT', '')}</td>
                    <td>
                      <Badge
                        variant={row.signal === 'BUY' ? 'buy' : row.signal === 'SELL' ? 'sell' : 'secondary'}
                        className="h-5 text-[0.62rem] font-bold"
                      >
                        {row.signal}
                      </Badge>
                    </td>
                    <td className={cn('num-mono text-right font-semibold', signalClass(row.signal))}>
                      {row.score > 0 ? '+' : ''}{row.score}
                    </td>
                    <td className="num-mono text-right text-muted-foreground">
                      {row.confidence != null ? `${Math.round(row.confidence * 100)}%` : '—'}
                    </td>
                    <td className="num-mono text-right">{row.rsi ?? '—'}</td>
                    <td className="capitalize text-muted-foreground">{row.macd_cross ?? '—'}</td>
                    <td className="capitalize text-muted-foreground">{row.atr_regime ?? '—'}</td>
                    <td className="text-right">
                      {row.signal !== 'NONE' && (
                        <Button
                          variant="ghost"
                          size="xs"
                          className="h-6 text-[0.62rem]"
                          onClick={(e) => { e.stopPropagation(); onPreview(row); }}
                        >
                          Preview
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

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
