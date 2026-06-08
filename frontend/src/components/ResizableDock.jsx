/**
 * ResizableDock.jsx
 * Bottom docked panel with 6 tabs:
 *   Positions | Orders | Balances | Algo Bot | History | Equity Curve
 *
 * Features:
 *  - Drag-to-resize via top handle (persists to localStorage)
 *  - History tab can be expanded to full-screen overlay
 *  - Badge counts on Positions and Orders tabs
 */
import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useStore } from '../store/useStore';
import { sendWebSocketAction } from '../services/websocket';
import {
  Briefcase, List, Landmark, Cpu, Activity, TrendingUp,
  Play, Square, Settings, Trash2, XSquare, Maximize2, Minimize2,
  RefreshCw, Download, Filter, X, CheckCircle2, XCircle, Clock,
  ChevronUp, ChevronDown, ChevronsUpDown, Award, Target, BarChart2,
} from 'lucide-react';
import EquityCurveTab from './EquityCurveTab';
import TradeHistoryContent from './TradeHistoryPanel';

const DOCK_MIN = 160;
const DOCK_MAX = 560;
const DOCK_DEFAULT = 320;
const STORAGE_KEY = 'terminal_dock_height';

// ── Tiny formatters ───────────────────────────────────────────────
const priceDecimals = (sym, price) =>
  (sym?.includes('XRP') || sym?.includes('ADA') || sym?.includes('DOGE') || (price != null && price < 2.0)) ? 4 : 2;

const fmtP = (n, d = 2) =>
  n == null ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

function PnlSpan({ value }) {
  if (value == null) return <span style={{ color: 'var(--text-muted)' }}>—</span>;
  const pos = value >= 0;
  return (
    <span className="num-mono" style={{ color: pos ? 'var(--color-up)' : 'var(--color-down)', fontWeight: 700 }}>
      {pos ? '+' : ''}${fmtP(value)}
    </span>
  );
}

// ── Positions Tab ─────────────────────────────────────────────────
function PositionsTab() {
  const { positions, tickerData, activeSymbol } = useStore();
  const entries = Object.entries(positions);

  const handleClose = (sym, pos) => {
    sendWebSocketAction('place_order', {
      symbol: sym,
      type: 'MARKET',
      side: pos.size > 0 ? 'SELL' : 'BUY',
      quantity: Math.abs(pos.size),
    });
  };

  if (entries.length === 0) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 8, color: 'var(--text-muted)' }}>
        <Briefcase size={24} style={{ opacity: 0.3 }} />
        <span style={{ fontSize: 'var(--fs-sm)' }}>No open positions</span>
      </div>
    );
  }

  return (
    <table className="terminal-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Side</th>
          <th style={{ textAlign: 'right' }}>Size</th>
          <th style={{ textAlign: 'right' }}>Avg Entry</th>
          <th style={{ textAlign: 'right' }}>Mark Price</th>
          <th style={{ textAlign: 'right' }}>Unrealized P&L</th>
          <th style={{ textAlign: 'right' }}>% Return</th>
          <th style={{ textAlign: 'center' }}>Close</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([sym, pos]) => {
          const mark = tickerData[sym]?.price ?? pos.avg_price;
          const uPnl = pos.size * (mark - pos.avg_price);
          const pct  = pos.avg_price > 0 ? ((mark - pos.avg_price) / pos.avg_price) * 100 : 0;
          const isLong = pos.size >= 0;
          const dec = priceDecimals(sym, Math.max(mark, pos.avg_price));
          const isActive = sym === activeSymbol;

          return (
            <tr key={sym} style={{ background: isActive ? 'rgba(37,99,235,0.05)' : undefined }}>
              <td>
                <span style={{ fontWeight: 700, color: isActive ? '#93c5fd' : '#fff' }}>{sym}</span>
                {(pos.stop_loss_price || pos.take_profit_price) && (
                  <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', marginTop: 2, display: 'flex', gap: 6 }}>
                    {pos.stop_loss_price && <span style={{ color: '#ef4444' }}>SL:{pos.stop_loss_price.toFixed(dec)}</span>}
                    {pos.take_profit_price && <span style={{ color: '#10b981' }}>TP:{pos.take_profit_price.toFixed(dec)}</span>}
                  </div>
                )}
              </td>
              <td>
                <span className={`badge ${isLong ? 'badge-buy' : 'badge-sell'}`}>
                  {isLong ? 'LONG' : 'SHORT'}
                </span>
              </td>
              <td className="num-mono" style={{ textAlign: 'right' }}>
                {Math.abs(pos.size).toLocaleString(undefined, { minimumFractionDigits: 4 })}
              </td>
              <td className="num-mono" style={{ textAlign: 'right' }}>
                {pos.avg_price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
              </td>
              <td className="num-mono" style={{ textAlign: 'right' }}>
                {mark.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
              </td>
              <td className="num-mono" style={{ textAlign: 'right', fontWeight: 700, color: uPnl >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                {uPnl >= 0 ? '+' : ''}{fmtP(uPnl)}
              </td>
              <td className="num-mono" style={{ textAlign: 'right', fontWeight: 600, color: pct >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
              </td>
              <td style={{ textAlign: 'center' }}>
                <button
                  onClick={() => handleClose(sym, pos)}
                  title={`Close ${sym} position`}
                  style={{
                    padding: '3px 10px', borderRadius: 'var(--r-sm)', border: '1px solid rgba(239,68,68,0.4)',
                    background: 'rgba(239,68,68,0.1)', color: '#f87171', fontSize: 'var(--fs-2xs)',
                    fontWeight: 700, cursor: 'pointer', fontFamily: 'var(--font-sans)',
                    transition: 'var(--transition)', letterSpacing: '0.3px',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.background = 'rgba(239,68,68,0.25)'; }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'rgba(239,68,68,0.1)'; }}
                >
                  CLOSE
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// ── Orders Tab ────────────────────────────────────────────────────
function OrdersTab() {
  const { orders } = useStore();
  const active = orders.filter(o => o.status === 'PENDING');

  if (active.length === 0) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 8, color: 'var(--text-muted)' }}>
        <List size={24} style={{ opacity: 0.3 }} />
        <span style={{ fontSize: 'var(--fs-sm)' }}>No pending orders</span>
      </div>
    );
  }

  return (
    <table className="terminal-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Type</th>
          <th>Side</th>
          <th style={{ textAlign: 'right' }}>Price</th>
          <th style={{ textAlign: 'right' }}>Qty</th>
          <th style={{ textAlign: 'right' }}>Value</th>
          <th style={{ textAlign: 'center' }}>Cancel</th>
        </tr>
      </thead>
      <tbody>
        {active.map(ord => {
          const dec = priceDecimals(ord.symbol, ord.price);
          const isBuy = ord.side === 'BUY';
          const value = (ord.price || 0) * ord.quantity;
          return (
            <tr key={ord.id}>
              <td style={{ fontWeight: 700 }}>{ord.symbol}</td>
              <td style={{ color: 'var(--text-secondary)', fontSize: 'var(--fs-xs)' }}>{ord.type}</td>
              <td><span className={`badge ${isBuy ? 'badge-buy' : 'badge-sell'}`}>{ord.side}</span></td>
              <td className="num-mono" style={{ textAlign: 'right' }}>
                {ord.price ? ord.price.toFixed(dec) : 'MKT'}
              </td>
              <td className="num-mono" style={{ textAlign: 'right' }}>
                {ord.quantity.toLocaleString(undefined, { minimumFractionDigits: 4 })}
              </td>
              <td className="num-mono" style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>
                ${fmtP(value)}
              </td>
              <td style={{ textAlign: 'center' }}>
                <button className="btn-icon" onClick={() => sendWebSocketAction('cancel_order', { order_id: ord.id })} title="Cancel order">
                  <XSquare size={15} style={{ color: 'var(--color-down)' }} />
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// ── Balances Tab ──────────────────────────────────────────────────
function BalancesTab() {
  const { balances } = useStore();
  const entries = Object.entries(balances);

  if (entries.length === 0) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>
        Loading balances…
      </div>
    );
  }

  return (
    <table className="terminal-table">
      <thead>
        <tr>
          <th>Asset</th>
          <th style={{ textAlign: 'right' }}>Total Balance</th>
          <th style={{ textAlign: 'right' }}>Locked</th>
          <th style={{ textAlign: 'right' }}>Available</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([asset, bal]) => {
          const avail = bal.balance - bal.locked;
          const dec = asset === 'USD' || asset === 'USDT' ? 2 : 6;
          return (
            <tr key={asset}>
              <td style={{ fontWeight: 700 }}>{asset}</td>
              <td className="num-mono" style={{ textAlign: 'right' }}>
                {bal.balance.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
              </td>
              <td className="num-mono" style={{ textAlign: 'right', color: 'var(--text-muted)' }}>
                {bal.locked.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
              </td>
              <td className="num-mono" style={{ textAlign: 'right', fontWeight: 700, color: avail > 0 ? '#fff' : 'var(--text-muted)' }}>
                {avail.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// ── Algo Bot Tab ──────────────────────────────────────────────────
function AlgoTab() {
  const {
    isBotRunning, botStrategy, botConfig, activeSymbol,
    positions, startBot, stopBot, setBotStrategy, updateBotConfig, clearBotLogs, botLogs,
  } = useStore();

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 12, padding: 12, height: '100%', overflow: 'hidden', minHeight: 0 }}>
      {/* Left: config */}
      <div style={{
        display: 'flex', flexDirection: 'column', gap: 10,
        background: 'rgba(255,255,255,0.015)', border: '1px solid rgba(255,255,255,0.06)',
        borderRadius: 'var(--r-lg)', padding: 12, overflowY: 'auto',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, borderBottom: '1px solid rgba(255,255,255,0.07)', paddingBottom: 6 }}>
          <Settings size={13} style={{ color: 'var(--color-accent-light)' }} />
          <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px', color: '#fff' }}>Bot Parameters</span>
        </div>

        <div className="terminal-input-group" style={{ margin: 0 }}>
          <label className="terminal-label">Select Strategy</label>
          <select
            value={botStrategy} onChange={e => setBotStrategy(e.target.value)} disabled={isBotRunning}
            style={{ width: '100%', background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border-color)', color: '#fff', borderRadius: 'var(--r-md)', padding: '7px 10px', fontSize: 'var(--fs-xs)', cursor: isBotRunning ? 'not-allowed' : 'pointer', fontFamily: 'var(--font-sans)', colorScheme: 'dark' }}
          >
            <option value="EMA_CROSS">EMA Crossover (9/21)</option>
            <option value="RSI_MEAN_REV">RSI Mean Reversion (14)</option>
            <option value="MACD_TREND">MACD Trend Follower</option>
          </select>
        </div>

        {[
          { label: 'Order Size (Qty)', key: 'quantity', suffix: 'units', step: 'any' },
          { label: 'Auto Stop Loss', key: 'stopLossPercent', suffix: '%', step: '0.1' },
          { label: 'Auto Take Profit', key: 'takeProfitPercent', suffix: '%', step: '0.1' },
        ].map(({ label, key, suffix, step }) => (
          <div className="terminal-input-group" key={key} style={{ margin: 0 }}>
            <label className="terminal-label">{label}</label>
            <div className="terminal-input-wrapper">
              <input
                type="number" step={step} disabled={isBotRunning}
                value={botConfig?.[key] || ''}
                onChange={e => updateBotConfig({ [key]: parseFloat(e.target.value) || 0 })}
                className="terminal-input"
                style={{ padding: '6px 36px 6px 10px', fontSize: 'var(--fs-xs)', height: 'auto' }}
              />
              <span className="terminal-input-suffix" style={{ fontSize: 'var(--fs-2xs)' }}>{suffix}</span>
            </div>
          </div>
        ))}

        <button
          onClick={() => isBotRunning ? stopBot() : startBot()}
          className="terminal-btn"
          style={{
            marginTop: 'auto',
            background: isBotRunning ? 'rgba(239,68,68,0.12)' : 'rgba(16,185,129,0.12)',
            border: `1px solid ${isBotRunning ? 'rgba(239,68,68,0.4)' : 'rgba(16,185,129,0.4)'}`,
            color: isBotRunning ? 'var(--color-down)' : 'var(--color-up)',
            boxShadow: isBotRunning ? '0 0 12px rgba(239,68,68,0.15)' : '0 0 12px rgba(16,185,129,0.15)',
            fontWeight: 700, padding: '9px',
          }}
        >
          {isBotRunning ? <><Square size={13} fill="currentColor" /> STOP BOT</> : <><Play size={13} fill="currentColor" /> START BOT</>}
        </button>
      </div>

      {/* Right: console */}
      <div style={{ display: 'flex', flexDirection: 'column', background: 'rgba(2,5,10,0.9)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: 'var(--r-lg)', padding: 12, overflow: 'hidden', minHeight: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid rgba(255,255,255,0.07)', paddingBottom: 6, flexShrink: 0, marginBottom: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Cpu size={13} style={{ color: isBotRunning ? '#10b981' : 'var(--text-muted)' }} />
            <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px', color: '#fff' }}>Bot Log</span>
            <span style={{
              fontSize: 'var(--fs-2xs)', padding: '1px 7px', borderRadius: 'var(--r-sm)',
              background: isBotRunning ? 'rgba(16,185,129,0.12)' : 'rgba(255,255,255,0.04)',
              color: isBotRunning ? '#10b981' : 'var(--text-muted)', fontWeight: 600,
            }}>
              {isBotRunning ? `SCANNING ${activeSymbol}` : 'IDLE'}
            </span>
          </div>
          <button className="btn-icon" onClick={clearBotLogs} title="Clear log"><Trash2 size={12} /></button>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-2xs)', color: '#64748b', display: 'flex', flexDirection: 'column-reverse', gap: 3 }}>
          {botLogs.length === 0
            ? <div style={{ margin: 'auto', opacity: 0.4, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
                <Cpu size={20} />
                <span>Bot console is empty</span>
              </div>
            : botLogs.map((log, i) => {
                let c = '#64748b';
                if (log.includes('BUY') || log.includes('Profit')) c = '#10b981';
                else if (log.includes('SELL') || log.includes('Loss') || log.includes('Stop')) c = '#ef4444';
                else if (log.includes('ACTIVATED') || log.includes('Config') || log.includes('Running')) c = '#60a5fa';
                return <div key={i} style={{ color: c, whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>{log}</div>;
              })
          }
        </div>
      </div>
    </div>
  );
}

// ── Main ResizableDock ────────────────────────────────────────────
export default function ResizableDock({ setDockHeight: setParentDockHeight }) {
  const { positions, orders, tradeHistory, isBotRunning } = useStore();
  const [activeTab, setActiveTab] = useState('positions');
  const [dockH, setDockH]   = useState(() => {
    try { return parseInt(localStorage.getItem(STORAGE_KEY)) || DOCK_DEFAULT; }
    catch { return DOCK_DEFAULT; }
  });
  const [historyFullscreen, setHistoryFullscreen] = useState(false);
  const isDragging = useRef(false);
  const startY    = useRef(0);
  const startH    = useRef(0);

  // Sync dock height to parent App so CSS variable can update
  useEffect(() => {
    setParentDockHeight(dockH);
  }, [dockH, setParentDockHeight]);

  const pendingOrders = orders.filter(o => o.status === 'PENDING').length;
  const posCount = Object.keys(positions).length;

  const onMouseDown = useCallback(e => {
    isDragging.current = true;
    startY.current = e.clientY;
    startH.current = dockH;
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';
  }, [dockH]);

  useEffect(() => {
    const onMove = e => {
      if (!isDragging.current) return;
      const delta = startY.current - e.clientY;
      const newH = Math.max(DOCK_MIN, Math.min(DOCK_MAX, startH.current + delta));
      setDockH(newH);
    };
    const onUp = () => {
      if (isDragging.current) {
        isDragging.current = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        try { localStorage.setItem(STORAGE_KEY, String(dockH)); } catch {}
      }
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
  }, [dockH]);

  const TABS = [
    { id: 'positions', label: 'Positions', icon: Briefcase, badge: posCount || null },
    { id: 'orders',    label: 'Orders',    icon: List,     badge: pendingOrders || null },
    { id: 'balances',  label: 'Balances',  icon: Landmark  },
    { id: 'algo',      label: 'Algo Bot',  icon: Cpu       },
    { id: 'history',   label: 'History',   icon: Activity, badge: tradeHistory.length || null },
    { id: 'equity',    label: 'Equity Curve', icon: TrendingUp },
  ];

  return (
    <>
      <div className="bottom-dock" style={{ gridArea: 'dock', height: dockH }}>
        {/* Drag handle */}
        <div className="dock-resize-handle" onMouseDown={onMouseDown} />

        {/* Tab navigation */}
        <div className="dock-tabs" style={{ paddingTop: 4 }}>
          {TABS.map(tab => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                className={`dock-tab-btn${activeTab === tab.id ? ' active' : ''}`}
                onClick={() => setActiveTab(tab.id)}
              >
                <Icon size={13} />
                {tab.label}
                {tab.badge != null && (
                  <span style={{
                    background: activeTab === tab.id ? 'rgba(37,99,235,0.3)' : 'rgba(255,255,255,0.08)',
                    color: activeTab === tab.id ? '#93c5fd' : 'var(--text-muted)',
                    borderRadius: 'var(--r-full)', padding: '1px 6px',
                    fontSize: 'var(--fs-2xs)', fontWeight: 700, minWidth: 18, textAlign: 'center',
                  }}>
                    {tab.badge}
                  </span>
                )}
                {/* Algo running indicator */}
                {tab.id === 'algo' && isBotRunning && (
                  <span style={{ width: 5, height: 5, borderRadius: '50%', background: '#10b981', boxShadow: '0 0 5px #10b981' }} />
                )}
              </button>
            );
          })}

          {/* Fullscreen toggle for history */}
          {activeTab === 'history' && (
            <button
              className="btn-icon"
              style={{ marginLeft: 'auto', marginRight: 8 }}
              onClick={() => setHistoryFullscreen(f => !f)}
              title={historyFullscreen ? 'Collapse' : 'Expand to fullscreen'}
            >
              {historyFullscreen ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
            </button>
          )}
        </div>

        {/* Tab content */}
        <div className="dock-tab-content">
          {activeTab === 'positions' && <PositionsTab />}
          {activeTab === 'orders'    && <OrdersTab />}
          {activeTab === 'balances'  && <BalancesTab />}
          {activeTab === 'algo'      && <AlgoTab />}
          {activeTab === 'equity'    && <EquityCurveTab />}
          {activeTab === 'history'   && !historyFullscreen && <TradeHistoryContent embedded />}
        </div>
      </div>

      {/* Fullscreen history overlay */}
      {historyFullscreen && activeTab === 'history' && (
        <>
          <div className="history-overlay-backdrop" onClick={() => setHistoryFullscreen(false)} />
          <div className="history-overlay-panel">
            <TradeHistoryContent embedded={false} onClose={() => setHistoryFullscreen(false)} />
          </div>
        </>
      )}
    </>
  );
}
