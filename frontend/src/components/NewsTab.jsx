/**
 * News tab — symbol headlines + Alpaca market movers / top headlines.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ExternalLink, Newspaper, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { fetchMarketNews, fetchSymbolNews } from '../api/endpoints';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import ChartSymbolSwitcher from './chart/ChartSymbolSwitcher';
import { WidgetEmpty, DockScrollPanel } from './WidgetShell';
import VirtualScrollList from './VirtualScrollList';
import { cn } from '@/lib/utils';

function formatPublished(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return String(iso);
  }
}

function scoreTone(score) {
  if (score == null || Number.isNaN(score)) return 'text-muted-foreground';
  if (score >= 0.15) return 'text-trading-up';
  if (score <= -0.15) return 'text-trading-down';
  return 'text-muted-foreground';
}

function scoreLabel(score) {
  if (score == null) return 'Neutral';
  if (score >= 0.15) return 'Bullish';
  if (score <= -0.15) return 'Bearish';
  return 'Neutral';
}

function sentimentVariant(score) {
  if (score == null || Number.isNaN(score)) return 'neutral';
  if (score >= 0.15) return 'bullish';
  if (score <= -0.15) return 'bearish';
  return 'neutral';
}

function formatPct(pct) {
  if (pct == null || Number.isNaN(Number(pct))) return '—';
  const n = Number(pct);
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}%`;
}

function NewsItem({ item, onSymbolClick }) {
  const tone = scoreTone(item.score);
  const variant = sentimentVariant(item.score);
  const related = (item.related_symbols || []).slice(0, 4);
  const content = (
    <>
      <div className="news-feed__item-head">
        <span className="news-feed__source">
          {item.source_label || item.source}
          {item.symbol && item.symbol !== 'MARKET' ? ` · ${item.symbol}` : ''}
        </span>
        <span className={cn('news-feed__score', `news-feed__score--${variant}`, tone)}>
          {scoreLabel(item.score)}
          {item.score != null ? ` (${item.score >= 0 ? '+' : ''}${Number(item.score).toFixed(2)})` : ''}
        </span>
      </div>
      <p className="news-feed__headline">{item.headline}</p>
      {item.summary && (
        <p className="news-feed__summary">{item.summary}</p>
      )}
      {(related.length > 0 || item.published_at) && (
        <p className="news-feed__meta">
          {related.length > 0 && (
            <span className="news-feed__related">
              {related.map((sym) => (
                <button
                  key={sym}
                  type="button"
                  className="news-feed__chip"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    onSymbolClick?.(sym);
                  }}
                >
                  {sym}
                </button>
              ))}
            </span>
          )}
          {formatPublished(item.published_at)}
        </p>
      )}
    </>
  );

  const itemClass = cn(
    'news-feed__item',
    `news-feed__item--${variant}`,
    item.url && 'news-feed__item--link',
  );

  if (item.url) {
    return (
      <a
        href={item.url}
        target="_blank"
        rel="noopener noreferrer"
        className={itemClass}
      >
        {content}
        <ExternalLink className="news-feed__external" aria-hidden />
      </a>
    );
  }

  return <article className={itemClass}>{content}</article>;
}

function MoverChip({ row, onClick }) {
  const pct = Number(row.percent_change);
  const up = pct >= 0;
  const label = row.terminal_symbol || row.symbol;
  return (
    <button
      type="button"
      className={cn('news-feed__mover', up ? 'news-feed__mover--up' : 'news-feed__mover--down')}
      title={`${row.symbol} · ${formatPct(pct)}`}
      onClick={() => onClick?.(row)}
    >
      <span className="news-feed__mover-sym">{label}</span>
      <span className="news-feed__mover-pct">{formatPct(pct)}</span>
    </button>
  );
}

function MoversStrip({ movers, onPick }) {
  const stocks = movers?.stocks || {};
  const crypto = movers?.crypto || {};
  const gainers = [...(stocks.gainers || []).slice(0, 4), ...(crypto.gainers || []).slice(0, 3)];
  const losers = [...(stocks.losers || []).slice(0, 4), ...(crypto.losers || []).slice(0, 3)];
  const actives = (movers?.most_actives || []).slice(0, 5);

  if (!gainers.length && !losers.length && !actives.length) return null;

  return (
    <div className="news-feed__movers">
      {gainers.length > 0 && (
        <div className="news-feed__movers-row">
          <span className="news-feed__movers-label news-feed__movers-label--up">Gainers</span>
          <div className="news-feed__movers-chips">
            {gainers.map((row) => (
              <MoverChip key={`g-${row.symbol}`} row={row} onClick={onPick} />
            ))}
          </div>
        </div>
      )}
      {losers.length > 0 && (
        <div className="news-feed__movers-row">
          <span className="news-feed__movers-label news-feed__movers-label--down">Losers</span>
          <div className="news-feed__movers-chips">
            {losers.map((row) => (
              <MoverChip key={`l-${row.symbol}`} row={row} onClick={onPick} />
            ))}
          </div>
        </div>
      )}
      {actives.length > 0 && (
        <div className="news-feed__movers-row">
          <span className="news-feed__movers-label">Actives</span>
          <div className="news-feed__movers-chips">
            {actives.map((row) => (
              <button
                key={`a-${row.symbol}`}
                type="button"
                className="news-feed__mover news-feed__mover--flat"
                onClick={() => onPick?.(row)}
              >
                <span className="news-feed__mover-sym">{row.terminal_symbol || row.symbol}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function NewsTab() {
  const activeSymbol = useStore((s) => s.activeSymbol);
  const terminalMode = useStore((s) => s.terminalMode);
  const setActiveSymbol = useStore((s) => s.setActiveSymbol);
  const [scope, setScope] = useState(
    terminalMode === 'LIVE_ALPACA' ? 'market' : 'symbol',
  );
  const [symbol, setSymbol] = useState(activeSymbol);
  const [filter, setFilter] = useState('all');
  const [loading, setLoading] = useState(false);
  const [feed, setFeed] = useState(null);
  const [error, setError] = useState(null);
  const loadSeq = useRef(0);

  useEffect(() => {
    setSymbol(activeSymbol);
  }, [activeSymbol]);

  const loadNews = useCallback(async (nextScope, sym, refresh = true) => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setError(null);
    try {
      let body;
      if (nextScope === 'market') {
        body = await fetchMarketNews({ limit: 50, top: 10, lookbackHours: 72 });
      } else {
        const target = String(sym || '').toUpperCase().trim();
        if (!target) return;
        body = await fetchSymbolNews(target, { refresh, limit: 50, lookbackHours: 72 });
      }
      if (seq !== loadSeq.current) return;
      if (!body?.ok) {
        throw new Error(body?.error || 'Failed to load news');
      }
      setFeed(body.news);
    } catch (e) {
      if (seq !== loadSeq.current) return;
      setError(e.message || 'Failed to load news');
      toast.error(e.message || 'Failed to load news');
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    setFeed(null);
    loadNews(scope, symbol, true);
  }, [scope, symbol, loadNews]);

  useEffect(() => {
    const onFocus = (e) => {
      const sym = e.detail?.symbol;
      if (sym) {
        setScope('symbol');
        setSymbol(String(sym).toUpperCase());
      }
    };
    window.addEventListener('news-focus', onFocus);
    return () => window.removeEventListener('news-focus', onFocus);
  }, []);

  const items = useMemo(() => {
    const list = feed?.items || [];
    if (filter === 'bullish') return list.filter((i) => (i.score ?? 0) >= 0.15);
    if (filter === 'bearish') return list.filter((i) => (i.score ?? 0) <= -0.15);
    return list;
  }, [feed?.items, filter]);

  const aggregate = feed?.aggregate;
  const sources = feed?.sources_available || [];
  const aggVariant = sentimentVariant(aggregate?.aggregate_score);

  const onMoverPick = useCallback((row) => {
    const term = String(row?.terminal_symbol || row?.symbol || '').toUpperCase();
    if (!term) return;
    setScope('symbol');
    setSymbol(term);
    setActiveSymbol?.(term);
  }, [setActiveSymbol]);

  const onRelatedClick = useCallback((sym) => {
    const term = String(sym || '').toUpperCase();
    if (!term) return;
    // Crypto news tickers are BTCUSD; map common USD → USDT terminal form.
    const mapped = term.endsWith('USD') && !term.endsWith('USDT') && term.length > 3
      ? `${term.slice(0, -3)}USDT`
      : term;
    setScope('symbol');
    setSymbol(mapped);
    setActiveSymbol?.(mapped);
  }, [setActiveSymbol]);

  return (
    <div className="news-feed dock-panel-tab flex min-h-0 flex-1 flex-col">
      <div className="news-feed__toolbar">
        <div className="news-feed__toolbar-start">
          <Newspaper className="news-feed__toolbar-icon" aria-hidden />
          <span className="news-feed__toolbar-label">News</span>
          <Select value={scope} onValueChange={setScope}>
            <SelectTrigger className="news-feed__scope">
              <SelectValue placeholder="Scope" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="market" className="text-xs">Market movers</SelectItem>
              <SelectItem value="symbol" className="text-xs">Symbol</SelectItem>
            </SelectContent>
          </Select>
          {scope === 'symbol' && (
            <ChartSymbolSwitcher compact className="news-feed__symbol" />
          )}
        </div>
        <div className="news-feed__toolbar-end">
          <Select value={filter} onValueChange={setFilter}>
            <SelectTrigger className="news-feed__filter">
              <SelectValue placeholder="Filter" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all" className="text-xs">All headlines</SelectItem>
              <SelectItem value="bullish" className="text-xs">Bullish</SelectItem>
              <SelectItem value="bearish" className="text-xs">Bearish</SelectItem>
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            className="news-feed__refresh"
            disabled={loading}
            onClick={() => loadNews(scope, symbol, true)}
          >
            <RefreshCw className={cn('news-feed__refresh-icon', loading && 'animate-spin')} aria-hidden />
            Refresh
          </Button>
        </div>
      </div>

      {scope === 'market' && feed?.movers && (
        <MoversStrip movers={feed.movers} onPick={onMoverPick} />
      )}

      {aggregate && (aggregate.mention_count ?? 0) > 0 && (
        <div className={cn('news-feed__aggregate', `news-feed__aggregate--${aggVariant}`)}>
          <div className="news-feed__aggregate-main">
            <span className="news-feed__aggregate-label">
              {scope === 'market'
                ? 'Market headlines'
                : (feed?.lookback_hours ? `${feed.lookback_hours}h` : '24h')}
              {' '}sentiment
            </span>
            <span className={cn('news-feed__aggregate-score', scoreTone(aggregate.aggregate_score))}>
              {scoreLabel(aggregate.aggregate_score)}
              {aggregate.aggregate_score != null
                ? ` (${aggregate.aggregate_score >= 0 ? '+' : ''}${Number(aggregate.aggregate_score).toFixed(2)})`
                : ''}
            </span>
          </div>
          {aggregate.mention_count > 0 && (
            <span className="news-feed__aggregate-mentions">
              {aggregate.mention_count} mentions
            </span>
          )}
        </div>
      )}

      {sources.length > 0 && (
        <p className="news-feed__sources">
          <span className="news-feed__sources-label">Sources</span>
          {sources.map((s) => s.replace(/_/g, ' ')).join(' · ')}
          {scope === 'market' && (
            <span className="news-feed__sources-hint">
              Alpaca screener movers + Benzinga headlines.
            </span>
          )}
          {scope === 'symbol' && !sources.includes('finnhub_news') && (
            <span className="news-feed__sources-hint">Add FINNHUB_API_KEY for Finnhub headlines on equities.</span>
          )}
          {terminalMode === 'LIVE_ALPACA' && !sources.includes('alpaca_news') && (
            <span className="news-feed__sources-hint">Alpaca news needs ALPACA_API_KEY / ALPACA_SECRET_KEY (Benzinga).</span>
          )}
        </p>
      )}

      <DockScrollPanel className="news-feed__scroll flex-1">
        {loading && !feed && (
          <WidgetEmpty message="Loading headlines…" />
        )}
        {!loading && error && !items.length && (
          <WidgetEmpty message={error} />
        )}
        {!loading && !error && items.length === 0 && (
          <WidgetEmpty
            message={
              scope === 'market'
                ? 'No market headlines yet. Try Refresh.'
                : `No recent headlines for ${symbol}. Try Refresh or another symbol.`
            }
          />
        )}
        {items.length > 0 && (
          <VirtualScrollList
            className="news-feed__list"
            items={items}
            rowHeight={scope === 'market' ? 88 : 72}
            getKey={(item) => item.id || `${item.source}-${item.headline}`}
            renderItem={(item) => (
              <li className="list-none">
                <NewsItem item={item} onSymbolClick={onRelatedClick} />
              </li>
            )}
          />
        )}
      </DockScrollPanel>
    </div>
  );
}
