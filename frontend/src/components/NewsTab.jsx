/**
 * News tab — symbol-scoped financial headlines (Finnhub, yfinance, Polygon).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ExternalLink, Newspaper, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { fetchSymbolNews } from '../api/endpoints';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { WidgetEmpty, DockScrollPanel } from './WidgetShell';
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

function NewsItem({ item }) {
  const tone = scoreTone(item.score);
  const content = (
    <>
      <div className="news-feed__item-head">
        <Badge variant="outline" className="news-feed__source h-4 px-1 text-[0.58rem] font-normal">
          {item.source_label || item.source}
        </Badge>
        <span className={cn('news-feed__score text-[0.58rem] font-medium', tone)}>
          {scoreLabel(item.score)}
          {item.score != null ? ` (${item.score >= 0 ? '+' : ''}${Number(item.score).toFixed(2)})` : ''}
        </span>
      </div>
      <p className="news-feed__headline">{item.headline}</p>
      {item.summary && (
        <p className="news-feed__summary">{item.summary}</p>
      )}
      <p className="news-feed__meta">{formatPublished(item.published_at)}</p>
    </>
  );

  if (item.url) {
    return (
      <a
        href={item.url}
        target="_blank"
        rel="noopener noreferrer"
        className="news-feed__item news-feed__item--link"
      >
        {content}
        <ExternalLink className="news-feed__external" aria-hidden />
      </a>
    );
  }

  return <article className="news-feed__item">{content}</article>;
}

export default function NewsTab() {
  const activeSymbol = useStore((s) => s.activeSymbol);
  const [symbol, setSymbol] = useState(activeSymbol);
  const [filter, setFilter] = useState('all');
  const [loading, setLoading] = useState(false);
  const [feed, setFeed] = useState(null);
  const [error, setError] = useState(null);
  const loadSeq = useRef(0);

  useEffect(() => {
    setSymbol(activeSymbol);
  }, [activeSymbol]);

  const loadNews = useCallback(async (sym, refresh = true) => {
    const target = String(sym || '').toUpperCase().trim();
    if (!target) return;
    const seq = ++loadSeq.current;
    setLoading(true);
    setError(null);
    try {
      const body = await fetchSymbolNews(target, { refresh, limit: 50, lookbackHours: 72 });
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
    loadNews(symbol, true);
  }, [symbol, loadNews]);

  useEffect(() => {
    const onFocus = (e) => {
      const sym = e.detail?.symbol;
      if (sym) setSymbol(String(sym).toUpperCase());
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

  return (
    <div className="news-feed dock-panel-tab flex min-h-0 flex-1 flex-col">
      <div className="news-feed__toolbar flex flex-wrap items-center gap-2 border-b border-border/50 px-3 py-2">
        <div className="flex items-center gap-2">
          <Newspaper className="size-3.5 text-muted-foreground" aria-hidden />
          <span className="text-xs font-semibold tracking-wide uppercase text-muted-foreground">News</span>
          <Badge variant="secondary" className="h-5 px-1.5 text-[0.65rem] font-mono">
            {symbol}
          </Badge>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Select value={filter} onValueChange={setFilter}>
            <SelectTrigger className="h-7 w-[7.5rem] text-xs">
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
            className="h-7 gap-1 text-xs"
            disabled={loading}
            onClick={() => loadNews(symbol, true)}
          >
            <RefreshCw className={cn('size-3', loading && 'animate-spin')} />
            Refresh
          </Button>
        </div>
      </div>

      {aggregate && (aggregate.mention_count ?? 0) > 0 && (
        <div className="news-feed__aggregate mx-3 mt-2 rounded-md border border-border/50 bg-muted/20 px-2.5 py-2 text-xs">
          <span className="text-muted-foreground">
            {feed?.lookback_hours ? `${feed.lookback_hours}h` : '24h'} sentiment{' '}
          </span>
          <span className={cn('font-medium', scoreTone(aggregate.aggregate_score))}>
            {scoreLabel(aggregate.aggregate_score)}
            {aggregate.aggregate_score != null
              ? ` (${aggregate.aggregate_score >= 0 ? '+' : ''}${Number(aggregate.aggregate_score).toFixed(2)})`
              : ''}
          </span>
          {aggregate.mention_count > 0 && (
            <span className="text-muted-foreground"> · {aggregate.mention_count} mentions</span>
          )}
        </div>
      )}

      {sources.length > 0 && (
        <p className="news-feed__sources px-3 pt-2 text-[0.62rem] text-muted-foreground">
          Sources: {sources.map((s) => s.replace(/_/g, ' ')).join(' · ')}
          {!sources.includes('finnhub_news') && (
            <span className="block mt-0.5 opacity-80">Add FINNHUB_API_KEY for Finnhub headlines on equities.</span>
          )}
        </p>
      )}

      <DockScrollPanel className="news-feed__scroll flex-1 px-3 py-2">
        {loading && !feed && (
          <WidgetEmpty message="Loading headlines…" />
        )}
        {!loading && error && !items.length && (
          <WidgetEmpty message={error} />
        )}
        {!loading && !error && items.length === 0 && (
          <WidgetEmpty message={`No recent headlines for ${symbol}. Try Refresh or another symbol.`} />
        )}
        {items.length > 0 && (
          <ul className="news-feed__list space-y-2">
            {items.map((item) => (
              <li key={item.id || `${item.source}-${item.headline}`}>
                <NewsItem item={item} />
              </li>
            ))}
          </ul>
        )}
      </DockScrollPanel>
    </div>
  );
}
