/**
 * Left-rail Market Movers — TradingView-style gainers/losers/actives + headlines.
 * Sibling of Watchlist; preference: workspace.leftPanelTab.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ExternalLink, RefreshCw, TrendingUp } from 'lucide-react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { fetchMarketNews } from '../api/endpoints';
import { formatChangePct, formatPrice, formatVolCompact } from '../lib/formatPrice';
import { WidgetShell, WidgetEmpty, DockScrollPanel } from './WidgetShell';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

const REFRESH_MS = 60_000;
const ROW_LIMIT = 8;

/** Warrants/units clutter Alpaca weekend screener — skip in the rail UI. */
function isNoisyMoverSymbol(symbol) {
  const s = String(symbol || '').toUpperCase();
  if (!s || s.includes('.')) return true;
  if (s.endsWith('WW') || s.endsWith('WS')) return true;
  if (s.length >= 5 && s.endsWith('W') && !s.includes('/')) return true;
  return false;
}

function cleanRows(rows, { requirePct = false } = {}) {
  const out = [];
  const seen = new Set();
  for (const row of rows || []) {
    if (!row || typeof row !== 'object') continue;
    const sym = String(row.symbol || '').toUpperCase();
    if (!sym || seen.has(sym) || isNoisyMoverSymbol(sym)) continue;
    if (requirePct && (row.percent_change == null || Number.isNaN(Number(row.percent_change)))) {
      continue;
    }
    seen.add(sym);
    out.push(row);
  }
  return out;
}

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

function toTerminalSymbol(raw) {
  const term = String(raw || '').toUpperCase().trim();
  if (!term) return '';
  if (term.includes('/')) {
    const [base, quote] = term.split('/');
    if (quote === 'USD' || quote === 'USDT' || quote === 'USDC') return `${base}USDT`;
    return `${base}${quote}`;
  }
  if (term.endsWith('USD') && !term.endsWith('USDT') && term.length > 3) {
    return `${term.slice(0, -3)}USDT`;
  }
  return term;
}

function displayLabel(row) {
  const term = row.terminal_symbol || row.symbol || '';
  if (String(term).endsWith('USDT')) return String(term).slice(0, -4);
  if (String(row.symbol || '').includes('/')) return String(row.symbol).split('/')[0];
  return term;
}

function barWidthPct(pct, maxAbs) {
  const n = Math.abs(Number(pct) || 0);
  const cap = Math.max(maxAbs || 1, 1);
  return Math.max(6, Math.min(100, (n / cap) * 100));
}

function MoverRow({ row, maxAbs, mode, onClick }) {
  const pct = Number(row.percent_change);
  const hasPct = !Number.isNaN(pct);
  const up = hasPct && pct >= 0;
  const label = displayLabel(row);
  const term = row.terminal_symbol || row.symbol;
  const price = row.price;
  const vol = row.volume;

  return (
    <button
      type="button"
      className={cn(
        'movers-panel__row',
        hasPct && (up ? 'movers-panel__row--up' : 'movers-panel__row--down'),
      )}
      title={`${row.symbol}${hasPct ? ` · ${formatChangePct(pct)}` : ''}`}
      onClick={() => onClick?.(row)}
    >
      <div className="movers-panel__row-main">
        <span className="movers-panel__sym">{label}</span>
        {row.market_type === 'crypto' && (
          <span className="movers-panel__tag">CRYPTO</span>
        )}
        {mode === 'actives' && vol != null && (
          <span className="movers-panel__meta">{formatVolCompact(vol)}</span>
        )}
      </div>
      <div className="movers-panel__row-metrics">
        {price != null && (
          <span className="movers-panel__price">{formatPrice(term, price)}</span>
        )}
        {hasPct ? (
          <div className="movers-panel__chg-wrap">
            <span
              className={cn('movers-panel__bar', up ? 'movers-panel__bar--up' : 'movers-panel__bar--down')}
              style={{ width: `${barWidthPct(pct, maxAbs)}%` }}
              aria-hidden
            />
            <span className={cn('movers-panel__pct', up ? 'text-trading-up' : 'text-trading-down')}>
              {formatChangePct(pct)}
            </span>
          </div>
        ) : (
          <span className="movers-panel__pct movers-panel__pct--muted">—</span>
        )}
      </div>
    </button>
  );
}

function HeadlineItem({ item, onSymbolClick }) {
  const related = (item.related_symbols || []).slice(0, 3);
  const inner = (
    <>
      <p className="movers-panel__hl-text">{item.headline}</p>
      <div className="movers-panel__hl-meta">
        <span className="movers-panel__hl-source">{item.source_label || 'News'}</span>
        {related.map((sym) => (
          <button
            key={sym}
            type="button"
            className="movers-panel__hl-chip"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onSymbolClick?.(sym);
            }}
          >
            {sym}
          </button>
        ))}
        <span className="movers-panel__hl-time">{formatPublished(item.published_at)}</span>
      </div>
    </>
  );

  if (item.url) {
    return (
      <a
        href={item.url}
        target="_blank"
        rel="noopener noreferrer"
        className="movers-panel__hl movers-panel__hl--link"
      >
        {inner}
        <ExternalLink className="movers-panel__hl-ext" aria-hidden />
      </a>
    );
  }
  return <article className="movers-panel__hl">{inner}</article>;
}

const BOARD_TABS = [
  { id: 'gainers', label: 'Gainers' },
  { id: 'losers', label: 'Losers' },
  { id: 'actives', label: 'Actives' },
];

export default function MarketMoversWidget() {
  const setActiveSymbol = useStore((s) => s.setActiveSymbol);
  const [board, setBoard] = useState('gainers');
  const [loading, setLoading] = useState(false);
  const [feed, setFeed] = useState(null);
  const [error, setError] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);
  const loadSeq = useRef(0);

  const load = useCallback(async ({ silent = false } = {}) => {
    const seq = ++loadSeq.current;
    if (!silent) setLoading(true);
    try {
      const body = await fetchMarketNews({ limit: 24, top: 10, lookbackHours: 72 });
      if (seq !== loadSeq.current) return;
      if (!body?.ok) throw new Error(body?.error || 'Market movers unavailable');
      setFeed(body.news);
      setError(null);
      setUpdatedAt(Date.now());
    } catch (e) {
      if (seq !== loadSeq.current) return;
      setError(e.message || 'Failed to load movers');
      if (!silent) toast.error(e.message || 'Failed to load movers');
    } finally {
      if (seq === loadSeq.current && !silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(() => load({ silent: true }), REFRESH_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const pickSymbol = useCallback((rowOrSym) => {
    const raw = typeof rowOrSym === 'string'
      ? rowOrSym
      : (rowOrSym?.terminal_symbol || rowOrSym?.symbol);
    const term = toTerminalSymbol(raw);
    if (!term) return;
    setActiveSymbol?.(term);
  }, [setActiveSymbol]);

  const movers = feed?.movers;
  const stocks = movers?.stocks || {};
  const crypto = movers?.crypto || {};

  const gainers = useMemo(
    () => cleanRows(
      [...(stocks.gainers || []), ...(crypto.gainers || [])],
      { requirePct: true },
    )
      .sort((a, b) => Number(b.percent_change) - Number(a.percent_change))
      .slice(0, ROW_LIMIT),
    [stocks.gainers, crypto.gainers],
  );

  const losers = useMemo(
    () => cleanRows(
      [...(stocks.losers || []), ...(crypto.losers || [])],
      { requirePct: true },
    )
      .sort((a, b) => Number(a.percent_change) - Number(b.percent_change))
      .slice(0, ROW_LIMIT),
    [stocks.losers, crypto.losers],
  );

  const actives = useMemo(
    () => cleanRows(movers?.most_actives || []).slice(0, ROW_LIMIT),
    [movers?.most_actives],
  );

  const activeRows = board === 'losers' ? losers : board === 'actives' ? actives : gainers;
  const maxAbs = useMemo(() => {
    const vals = activeRows
      .map((r) => Math.abs(Number(r.percent_change) || 0))
      .filter((n) => n > 0);
    if (!vals.length) return 5;
    // Cap so one microcap spike does not crush every other bar.
    return Math.min(Math.max(...vals), 40);
  }, [activeRows]);

  const headlines = feed?.items || [];
  const asOf = updatedAt
    ? new Date(updatedAt).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : null;

  return (
    <WidgetShell
      icon={TrendingUp}
      title="Market movers"
      className="movers-panel"
      headerRight={(
        <div className="movers-panel__hdr-actions">
          {asOf && <span className="movers-panel__asof">{asOf}</span>}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="movers-panel__refresh"
            disabled={loading}
            onClick={() => load()}
            title="Refresh movers & headlines"
          >
            <RefreshCw className={cn('size-3', loading && 'animate-spin')} aria-hidden />
          </Button>
        </div>
      )}
      contentClassName="movers-panel__content"
    >
      <div className="movers-panel__body">
        <div className="movers-panel__tabs" role="tablist" aria-label="Mover board">
          {BOARD_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={board === tab.id}
              className={cn('movers-panel__tab', board === tab.id && 'movers-panel__tab--active')}
              onClick={() => setBoard(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <section className="movers-panel__board" aria-label={board}>
          {loading && !feed && <WidgetEmpty message="Loading movers…" />}
          {!loading && error && !feed && <WidgetEmpty message={error} />}
          {feed && activeRows.length === 0 && (
            <WidgetEmpty message={`No ${board} right now.`} />
          )}
          {activeRows.length > 0 && (
            <div className="movers-panel__list">
              {activeRows.map((row) => (
                <MoverRow
                  key={`${board}-${row.market_type || 'eq'}-${row.symbol}`}
                  row={row}
                  maxAbs={maxAbs}
                  mode={board}
                  onClick={pickSymbol}
                />
              ))}
            </div>
          )}
        </section>

        <section className="movers-panel__news" aria-label="Leading headlines">
          <div className="movers-panel__news-head">
            <h3 className="movers-panel__news-title">Headlines</h3>
            <span className="movers-panel__news-count">{headlines.length}</span>
          </div>
          <DockScrollPanel className="movers-panel__scroll">
            {loading && !headlines.length && <WidgetEmpty message="Loading headlines…" />}
            {!loading && !error && headlines.length === 0 && (
              <WidgetEmpty message="No headlines yet." />
            )}
            {headlines.length > 0 && (
              <ul className="movers-panel__hl-list">
                {headlines.map((item) => (
                  <li key={item.id || item.headline} className="list-none">
                    <HeadlineItem item={item} onSymbolClick={pickSymbol} />
                  </li>
                ))}
              </ul>
            )}
          </DockScrollPanel>
        </section>
      </div>
    </WidgetShell>
  );
}
