/**
 * TradeHistoryPanel.jsx
 * Trade blotter — works both as an embedded dock tab and as a full-screen overlay.
 *
 * Named export `TradeHistoryContent` is used by ResizableDock for the embedded tab.
 * The default export is the full-screen modal wrapper (used for legacy overlay).
 *
 * Features:
 *  - Statistics dashboard: Total P&L, Win Rate, Profit Factor, Best/Worst trade
 *  - Full sortable, filterable trade blotter with FIFO realized P&L per row
 *  - Filters: symbol, side, status, date range, search text
 *  - CSV export of filtered data
 *  - Expand-to-fullscreen button (managed by ResizableDock)
 */
import React, { useState, useMemo, useCallback, useEffect } from 'react';
import { useStore } from '../store/useStore';
import { sendWebSocketAction } from '../services/websocket';
import {
  X, Download, RefreshCw, ChevronUp, ChevronDown, ChevronsUpDown,
  TrendingUp, TrendingDown, BarChart2, Award, Target, Activity,
  CheckCircle2, XCircle, Clock, Filter,
} from 'lucide-react';

// ── Helpers ────────────────────────────────────────────────────────
const fmt = (n, dec = 2) =>
  n == null ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });

const fmtPnl = (n) => {
  if (n == null) return <span style={{ color: 'var(--text-muted)' }}>—</span>;
  const pos = n >= 0;
  return (
    <span className="num-mono" style={{ color: pos ? 'var(--color-up)' : 'var(--color-down)', fontWeight: 700 }}>
      {pos ? '+' : ''}${fmt(n)}
    </span>
  );
};

const STATUS_STYLES = {
  FILLED:   { color: '#10b981', bg: 'rgba(16,185,129,0.12)', icon: CheckCircle2 },
  CANCELED: { color: '#94a3b8', bg: 'rgba(148,163,184,0.08)', icon: XCircle },
  PENDING:  { color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', icon: Clock },
  REJECTED: { color: '#ef4444', bg: 'rgba(239,68,68,0.12)', icon: XCircle },
};

const SIDE_STYLES = {
  BUY:  { color: '#10b981', bg: 'rgba(16,185,129,0.15)' },
  SELL: { color: '#ef4444', bg: 'rgba(239,68,68,0.15)' },
};

const DATE_RANGES = [
  { label: 'Today', days: 1 },
  { label: '7D',    days: 7 },
  { label: '30D',   days: 30 },
  { label: 'All',   days: Infinity },
];

// ── Stat Card ──────────────────────────────────────────────────────
function StatCard({ label, value, sub, icon: Icon, positive, negative, accent }) {
  const col = positive ? 'var(--color-up)' : negative ? 'var(--color-down)' : accent || '#60a5fa';
  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 'var(--r-md)', padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 4,
      minWidth: 110, flex: '1 1 110px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 600 }}>{label}</span>
        {Icon && <Icon size={11} style={{ color: col, opacity: 0.8 }} />}
      </div>
      <span className="num-mono" style={{ fontSize: 'var(--fs-lg)', fontWeight: 800, color: col, lineHeight: 1 }}>{value}</span>
      {sub && <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>{sub}</span>}
    </div>
  );
}

// ── Sort Header ────────────────────────────────────────────────────
function SortTh({ children, field, sort, onSort, style }) {
  const active = sort.field === field;
  const Icon = active ? (sort.dir === 'asc' ? ChevronUp : ChevronDown) : ChevronsUpDown;
  return (
    <th
      onClick={() => onSort(field)}
      style={{
        padding: '8px 12px', fontSize: 'var(--fs-2xs)', color: active ? '#60a5fa' : 'var(--text-muted)',
        fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.4px',
        cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap',
        borderBottom: '1px solid rgba(255,255,255,0.07)',
        background: 'rgba(6,10,18,0.98)', position: 'sticky', top: 0, zIndex: 1,
        ...style,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
        {children}
        <Icon size={10} style={{ opacity: active ? 1 : 0.35, flexShrink: 0 }} />
      </div>
    </th>
  );
}

// ── Core History Content (shared between dock tab and fullscreen) ──
export function TradeHistoryContent({ embedded = true, onClose }) {
  const { tradeHistory, tradeStats } = useStore();

  const [loading,    setLoading]   = useState(false);
  const [symFilter,  setSymFilter] = useState('ALL');
  const [sideFilter, setSide]      = useState('ALL');
  const [statFilter, setStat]      = useState('ALL');
  const [dateRange,  setDateRange] = useState('All');
  const [search,     setSearch]    = useState('');
  const [sort,       setSort]      = useState({ field: 'timestamp', dir: 'desc' });

  const fetchHistory = useCallback(() => {
    setLoading(true);
    sendWebSocketAction('get_history');
  }, []);

  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  useEffect(() => {
    if (tradeHistory.length > 0 || tradeStats) setLoading(false);
  }, [tradeHistory, tradeStats]);

  const handleSort = (field) => {
    setSort(prev => ({ field, dir: prev.field === field ? (prev.dir === 'asc' ? 'desc' : 'asc') : 'desc' }));
  };

  const symbols = useMemo(() => ['ALL', ...Array.from(new Set(tradeHistory.map(t => t.symbol)))], [tradeHistory]);

  const selectedRange = DATE_RANGES.find(r => r.label === dateRange);
  const cutoff = selectedRange?.days === Infinity ? 0 : Date.now() - selectedRange.days * 86400000;

  const filtered = useMemo(() => {
    let rows = tradeHistory;
    if (cutoff > 0)           rows = rows.filter(t => t.timestamp >= cutoff);
    if (symFilter  !== 'ALL') rows = rows.filter(t => t.symbol === symFilter);
    if (sideFilter !== 'ALL') rows = rows.filter(t => t.side   === sideFilter);
    if (statFilter !== 'ALL') rows = rows.filter(t => t.status === statFilter);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      rows = rows.filter(t =>
        t.symbol.toLowerCase().includes(q) ||
        t.id.toLowerCase().includes(q) ||
        t.type.toLowerCase().includes(q)
      );
    }
    return [...rows].sort((a, b) => {
      let av = a[sort.field], bv = b[sort.field];
      if (av == null) av = sort.dir === 'asc' ? Infinity : -Infinity;
      if (bv == null) bv = sort.dir === 'asc' ? Infinity : -Infinity;
      if (typeof av === 'string') av = av.toLowerCase(), bv = String(bv).toLowerCase();
      return sort.dir === 'asc' ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
    });
  }, [tradeHistory, cutoff, symFilter, sideFilter, statFilter, search, sort]);

  const exportCSV = () => {
    const headers = ['Time', 'ID', 'Symbol', 'Type', 'Side', 'Status', 'Qty', 'Fill Price', 'Value', 'Cost Basis', 'Realized P&L'];
    const rows = filtered.map(t => [
      new Date(t.timestamp).toISOString(),
      t.id, t.symbol, t.type, t.side, t.status,
      t.filled_quantity ?? t.quantity,
      t.average_fill_price ?? t.price ?? '',
      t.trade_value ?? '', t.cost_basis ?? '', t.realized_pnl ?? '',
    ]);
    const csv = [headers, ...rows].map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `trade_history_${Date.now()}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

  const stats = tradeStats;
  const totalPnlPos = stats?.total_pnl >= 0;

  const headerStyle = embedded
    ? { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 12px', height: 36, flexShrink: 0, borderBottom: '1px solid rgba(255,255,255,0.06)', background: 'rgba(6,10,18,0.9)' }
    : { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 20px', height: 48, flexShrink: 0, borderBottom: '1px solid rgba(255,255,255,0.07)', background: '#080d14' };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* ── Panel header ── */}
      {!embedded && (
        <div style={headerStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Activity size={14} style={{ color: '#60a5fa' }} />
            <span style={{ fontWeight: 800, fontSize: 'var(--fs-md)', color: '#fff' }}>Transaction History</span>
            <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', background: 'rgba(255,255,255,0.05)', padding: '2px 8px', borderRadius: 'var(--r-sm)' }}>
              {filtered.length} records
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button onClick={() => { setLoading(true); fetchHistory(); }} style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '5px 10px', borderRadius: 'var(--r-sm)', cursor: 'pointer', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', color: 'var(--text-secondary)', fontSize: 'var(--fs-xs)', fontFamily: 'var(--font-sans)' }}>
              <RefreshCw size={11} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }} /> Refresh
            </button>
            <button onClick={exportCSV} style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '5px 10px', borderRadius: 'var(--r-sm)', cursor: 'pointer', background: 'rgba(37,99,235,0.12)', border: '1px solid rgba(37,99,235,0.35)', color: '#60a5fa', fontSize: 'var(--fs-xs)', fontFamily: 'var(--font-sans)' }}>
              <Download size={11} /> Export CSV
            </button>
            {onClose && (
              <button onClick={onClose} style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: '4px', display: 'flex', borderRadius: 'var(--r-sm)' }}>
                <X size={16} />
              </button>
            )}
          </div>
        </div>
      )}

      {/* Embedded sub-header with refresh + export */}
      {embedded && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 12px', flexShrink: 0, borderBottom: '1px solid rgba(255,255,255,0.05)', background: 'rgba(6,10,18,0.8)' }}>
          <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', marginRight: 2 }}>{filtered.length} records</span>
          <button onClick={() => { setLoading(true); fetchHistory(); }} style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '3px 8px', borderRadius: 'var(--r-sm)', cursor: 'pointer', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)', color: 'var(--text-muted)', fontSize: 'var(--fs-2xs)', fontFamily: 'var(--font-sans)' }}>
            <RefreshCw size={10} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }} /> Refresh
          </button>
          <button onClick={exportCSV} style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '3px 8px', borderRadius: 'var(--r-sm)', cursor: 'pointer', background: 'rgba(37,99,235,0.08)', border: '1px solid rgba(37,99,235,0.25)', color: '#60a5fa', fontSize: 'var(--fs-2xs)', fontFamily: 'var(--font-sans)' }}>
            <Download size={10} /> CSV
          </button>
        </div>
      )}

      {/* ── Stats row ── */}
      {stats && (
        <div style={{ display: 'flex', gap: 6, padding: '8px 12px', flexWrap: 'wrap', flexShrink: 0, borderBottom: '1px solid rgba(255,255,255,0.05)', background: 'rgba(6,10,18,0.7)' }}>
          <StatCard label="Realized P&L" icon={totalPnlPos ? TrendingUp : TrendingDown}
            value={`${stats.total_pnl >= 0 ? '+' : ''}$${fmt(stats.total_pnl)}`}
            positive={stats.total_pnl > 0} negative={stats.total_pnl < 0}
            sub={`${stats.wins}W / ${stats.losses}L`} />
          <StatCard label="Win Rate" icon={Target}
            value={`${fmt(stats.win_rate, 1)}%`}
            positive={stats.win_rate >= 50} negative={stats.win_rate < 40}
            sub={`${stats.total_sells} closed`} />
          <StatCard label="Profit Factor" icon={BarChart2}
            value={stats.profit_factor != null ? fmt(stats.profit_factor) : '—'}
            positive={stats.profit_factor > 1.5} negative={stats.profit_factor < 1}
            sub="Win÷Loss PnL" />
          <StatCard label="Best Trade" icon={Award}
            value={`+$${fmt(stats.best_trade)}`} positive={stats.best_trade > 0}
            sub={`Avg win: +$${fmt(stats.avg_win)}`} />
          <StatCard label="Worst Trade" icon={TrendingDown}
            value={`-$${fmt(Math.abs(stats.worst_trade))}`} negative={stats.worst_trade < 0}
            sub={`Avg loss: -$${fmt(Math.abs(stats.avg_loss))}`} />
          <StatCard label="Total Fills" icon={Activity}
            value={stats.total_fills} accent="#8b5cf6"
            sub={`Vol: $${fmt(stats.gross_volume)}`} />
        </div>
      )}

      {/* ── Filter bar ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', flexShrink: 0, borderBottom: '1px solid rgba(255,255,255,0.04)', background: 'rgba(6,10,18,0.85)', flexWrap: 'wrap' }}>
        <Filter size={11} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />

        {/* Date range */}
        {DATE_RANGES.map(r => (
          <button key={r.label} onClick={() => setDateRange(r.label)} style={{
            padding: '3px 8px', borderRadius: 'var(--r-sm)', cursor: 'pointer', fontSize: 'var(--fs-2xs)',
            fontWeight: 600, fontFamily: 'var(--font-sans)',
            background: dateRange === r.label ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.02)',
            border: `1px solid ${dateRange === r.label ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.06)'}`,
            color: dateRange === r.label ? '#fff' : 'var(--text-muted)',
          }}>{r.label}</button>
        ))}

        <div style={{ width: 1, height: 14, background: 'rgba(255,255,255,0.1)', flexShrink: 0 }} />

        {/* Symbol */}
        <select value={symFilter} onChange={e => setSymFilter(e.target.value)} style={{ background: '#0f172a', border: '1px solid rgba(255,255,255,0.08)', color: 'var(--text-secondary)', borderRadius: 'var(--r-sm)', padding: '3px 8px', fontSize: 'var(--fs-2xs)', cursor: 'pointer', fontFamily: 'var(--font-sans)', colorScheme: 'dark' }}>
          {symbols.map(s => <option key={s} value={s}>{s === 'ALL' ? 'All Symbols' : s}</option>)}
        </select>

        {/* Side */}
        {['ALL', 'BUY', 'SELL'].map(s => (
          <button key={s} onClick={() => setSide(s)} style={{
            padding: '3px 8px', borderRadius: 'var(--r-sm)', cursor: 'pointer', fontSize: 'var(--fs-2xs)', fontWeight: 600, fontFamily: 'var(--font-sans)',
            background: sideFilter === s ? (s === 'BUY' ? 'rgba(16,185,129,0.18)' : s === 'SELL' ? 'rgba(239,68,68,0.18)' : 'rgba(37,99,235,0.18)') : 'rgba(255,255,255,0.02)',
            border: `1px solid ${sideFilter === s ? (s === 'BUY' ? '#10b981' : s === 'SELL' ? '#ef4444' : '#3b82f6') : 'rgba(255,255,255,0.06)'}`,
            color: sideFilter === s ? (s === 'BUY' ? '#10b981' : s === 'SELL' ? '#ef4444' : '#60a5fa') : 'var(--text-muted)',
          }}>{s === 'ALL' ? 'All' : s}</button>
        ))}

        {/* Status */}
        {['ALL', 'FILLED', 'PENDING', 'CANCELED'].map(s => (
          <button key={s} onClick={() => setStat(s)} style={{
            padding: '3px 8px', borderRadius: 'var(--r-sm)', cursor: 'pointer', fontSize: 'var(--fs-2xs)', fontWeight: 600, fontFamily: 'var(--font-sans)',
            background: statFilter === s ? 'rgba(255,255,255,0.07)' : 'rgba(255,255,255,0.02)',
            border: `1px solid ${statFilter === s ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.06)'}`,
            color: statFilter === s ? 'var(--text-primary)' : 'var(--text-muted)',
          }}>{s === 'ALL' ? 'All Status' : s.charAt(0) + s.slice(1).toLowerCase()}</button>
        ))}

        {/* Search */}
        <input type="text" placeholder="Search…" value={search} onChange={e => setSearch(e.target.value)} style={{ marginLeft: 'auto', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', color: 'var(--text-primary)', borderRadius: 'var(--r-sm)', padding: '3px 10px', fontSize: 'var(--fs-2xs)', outline: 'none', width: 160, fontFamily: 'var(--font-sans)' }} />
      </div>

      {/* ── Table ── */}
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)', gap: 10 }}>
            <RefreshCw size={14} style={{ animation: 'spin 1s linear infinite' }} />Loading…
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 8 }}>
            <Activity size={28} style={{ color: 'var(--text-muted)', opacity: 0.3 }} />
            <span style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>No transactions match your filters</span>
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-xs)' }}>
            <thead>
              <tr>
                <SortTh field="timestamp" sort={sort} onSort={handleSort}>Time</SortTh>
                <SortTh field="symbol" sort={sort} onSort={handleSort}>Symbol</SortTh>
                <SortTh field="side" sort={sort} onSort={handleSort}>Side</SortTh>
                <SortTh field="type" sort={sort} onSort={handleSort}>Type</SortTh>
                <SortTh field="status" sort={sort} onSort={handleSort}>Status</SortTh>
                <SortTh field="filled_quantity" sort={sort} onSort={handleSort} style={{ textAlign: 'right' }}>Qty</SortTh>
                <SortTh field="average_fill_price" sort={sort} onSort={handleSort} style={{ textAlign: 'right' }}>Fill Price</SortTh>
                <SortTh field="trade_value" sort={sort} onSort={handleSort} style={{ textAlign: 'right' }}>Value</SortTh>
                <SortTh field="realized_pnl" sort={sort} onSort={handleSort} style={{ textAlign: 'right' }}>Realized P&L</SortTh>
              </tr>
            </thead>
            <tbody>
              {filtered.map((trade, i) => {
                const ss = STATUS_STYLES[trade.status] || STATUS_STYLES.PENDING;
                const sds = SIDE_STYLES[trade.side] || {};
                const StatusIcon = ss.icon;
                const qty = trade.filled_quantity ?? trade.quantity;
                const fp  = trade.average_fill_price || trade.price;
                const pdec = (trade.symbol?.includes('XRP') || trade.symbol?.includes('ADA') || trade.symbol?.includes('DOGE') || (fp && fp < 2.0)) ? 4 : 2;
                return (
                  <tr key={trade.id} style={{ background: i % 2 === 0 ? 'rgba(255,255,255,0.008)' : 'transparent' }}
                    onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.035)'}
                    onMouseLeave={e => e.currentTarget.style.background = i % 2 === 0 ? 'rgba(255,255,255,0.008)' : 'transparent'}>
                    <td style={{ padding: '7px 12px', color: 'var(--text-muted)', borderBottom: '1px solid rgba(255,255,255,0.025)' }}>
                      <span className="num-mono" style={{ fontSize: 'var(--fs-2xs)' }}>
                        {trade.timestamp ? new Date(trade.timestamp).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—'}
                      </span>
                    </td>
                    <td style={{ padding: '7px 12px', borderBottom: '1px solid rgba(255,255,255,0.025)' }}>
                      <span style={{ fontWeight: 700, color: '#fff' }}>{trade.symbol}</span>
                    </td>
                    <td style={{ padding: '7px 12px', borderBottom: '1px solid rgba(255,255,255,0.025)' }}>
                      <span style={{ padding: '2px 7px', borderRadius: 'var(--r-sm)', fontWeight: 700, fontSize: 'var(--fs-2xs)', background: sds.bg, color: sds.color }}>
                        {trade.side}
                      </span>
                    </td>
                    <td style={{ padding: '7px 12px', color: 'var(--text-secondary)', borderBottom: '1px solid rgba(255,255,255,0.025)' }}>{trade.type}</td>
                    <td style={{ padding: '7px 12px', borderBottom: '1px solid rgba(255,255,255,0.025)' }}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 7px', borderRadius: 'var(--r-sm)', fontWeight: 600, fontSize: 'var(--fs-2xs)', background: ss.bg, color: ss.color }}>
                        <StatusIcon size={9} />{trade.status}
                      </span>
                    </td>
                    <td className="num-mono" style={{ padding: '7px 12px', textAlign: 'right', borderBottom: '1px solid rgba(255,255,255,0.025)' }}>
                      {qty != null ? qty.toFixed(qty < 1 ? 6 : 4) : '—'}
                    </td>
                    <td className="num-mono" style={{ padding: '7px 12px', textAlign: 'right', borderBottom: '1px solid rgba(255,255,255,0.025)', fontWeight: 600 }}>
                      {fp ? `$${fmt(fp, pdec)}` : '—'}
                    </td>
                    <td className="num-mono" style={{ padding: '7px 12px', textAlign: 'right', borderBottom: '1px solid rgba(255,255,255,0.025)', color: 'var(--text-secondary)' }}>
                      {trade.trade_value ? `$${fmt(trade.trade_value)}` : '—'}
                    </td>
                    <td style={{ padding: '7px 12px 7px 12px', textAlign: 'right', borderBottom: '1px solid rgba(255,255,255,0.025)' }}>
                      {fmtPnl(trade.realized_pnl)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Footer ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '5px 12px', flexShrink: 0, borderTop: '1px solid rgba(255,255,255,0.05)', background: 'rgba(6,10,18,0.9)', fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
        <span>Showing {filtered.length} of {tradeHistory.length}</span>
        {filtered.length > 0 && (() => {
          const fp2 = filtered.filter(t => t.realized_pnl != null).reduce((s, t) => s + t.realized_pnl, 0);
          const fv  = filtered.reduce((s, t) => s + (t.trade_value || 0), 0);
          const pos = fp2 >= 0;
          return (
            <span style={{ display: 'flex', gap: 12 }}>
              <span>Filtered P&L: <span className="num-mono" style={{ color: pos ? 'var(--color-up)' : 'var(--color-down)', fontWeight: 700 }}>{pos ? '+' : ''}${fmt(fp2)}</span></span>
              <span>Vol: <span className="num-mono" style={{ color: 'var(--text-secondary)' }}>${fmt(fv)}</span></span>
            </span>
          );
        })()}
        <span style={{ opacity: 0.5 }}>FIFO · local time</span>
      </div>
    </div>
  );
}

// Default export is the TradeHistoryContent (for ResizableDock import)
export default TradeHistoryContent;
