/**
 * WatchlistWidget.jsx
 * Enhanced market watchlist with:
 *  - Category tabs (ALL / CRYPTO / EQUITY / ETF)
 *  - Mini SVG sparkline per row
 *  - Volume column
 *  - Search filter
 *  - Sortable headers
 *  - Asset-type color coding
 */
import React, { useEffect, useState, useMemo, useRef } from 'react';
import { useStore } from '../store/useStore';
import { Search, Activity } from 'lucide-react';

// ── Asset type helpers ──────────────────────────────────────────
const isCrypto = (sym) => sym.includes('USDT');
const isETF    = (sym) => ['SPY','QQQ'].includes(sym);
const getCategory = (sym) => isCrypto(sym) ? 'CRYPTO' : isETF(sym) ? 'ETF' : 'EQUITY';

const ASSET_COLOR = {
  CRYPTO: 'var(--color-crypto)',
  EQUITY: 'var(--color-equity)',
  ETF:    'var(--color-etf)',
};

const getPriceDecimals = (sym, price) => {
  if (sym.includes('XRP') || sym.includes('ADA') || sym.includes('DOGE') || (price != null && price < 2.0)) return 4;
  return 2;
};

const fmtVol = (v) => {
  if (!v) return '—';
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return v.toFixed(0);
};

// ── Mini Sparkline (pure SVG) ──────────────────────────────────
function MiniSparkline({ points, isUp }) {
  if (!points || points.length < 2) return <span style={{ width: 44, display: 'inline-block' }} />;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const w = 44, h = 22;
  const xs = points.map((_, i) => (i / (points.length - 1)) * w);
  const ys = points.map(v => h - ((v - min) / range) * h * 0.85 - h * 0.075);
  const d = xs.map((x, i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ');
  const color = isUp ? '#10b981' : '#ef4444';
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: 'block', flexShrink: 0 }}>
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.85" />
    </svg>
  );
}

const SORT_FIELDS = ['symbol', 'price', 'change_24h', 'volume_24h'];

export default function WatchlistWidget() {
  const { tickerData, priceDirections, activeSymbol, setActiveSymbol, symbolsList } = useStore();

  const [cat,      setCat]    = useState('ALL');
  const [search,   setSearch] = useState('');
  const [sort,     setSort]   = useState({ field: 'symbol', dir: 'asc' });
  const [flash,    setFlash]  = useState({});

  // Sparkline data: last 24 price samples per symbol
  const sparkRef = useRef({});

  // Update sparklines on every tick
  useEffect(() => {
    Object.entries(tickerData).forEach(([sym, info]) => {
      if (!info?.price) return;
      if (!sparkRef.current[sym]) sparkRef.current[sym] = [];
      const arr = sparkRef.current[sym];
      if (arr.length === 0 || arr[arr.length - 1] !== info.price) {
        arr.push(info.price);
        if (arr.length > 24) arr.shift();
      }
    });
  }, [tickerData]);

  // Flash animation tracking
  useEffect(() => {
    const newFlash = { ...flash };
    let changed = false;
    Object.entries(priceDirections).forEach(([sym, dir]) => {
      if (dir !== 'flat' && (!flash[sym] || flash[sym].dir !== dir)) {
        newFlash[sym] = { dir, key: Date.now() };
        changed = true;
      }
    });
    if (changed) setFlash(newFlash);
  }, [priceDirections]);

  const handleSort = (field) => {
    setSort(prev => ({ field, dir: prev.field === field ? (prev.dir === 'asc' ? 'desc' : 'asc') : 'asc' }));
  };

  // Filter + sort symbols
  const displaySymbols = useMemo(() => {
    let list = symbolsList;
    if (cat !== 'ALL') list = list.filter(s => getCategory(s) === cat);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(s => s.toLowerCase().includes(q));
    }
    return [...list].sort((a, b) => {
      const ia = tickerData[a], ib = tickerData[b];
      let va = sort.field === 'symbol' ? a : (ia?.[sort.field] ?? 0);
      let vb = sort.field === 'symbol' ? b : (ib?.[sort.field] ?? 0);
      if (typeof va === 'string') return sort.dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
      return sort.dir === 'asc' ? va - vb : vb - va;
    });
  }, [symbolsList, cat, search, sort, tickerData]);

  const counts = useMemo(() => ({
    ALL:    symbolsList.length,
    CRYPTO: symbolsList.filter(isCrypto).length,
    EQUITY: symbolsList.filter(s => !isCrypto(s) && !isETF(s)).length,
    ETF:    symbolsList.filter(isETF).length,
  }), [symbolsList]);

  return (
    <div className="widget-card">
      {/* Header */}
      <div className="widget-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <Activity size={13} className="logo-icon" />
          <span className="widget-title">Watchlist</span>
        </div>
      </div>

      {/* Search */}
      <div style={{ padding: '6px 10px', borderBottom: '1px solid var(--border-color)', flexShrink: 0, background: 'rgba(6,10,18,0.8)' }}>
        <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
          <Search size={11} style={{ position: 'absolute', left: 8, color: 'var(--text-muted)', pointerEvents: 'none' }} />
          <input
            type="text"
            placeholder="Filter symbols…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              width: '100%', paddingLeft: 26, paddingRight: 8, paddingTop: 5, paddingBottom: 5,
              background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border-color)',
              color: 'var(--text-primary)', borderRadius: 'var(--r-md)', fontSize: 'var(--fs-xs)',
              outline: 'none', fontFamily: 'var(--font-sans)',
            }}
          />
        </div>
      </div>

      {/* Category Tabs */}
      <div className="watchlist-cat-tabs">
        {[['ALL', 'All'], ['CRYPTO', 'Crypto'], ['EQUITY', 'Equity'], ['ETF', 'ETF']].map(([key, label]) => (
          <button
            key={key}
            className={`cat-tab-btn ${key.toLowerCase()}${cat === key ? ' active' : ''}`}
            onClick={() => setCat(key)}
          >
            {label}
            <span style={{ marginLeft: 3, opacity: 0.65, fontWeight: 500 }}>({counts[key]})</span>
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="widget-content">
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-xs)' }}>
          <thead>
            <tr>
              {[
                { field: 'symbol',    label: 'Symbol', align: 'left'  },
                { field: null,        label: '',        align: 'left'  }, // sparkline
                { field: 'price',     label: 'Price',   align: 'right' },
                { field: 'change_24h',label: '24h%',    align: 'right' },
                { field: 'volume_24h',label: 'Vol',     align: 'right' },
              ].map(({ field, label, align }) => (
                <th key={label} onClick={field ? () => handleSort(field) : undefined}
                  style={{
                    position: 'sticky', top: 0, zIndex: 1, padding: '6px 8px',
                    fontSize: 'var(--fs-2xs)', color: (sort.field === field) ? '#60a5fa' : 'var(--text-muted)',
                    fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.4px',
                    textAlign: align, background: 'rgba(6,10,18,0.98)',
                    borderBottom: '1px solid var(--border-color)',
                    cursor: field ? 'pointer' : 'default', whiteSpace: 'nowrap',
                  }}>
                  {label}
                  {field && sort.field === field && (
                    <span style={{ marginLeft: 2, opacity: 0.7 }}>{sort.dir === 'asc' ? '↑' : '↓'}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {displaySymbols.map(symbol => {
              const info = tickerData[symbol];
              const flashState = flash[symbol];
              const flashCls = flashState ? (flashState.dir === 'up' ? 'flash-up' : 'flash-down') : '';
              const cat = getCategory(symbol);
              const catColor = ASSET_COLOR[cat];
              const isActive = symbol === activeSymbol;
              const sparkData = sparkRef.current[symbol] || [];
              const isUp = info?.change_24h >= 0;
              const dec = info ? getPriceDecimals(symbol, info.price) : 2;
              const shortSym = symbol.replace('USDT', '');

              return (
                <tr
                  key={symbol}
                  onClick={() => setActiveSymbol(symbol)}
                  style={{
                    cursor: 'pointer',
                    background: isActive ? 'rgba(37,99,235,0.08)' : undefined,
                    borderLeft: `3px solid ${isActive ? 'var(--color-accent-light)' : 'transparent'}`,
                    transition: 'background 0.1s',
                  }}
                  onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'rgba(255,255,255,0.03)'; }}
                  onMouseLeave={e => { e.currentTarget.style.background = isActive ? 'rgba(37,99,235,0.08)' : ''; }}
                >
                  {/* Symbol + category dot */}
                  <td style={{ padding: '5px 4px 5px 8px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                      <span style={{ width: 5, height: 5, borderRadius: '50%', background: catColor, flexShrink: 0, boxShadow: isActive ? `0 0 5px ${catColor}` : 'none' }} />
                      <span style={{ fontWeight: isActive ? 700 : 600, color: isActive ? '#fff' : 'var(--text-primary)', fontSize: 'var(--fs-xs)', letterSpacing: '0.2px' }}>
                        {shortSym}
                      </span>
                    </div>
                  </td>

                  {/* Sparkline */}
                  <td style={{ padding: '2px 2px' }}>
                    <MiniSparkline points={sparkData} isUp={isUp} />
                  </td>

                  {/* Price */}
                  <td
                    key={flashState?.key}
                    className={`${flashCls} num-mono`}
                    style={{
                      padding: '5px 6px', textAlign: 'right', fontWeight: 600,
                      color: flashState ? (flashState.dir === 'up' ? 'var(--color-up)' : 'var(--color-down)') : 'var(--text-primary)',
                      fontSize: 'var(--fs-xs)',
                    }}
                  >
                    {info ? info.price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec }) : '…'}
                  </td>

                  {/* 24h% */}
                  <td className={`num-mono ${isUp ? 'text-up' : 'text-down'}`} style={{ padding: '5px 6px', textAlign: 'right', fontWeight: 600, fontSize: 'var(--fs-2xs)' }}>
                    {info ? `${isUp ? '+' : ''}${Number(info.change_24h).toFixed(2)}%` : '—'}
                  </td>

                  {/* Volume */}
                  <td className="num-mono" style={{ padding: '5px 8px 5px 4px', textAlign: 'right', color: 'var(--text-muted)', fontSize: 'var(--fs-2xs)' }}>
                    {info ? fmtVol(info.volume_24h) : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {displaySymbols.length === 0 && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px 16px', color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>
            No symbols match your filter
          </div>
        )}
      </div>
    </div>
  );
}
