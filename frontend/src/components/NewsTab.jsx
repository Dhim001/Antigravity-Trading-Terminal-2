/**
 * News tab — symbol-scoped financial headlines (Finnhub, yfinance, Polygon).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ExternalLink, Newspaper, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { fetchSymbolNews } from '../api/endpoints';
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

function NewsItem({ item }) {
  const tone = scoreTone(item.score);
  const variant = sentimentVariant(item.score);
  const content = (
    <>
      <div className="news-feed__item-head">
        <span className="news-feed__source">
          {item.source_label || item.source}
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
      <p className="news-feed__meta">{formatPublished(item.published_at)}</p>
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

  const aggVariant = sentimentVariant(aggregate?.aggregate_score);

  return (
    <div className="news-feed dock-panel-tab flex min-h-0 flex-1 flex-col">
      <div className="news-feed__toolbar">
        <div className="news-feed__toolbar-start">
          <Newspaper className="news-feed__toolbar-icon" aria-hidden />
          <span className="news-feed__toolbar-label">News</span>
          <ChartSymbolSwitcher compact className="news-feed__symbol" />
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
            onClick={() => loadNews(symbol, true)}
          >
            <RefreshCw className={cn('news-feed__refresh-icon', loading && 'animate-spin')} aria-hidden />
            Refresh
          </Button>
        </div>
      </div>

      {aggregate && (aggregate.mention_count ?? 0) > 0 && (
        <div className={cn('news-feed__aggregate', `news-feed__aggregate--${aggVariant}`)}>
          <div className="news-feed__aggregate-main">
            <span className="news-feed__aggregate-label">
              {feed?.lookback_hours ? `${feed.lookback_hours}h` : '24h'} sentiment
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
          {!sources.includes('finnhub_news') && (
            <span className="news-feed__sources-hint">Add FINNHUB_API_KEY for Finnhub headlines on equities.</span>
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
          <WidgetEmpty message={`No recent headlines for ${symbol}. Try Refresh or another symbol.`} />
        )}
        {items.length > 0 && (
          <VirtualScrollList
            className="news-feed__list"
            items={items}
            rowHeight={72}
            getKey={(item) => item.id || `${item.source}-${item.headline}`}
            renderItem={(item) => (
              <li className="list-none">
                <NewsItem item={item} />
              </li>
            )}
          />
        )}
      </DockScrollPanel>
    </div>
  );
}
