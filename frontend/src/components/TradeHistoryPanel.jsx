/**
 * TradeHistoryPanel.jsx
 * Full-screen docked trade blotter — inspired by ThinkOrSwim Activity Monitor,
 * Interactive Brokers Fills Log, and TradingView's Strategy Tester.
 *
 * Features:
 *  - Statistics dashboard: Total P&L, Win Rate, Profit Factor, Best/Worst trade
 *  - Full sortable, filterable trade table with FIFO realized P&L per row
 *  - Filters: symbol, side, status, search
 *  - CSV export of filtered data
 *  - Animated slide-up panel with backdrop
 *  - Auto-requests fresh history from backend on open
 */
import React, { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { useStore } from '../store/useStore';
import { sendWebSocketAction } from '../services/websocket';
import {
  X, Download, RefreshCw, ChevronUp, ChevronDown, ChevronsUpDown,
  TrendingUp, TrendingDown, BarChart2, Award, Target, Activity,
  CheckCircle2, XCircle, Clock, Filter
} from 'lucide-react';

// ── Helpers ────────────────────────────────────────────────────────────────
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
  CANCELED: { color: '#94a3b8', bg: 'rgba(148,163,184,0.1)', icon: XCircle     },
  PENDING:  { color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', icon: Clock        },
  REJECTED: { color: '#ef4444', bg: 'rgba(239,68,68,0.12)',  icon: XCircle      },
};

const SIDE_STYLES = {
  BUY:  { color: '#10b981', bg: 'rgba(16,185,129,0.15)'  },
  SELL: { color: '#ef4444', bg: 'rgba(239,68,68,0.15)'   },
};

const SYMBOL_DOT = {
  BTCUSDT: '#f59e0b', ETHUSDT: '#8b5cf6',
  AAPL: '#34d399', TSLA: '#f87171', MSFT: '#06b6d4',
};

// ── Stat Card ──────────────────────────────────────────────────────────────
function StatCard({ label, value, sub, icon: Icon, positive, negative, accent }) {
  const col = positive ? 'var(--color-up)' : negative ? 'var(--color-down)' : accent || '#60a5fa';
  return (
    <div style={{
      background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)',
      borderRadius: '8px', padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '6px',
      minWidth: '130px', flex: '1 1 130px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 600 }}>
          {label}
        </span>
        {Icon && <Icon size={13} style={{ color: col, opacity: 0.8 }} />}
      </div>
      <span className="num-mono" style={{ fontSize: '1.1rem', fontWeight: '800', color: col, lineHeight: 1 }}>
        {value}
      </span>
      {sub && <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{sub}</span>}
    </div>
  );
}

// ── Sort Header ────────────────────────────────────────────────────────────
function SortTh({ children, field, sort, onSort, style }) {
  const active  = sort.field === field;
  const Icon    = active ? (sort.dir === 'asc' ? ChevronUp : ChevronDown) : ChevronsUpDown;
  return (
    <th
      onClick={() => onSort(field)}
      style={{
        padding: '10px 12px', fontSize: '0.7rem', color: active ? '#60a5fa' : 'var(--text-muted)',
        fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px',
        cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap',
        borderBottom: '1px solid rgba(255,255,255,0.08)',
        background: 'rgba(8,13,20,0.8)',
        position: 'sticky', top: 0, zIndex: 1,
        ...style,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
        {children}
        <Icon size={11} style={{ opacity: active ? 1 : 0.4, flexShrink: 0 }} />
      </div>
    </th>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────
export default function TradeHistoryPanel({ onClose }) {
  const { tradeHistory, tradeStats } = useStore();

  const [loading,   setLoading]   = useState(false);
  const [symFilter, setSymFilter] = useState('ALL');
  const [sideFilter,setSideFilter]= useState('ALL');
  const [statFilter,setStatFilter]= useState('ALL');
  const [search,    setSearch]    = useState('');
  const [sort,      setSort]      = useState({ field: 'timestamp', dir: 'desc' });

  // Request history from backend when panel opens
  const fetchHistory = useCallback(() => {
    setLoading(true);
    sendWebSocketAction('get_history');
    // Loading state clears once store updates via subscription
  }, []);

  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  // Clear loading once data arrives
  useEffect(() => {
    if (tradeHistory.length > 0 || tradeStats) setLoading(false);
  }, [tradeHistory, tradeStats]);

  // ── Sorting ──────────────────────────────────────────────────────────────
  const handleSort = (field) => {
    setSort(prev => ({
      field,
      dir: prev.field === field ? (prev.dir === 'asc' ? 'desc' : 'asc') : 'desc',
    }));
  };

  // ── Filtering + Sorting ──────────────────────────────────────────────────
  const symbols = useMemo(() => {
    const s = new Set(tradeHistory.map(t => t.symbol));
    return ['ALL', ...Array.from(s)];
  }, [tradeHistory]);

  const filtered = useMemo(() => {
    let rows = tradeHistory;
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
  }, [tradeHistory, symFilter, sideFilter, statFilter, search, sort]);

  // ── CSV Export ───────────────────────────────────────────────────────────
  const exportCSV = () => {
    const headers = ['Time','ID','Symbol','Type','Side','Status','Qty','Fill Price','Value','Cost Basis','Realized P&L'];
    const rows = filtered.map(t => [
      t.timestamp,
      t.id,
      t.symbol,
      t.type,
      t.side,
      t.status,
      t.filled_quantity ?? t.quantity,
      t.average_fill_price ?? t.price ?? '',
      t.trade_value ?? '',
      t.cost_basis ?? '',
      t.realized_pnl ?? '',
    ]);
    const csv = [headers, ...rows].map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url; a.download = `trade_history_${Date.now()}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

  const stats = tradeStats;
  const totalPnlPos = stats && stats.total_pnl >= 0;

  return (
    <>
      {/* ── Backdrop ──────────────────────────────────────────────────── */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0, zIndex: 300,
          background: 'rgba(0,0,0,0.55)',
          backdropFilter: 'blur(2px)',
          animation: 'fadeIn 0.2s ease',
        }}
      />

      {/* ── Panel ─────────────────────────────────────────────────────── */}
      <div
        style={{
          position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 301,
          height: '62vh', display: 'flex', flexDirection: 'column',
          background: '#07090f',
          borderTop: '1px solid rgba(255,255,255,0.1)',
          boxShadow: '0 -24px 80px rgba(0,0,0,0.8)',
          animation: 'slideUp 0.25s cubic-bezier(0.16,1,0.3,1)',
        }}
      >
        {/* ── Panel header ──────────────────────────────────────────────── */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0 20px', height: '48px', flexShrink: 0,
          borderBottom: '1px solid rgba(255,255,255,0.07)',
          background: '#080d14',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Activity size={15} style={{ color: '#60a5fa' }} />
            <span style={{ fontWeight: '800', fontSize: '0.88rem', color: '#fff', letterSpacing: '0.3px' }}>
              Transaction History
            </span>
            <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', background: 'rgba(255,255,255,0.06)', padding: '2px 8px', borderRadius: '4px' }}>
              {filtered.length} record{filtered.length !== 1 ? 's' : ''}
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <button
              onClick={() => { setLoading(true); fetchHistory(); }}
              title="Refresh"
              style={{
                display: 'flex', alignItems: 'center', gap: '5px',
                padding: '5px 10px', borderRadius: '5px', cursor: 'pointer',
                background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)',
                color: 'var(--text-secondary)', fontSize: '0.75rem', fontFamily: 'var(--font-sans)',
              }}
            >
              <RefreshCw size={12} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }} />
              Refresh
            </button>
            <button
              onClick={exportCSV}
              title="Export CSV"
              style={{
                display: 'flex', alignItems: 'center', gap: '5px',
                padding: '5px 10px', borderRadius: '5px', cursor: 'pointer',
                background: 'rgba(37,99,235,0.15)', border: '1px solid rgba(37,99,235,0.4)',
                color: '#60a5fa', fontSize: '0.75rem', fontFamily: 'var(--font-sans)',
              }}
            >
              <Download size={12} />
              Export CSV
            </button>
            <button
              onClick={onClose}
              style={{
                background: 'transparent', border: 'none', cursor: 'pointer',
                color: 'var(--text-muted)', padding: '4px', display: 'flex',
                borderRadius: '4px',
              }}
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {/* ── Stats row ─────────────────────────────────────────────────── */}
        {stats && (
          <div style={{
            display: 'flex', gap: '8px', padding: '10px 16px',
            flexWrap: 'wrap', flexShrink: 0,
            borderBottom: '1px solid rgba(255,255,255,0.06)',
            background: 'rgba(8,13,20,0.6)',
          }}>
            <StatCard
              label="Realized P&L" icon={totalPnlPos ? TrendingUp : TrendingDown}
              value={`${stats.total_pnl >= 0 ? '+' : ''}$${fmt(stats.total_pnl)}`}
              positive={stats.total_pnl > 0} negative={stats.total_pnl < 0}
              sub={`${stats.wins}W / ${stats.losses}L`}
            />
            <StatCard
              label="Win Rate" icon={Target}
              value={`${fmt(stats.win_rate, 1)}%`}
              positive={stats.win_rate >= 50} negative={stats.win_rate < 40}
              sub={`${stats.total_sells} closed trades`}
            />
            <StatCard
              label="Profit Factor" icon={BarChart2}
              value={stats.profit_factor != null ? fmt(stats.profit_factor) : '—'}
              positive={stats.profit_factor > 1.5} negative={stats.profit_factor < 1}
              sub="Win PnL / Loss PnL"
            />
            <StatCard
              label="Best Trade" icon={Award}
              value={`+$${fmt(stats.best_trade)}`}
              positive={stats.best_trade > 0}
              sub={`Avg win: +$${fmt(stats.avg_win)}`}
            />
            <StatCard
              label="Worst Trade" icon={TrendingDown}
              value={`-$${fmt(Math.abs(stats.worst_trade))}`}
              negative={stats.worst_trade < 0}
              sub={`Avg loss: -$${fmt(Math.abs(stats.avg_loss))}`}
            />
            <StatCard
              label="Total Fills" icon={Activity}
              value={stats.total_fills}
              accent="#8b5cf6"
              sub={`Vol: $${fmt(stats.gross_volume)}`}
            />
          </div>
        )}

        {/* ── Filter bar ────────────────────────────────────────────────── */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 16px',
          flexShrink: 0, borderBottom: '1px solid rgba(255,255,255,0.05)',
          background: 'rgba(8,13,20,0.8)', flexWrap: 'wrap',
        }}>
          <Filter size={13} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />

          {/* Symbol filter */}
          <select
            value={symFilter}
            onChange={e => setSymFilter(e.target.value)}
            style={{
              background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)',
              color: 'var(--text-secondary)', borderRadius: '5px', padding: '4px 10px',
              fontSize: '0.75rem', cursor: 'pointer', fontFamily: 'var(--font-sans)',
            }}
          >
            {symbols.map(s => <option key={s} value={s}>{s === 'ALL' ? 'All Symbols' : s}</option>)}
          </select>

          {/* Side filter */}
          {['ALL','BUY','SELL'].map(s => (
            <button key={s} onClick={() => setSideFilter(s)} style={{
              padding: '4px 10px', borderRadius: '5px', cursor: 'pointer', fontSize: '0.72rem',
              fontWeight: 600, fontFamily: 'var(--font-sans)',
              background: sideFilter === s
                ? (s === 'BUY' ? 'rgba(16,185,129,0.2)' : s === 'SELL' ? 'rgba(239,68,68,0.2)' : 'rgba(37,99,235,0.2)')
                : 'rgba(255,255,255,0.04)',
              border: `1px solid ${sideFilter === s
                ? (s === 'BUY' ? '#10b981' : s === 'SELL' ? '#ef4444' : '#3b82f6')
                : 'rgba(255,255,255,0.1)'}`,
              color: sideFilter === s
                ? (s === 'BUY' ? '#10b981' : s === 'SELL' ? '#ef4444' : '#60a5fa')
                : 'var(--text-muted)',
            }}>
              {s === 'ALL' ? 'All Sides' : s}
            </button>
          ))}

          {/* Status filter */}
          {['ALL','FILLED','PENDING','CANCELED'].map(s => (
            <button key={s} onClick={() => setStatFilter(s)} style={{
              padding: '4px 10px', borderRadius: '5px', cursor: 'pointer', fontSize: '0.72rem',
              fontWeight: 600, fontFamily: 'var(--font-sans)',
              background: statFilter === s ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.03)',
              border: `1px solid ${statFilter === s ? 'rgba(255,255,255,0.25)' : 'rgba(255,255,255,0.08)'}`,
              color: statFilter === s ? 'var(--text-primary)' : 'var(--text-muted)',
            }}>
              {s === 'ALL' ? 'All Status' : s.charAt(0) + s.slice(1).toLowerCase()}
            </button>
          ))}

          {/* Search */}
          <input
            type="text"
            placeholder="Search symbol, ID…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              marginLeft: 'auto', background: 'rgba(255,255,255,0.05)',
              border: '1px solid rgba(255,255,255,0.1)', color: 'var(--text-primary)',
              borderRadius: '5px', padding: '4px 12px', fontSize: '0.75rem',
              outline: 'none', width: '180px', fontFamily: 'var(--font-sans)',
            }}
          />
        </div>

        {/* ── Table ─────────────────────────────────────────────────────── */}
        <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
          {loading ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)', gap: '10px' }}>
              <RefreshCw size={16} style={{ animation: 'spin 1s linear infinite' }} />
              Loading trade history…
            </div>
          ) : filtered.length === 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: '8px' }}>
              <Activity size={32} style={{ color: 'var(--text-muted)', opacity: 0.4 }} />
              <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>No transactions match your filters</span>
              <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem', opacity: 0.6 }}>Place your first trade to see history here</span>
            </div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
              <thead>
                <tr>
                  <SortTh field="timestamp"          sort={sort} onSort={handleSort}>Time</SortTh>
                  <SortTh field="symbol"             sort={sort} onSort={handleSort}>Symbol</SortTh>
                  <SortTh field="side"               sort={sort} onSort={handleSort}>Side</SortTh>
                  <SortTh field="type"               sort={sort} onSort={handleSort}>Type</SortTh>
                  <SortTh field="status"             sort={sort} onSort={handleSort}>Status</SortTh>
                  <SortTh field="filled_quantity"    sort={sort} onSort={handleSort} style={{ textAlign: 'right' }}>Qty</SortTh>
                  <SortTh field="average_fill_price" sort={sort} onSort={handleSort} style={{ textAlign: 'right' }}>Fill Price</SortTh>
                  <SortTh field="trade_value"        sort={sort} onSort={handleSort} style={{ textAlign: 'right' }}>Value</SortTh>
                  <SortTh field="cost_basis"         sort={sort} onSort={handleSort} style={{ textAlign: 'right' }}>Cost Basis</SortTh>
                  <SortTh field="realized_pnl"       sort={sort} onSort={handleSort} style={{ textAlign: 'right' }}>Realized P&L</SortTh>
                </tr>
              </thead>
              <tbody>
                {filtered.map((trade, i) => {
                  const ss  = STATUS_STYLES[trade.status] || STATUS_STYLES.PENDING;
                  const sds = SIDE_STYLES[trade.side]     || {};
                  const dot = SYMBOL_DOT[trade.symbol];
                  const StatusIcon = ss.icon;
                  const qty  = trade.filled_quantity ?? trade.quantity;
                  const fp   = trade.average_fill_price || trade.price;
                  return (
                    <tr
                      key={trade.id}
                      style={{
                        background: i % 2 === 0 ? 'rgba(255,255,255,0.01)' : 'transparent',
                        transition: 'background 0.1s',
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.04)'}
                      onMouseLeave={e => e.currentTarget.style.background = i % 2 === 0 ? 'rgba(255,255,255,0.01)' : 'transparent'}
                    >
                      {/* Time */}
                      <td style={{ padding: '9px 12px', color: 'var(--text-muted)', whiteSpace: 'nowrap', borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                        <span className="num-mono" style={{ fontSize: '0.72rem' }}>
                          {trade.timestamp ? new Date(trade.timestamp).toLocaleString('en-GB', { day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit', second:'2-digit' }) : '—'}
                        </span>
                      </td>

                      {/* Symbol */}
                      <td style={{ padding: '9px 12px', borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: dot || '#94a3b8', flexShrink: 0, boxShadow: `0 0 5px ${dot || '#94a3b8'}` }} />
                          <span style={{ fontWeight: 700, color: '#fff' }}>{trade.symbol}</span>
                        </div>
                      </td>

                      {/* Side */}
                      <td style={{ padding: '9px 12px', borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                        <span style={{
                          padding: '2px 8px', borderRadius: '4px', fontWeight: 700, fontSize: '0.72rem',
                          background: sds.bg, color: sds.color,
                        }}>
                          {trade.side}
                        </span>
                      </td>

                      {/* Type */}
                      <td style={{ padding: '9px 12px', color: 'var(--text-secondary)', borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                        {trade.type}
                      </td>

                      {/* Status */}
                      <td style={{ padding: '9px 12px', borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                        <span style={{
                          display: 'inline-flex', alignItems: 'center', gap: '5px',
                          padding: '2px 8px', borderRadius: '4px', fontWeight: 600, fontSize: '0.7rem',
                          background: ss.bg, color: ss.color,
                        }}>
                          <StatusIcon size={10} />
                          {trade.status}
                        </span>
                      </td>

                      {/* Qty */}
                      <td style={{ padding: '9px 12px', textAlign: 'right', borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                        <span className="num-mono" style={{ color: 'var(--text-primary)' }}>
                          {qty != null ? qty.toFixed(qty < 1 ? 6 : 4) : '—'}
                        </span>
                      </td>

                      {/* Fill Price */}
                      <td style={{ padding: '9px 12px', textAlign: 'right', borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                        <span className="num-mono" style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
                          {fp ? `$${fmt(fp)}` : '—'}
                        </span>
                      </td>

                      {/* Value */}
                      <td style={{ padding: '9px 12px', textAlign: 'right', borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                        <span className="num-mono" style={{ color: 'var(--text-secondary)' }}>
                          {trade.trade_value ? `$${fmt(trade.trade_value)}` : '—'}
                        </span>
                      </td>

                      {/* Cost Basis */}
                      <td style={{ padding: '9px 12px', textAlign: 'right', borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                        <span className="num-mono" style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>
                          {trade.cost_basis ? `$${fmt(trade.cost_basis)}` : '—'}
                        </span>
                      </td>

                      {/* Realized P&L */}
                      <td style={{ padding: '9px 16px 9px 12px', textAlign: 'right', borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                        {fmtPnl(trade.realized_pnl)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* ── Footer summary ─────────────────────────────────────────────── */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '6px 16px', flexShrink: 0, borderTop: '1px solid rgba(255,255,255,0.06)',
          background: '#080d14', fontSize: '0.72rem', color: 'var(--text-muted)',
        }}>
          <span>Showing {filtered.length} of {tradeHistory.length} transactions</span>
          {filtered.length > 0 && (() => {
            const filteredPnl = filtered
              .filter(t => t.realized_pnl != null)
              .reduce((s, t) => s + t.realized_pnl, 0);
            const filteredVol = filtered.reduce((s, t) => s + (t.trade_value || 0), 0);
            const pos = filteredPnl >= 0;
            return (
              <span style={{ display: 'flex', gap: '16px' }}>
                <span>
                  Filtered P&L:{' '}
                  <span className="num-mono" style={{ color: pos ? 'var(--color-up)' : 'var(--color-down)', fontWeight: 700 }}>
                    {pos ? '+' : ''}${fmt(filteredPnl)}
                  </span>
                </span>
                <span>
                  Filtered Vol:{' '}
                  <span className="num-mono" style={{ color: 'var(--text-secondary)' }}>
                    ${fmt(filteredVol)}
                  </span>
                </span>
              </span>
            );
          })()}
          <span style={{ opacity: 0.5 }}>FIFO cost-basis · Times in local timezone</span>
        </div>
      </div>
    </>
  );
}
