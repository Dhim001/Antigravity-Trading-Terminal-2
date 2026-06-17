import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useStore } from '../store/useStore';
import { selectStripItemState } from '../store/selectors';
import { cn } from '@/lib/utils';
import { formatChangePct, formatPrice, stripSymbolLabel } from '@/lib/formatPrice';

const TICKER_SPEED_PX = 40;
const HOT_MOVE_PCT = 3;
/** Re-rank movers on this interval; DOM order updates at loop seam only. */
const SORT_REFRESH_MS = 20_000;
/** Sort bucket (0.5%) — coarser ranks, fewer pending reorders. */
const MOVER_SORT_BUCKET_PCT = 0.5;

function moverSortKey(change) {
  if (change == null || Number.isNaN(change)) return -1;
  return Math.floor(Math.abs(change) / MOVER_SORT_BUCKET_PCT);
}

/** Biggest |24h%| movers first; ties keep watchlist order. */
function sortSymbolsByMovers(symbols, tickerData) {
  const watchlistOrder = new Map(symbols.map((sym, index) => [sym, index]));
  return [...symbols].sort((a, b) => {
    const rankA = moverSortKey(tickerData[a]?.change_24h);
    const rankB = moverSortKey(tickerData[b]?.change_24h);
    if (rankB !== rankA) return rankB - rankA;
    return (watchlistOrder.get(a) ?? 0) - (watchlistOrder.get(b) ?? 0);
  });
}

function assetKind(sym) {
  if (sym.includes('USDT')) return 'crypto';
  if (['SPY', 'QQQ'].includes(sym)) return 'etf';
  return 'equity';
}

const DOT_CLASS = {
  crypto: 'bg-trading-warn shadow-[0_0_4px_var(--color-crypto)]',
  etf: 'bg-trading-accent shadow-[0_0_4px_var(--color-etf)]',
  equity: 'bg-primary shadow-[0_0_4px_var(--color-equity)]',
};

/** Command-bar tape: symbol + last + 24h% (TradingView / Bloomberg style). */
const MarketStripItemCompact = memo(function MarketStripItemCompact({ sym }) {
  const { price, change, isActive } = useStore(
    useShallow(useCallback((state) => selectStripItemState(state, sym), [sym])),
  );
  const setActiveSymbol = useStore((state) => state.setActiveSymbol);

  const kind = assetKind(sym);
  const label = stripSymbolLabel(sym, true);
  const isUp = (change ?? 0) >= 0;
  const hot = change != null && Math.abs(change) >= HOT_MOVE_PCT;

  return (
    <button
      type="button"
      title={price != null ? `${sym} · ${formatPrice(sym, price)} · ${formatChangePct(change)}` : sym}
      className={cn(
        'strip-item border-b-2',
        isActive && 'strip-item-active border-primary bg-primary/10',
        !isActive && 'border-transparent',
        hot && (isUp ? 'strip-item--hot-up' : 'strip-item--hot-down'),
      )}
      onClick={() => setActiveSymbol(sym)}
    >
      <span className={cn('size-1.5 shrink-0 rounded-full', DOT_CLASS[kind])} />
      <span className={cn(
        'strip-item__symbol text-xs font-bold tracking-wide',
        isActive ? 'text-foreground' : 'text-secondary-foreground',
      )}>
        {label}
      </span>
      <span className={cn(
        'strip-item__price num-mono text-xs font-semibold',
        price != null ? (isUp ? 'text-trading-up' : 'text-trading-down') : 'text-muted-foreground',
      )}>
        {price != null ? formatPrice(sym, price) : '—'}
      </span>
      <span className={cn(
        'strip-item__change num-mono text-xs',
        price != null ? (isUp ? 'text-trading-up' : 'text-trading-down') : 'text-muted-foreground',
      )}>
        {price != null ? formatChangePct(change) : '—'}
      </span>
    </button>
  );
});

const MarketStripItemFull = memo(function MarketStripItemFull({ sym }) {
  const { price, change, isActive } = useStore(
    useShallow(useCallback((state) => selectStripItemState(state, sym), [sym])),
  );
  const setActiveSymbol = useStore((state) => state.setActiveSymbol);

  const kind = assetKind(sym);
  const label = stripSymbolLabel(sym, false);
  const isUp = (change ?? 0) >= 0;
  const hot = change != null && Math.abs(change) >= HOT_MOVE_PCT;

  return (
    <button
      type="button"
      className={cn(
        'strip-item border-b-2',
        isActive && 'strip-item-active border-primary bg-primary/10',
        !isActive && 'border-transparent',
        hot && (isUp ? 'strip-item--hot-up' : 'strip-item--hot-down'),
      )}
      onClick={() => setActiveSymbol(sym)}
    >
      <span className={cn('size-1.5 shrink-0 rounded-full', DOT_CLASS[kind])} />
      <span className={cn(
        'strip-item__symbol text-xs font-bold tracking-wide',
        isActive ? 'text-foreground' : 'text-secondary-foreground',
      )}>
        {label}
      </span>
      <span className={cn(
        'strip-item__price num-mono text-xs font-semibold',
        price != null ? (isUp ? 'text-trading-up' : 'text-trading-down') : 'text-muted-foreground',
      )}>
        {price != null ? formatPrice(sym, price) : '—'}
      </span>
      <span className={cn(
        'strip-item__change num-mono text-xs strip-item-change',
        price != null ? (isUp ? 'text-trading-up' : 'text-trading-down') : 'text-muted-foreground',
      )}>
        {price != null ? formatChangePct(change) : '—'}
      </span>
    </button>
  );
});

function MarketStripItem({ sym, compact = false }) {
  return compact
    ? <MarketStripItemCompact sym={sym} />
    : <MarketStripItemFull sym={sym} />;
}

function TickerSet({ symbols, compact, setRef, ariaHidden, suffix }) {
  return (
    <div
      ref={setRef}
      className="strip-ticker__set"
      aria-hidden={ariaHidden || undefined}
    >
      {symbols.map((sym) => (
        <MarketStripItem key={`${sym}-${suffix}`} sym={sym} compact={compact} />
      ))}
    </div>
  );
}

function measureLoopWidth(track, set) {
  if (!set) return 0;
  const setWidth = set.offsetWidth;
  if (setWidth <= 0) return 0;
  if (!track) return setWidth;

  const styles = getComputedStyle(track);
  const setGap = Number.parseFloat(styles.columnGap || styles.gap) || 0;
  return setWidth + setGap;
}

function readSortedTapeSymbols(symbolsList) {
  return sortSymbolsByMovers(symbolsList, useStore.getState().tickerData);
}

export default function MarketOverviewStrip({ compact = false }) {
  const symbolsList = useStore(state => state.symbolsList);
  const watchlistKey = useMemo(() => symbolsList.join('|'), [symbolsList]);

  const [tapeSymbols, setTapeSymbols] = useState(() => readSortedTapeSymbols(symbolsList));
  const tapeSymbolsRef = useRef(tapeSymbols);
  tapeSymbolsRef.current = tapeSymbols;

  const pendingSortRef = useRef(null);

  const trackRef = useRef(null);
  const setRef = useRef(null);
  const loopRef = useRef({
    offset: 0,
    halfWidth: 0,
    paused: false,
    lastTs: null,
    rafId: null,
  });
  const prevWatchlistKeyRef = useRef(watchlistKey);

  const scheduleResort = useCallback(() => {
    const next = readSortedTapeSymbols(symbolsList);
    if (next.join('|') === tapeSymbolsRef.current.join('|')) {
      pendingSortRef.current = null;
      return;
    }
    pendingSortRef.current = next;
  }, [symbolsList]);

  const applyPendingSort = useCallback(() => {
    const pending = pendingSortRef.current;
    if (!pending) return;
    pendingSortRef.current = null;
    setTapeSymbols(pending);
  }, []);

  useEffect(() => {
    pendingSortRef.current = null;
    setTapeSymbols(readSortedTapeSymbols(symbolsList));
  }, [watchlistKey, symbolsList]);

  useEffect(() => {
    scheduleResort();
    const id = window.setInterval(() => {
      scheduleResort();
      if (loopRef.current.paused) applyPendingSort();
    }, SORT_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [scheduleResort, applyPendingSort]);

  const applyPendingSortRef = useRef(applyPendingSort);
  applyPendingSortRef.current = applyPendingSort;

  useLayoutEffect(() => {
    const track = trackRef.current;
    const set = setRef.current;
    const loop = loopRef.current;
    if (!track || !set) return undefined;

    const resetPhase = prevWatchlistKeyRef.current !== watchlistKey || loop.halfWidth <= 0;
    prevWatchlistKeyRef.current = watchlistKey;

    const applyMeasure = () => {
      const next = measureLoopWidth(track, set);
      if (next <= 0) return;

      const prev = loop.halfWidth;
      if (resetPhase) {
        loop.offset = 0;
      } else if (prev !== next) {
        loop.offset = Math.round((loop.offset % prev) * (next / prev)) % next;
      }
      loop.halfWidth = next;
      track.style.transform = `translate3d(${-loop.offset}px, 0, 0)`;
    };

    applyMeasure();
    const raf = requestAnimationFrame(applyMeasure);
    let cancelled = false;
    document.fonts?.ready?.then(() => {
      if (!cancelled) applyMeasure();
    });

    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
    };
  }, [watchlistKey, compact]);

  useEffect(() => {
    const track = trackRef.current;
    const loop = loopRef.current;
    if (!track) return undefined;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) return undefined;

    let lastPaint = -1;

    const frame = (ts) => {
      if (loop.lastTs == null) loop.lastTs = ts;
      const dt = Math.min((ts - loop.lastTs) / 1000, 0.032);
      loop.lastTs = ts;

      const half = loop.halfWidth;
      const hidden = document.visibilityState === 'hidden';

      if (!loop.paused && !hidden && half > 0) {
        loop.offset += TICKER_SPEED_PX * dt;
        if (loop.offset >= half) {
          loop.offset %= half;
          applyPendingSortRef.current();
        }
        const paint = Math.round(loop.offset);
        if (paint !== lastPaint) {
          lastPaint = paint;
          track.style.transform = `translate3d(${-paint}px, 0, 0)`;
        }
      }

      loop.rafId = requestAnimationFrame(frame);
    };

    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        loop.lastTs = null;
      }
    };
    document.addEventListener('visibilitychange', onVisibility);

    loop.rafId = requestAnimationFrame(frame);

    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      if (loop.rafId) cancelAnimationFrame(loop.rafId);
      loop.rafId = null;
      loop.lastTs = null;
    };
  }, []);

  return (
    <div className={cn('market-strip', compact && 'market-strip--compact')}>
      <div
        className="strip-ticker-viewport"
        onMouseEnter={() => {
          loopRef.current.paused = true;
          loopRef.current.lastTs = null;
          applyPendingSort();
        }}
        onMouseLeave={() => {
          loopRef.current.paused = false;
          loopRef.current.lastTs = null;
        }}
      >
        <div className="strip-ticker" ref={trackRef}>
          <TickerSet symbols={tapeSymbols} compact={compact} setRef={setRef} suffix="a" />
          <TickerSet symbols={tapeSymbols} compact={compact} ariaHidden suffix="b" />
        </div>
      </div>
    </div>
  );
}
