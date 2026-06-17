/**
 * TradeHistoryPanel.jsx
 * Trade blotter — embedded dock tab or expanded via Sheet (ResizableDock).
 */
import React, { useState, useMemo, useCallback, useEffect } from 'react';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { cn } from '@/lib/utils';
import {
  X, Download, RefreshCw, ChevronUp, ChevronDown, ChevronsUpDown,
  TrendingUp, TrendingDown, BarChart2, Award, Target, Activity,
  CheckCircle2, XCircle, Clock, Filter,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { WidgetToolbar, WidgetEmpty } from './WidgetShell';
import { StatCard } from './StatCard';
import { buildBotLookup, parseTradeTimestamp, tradeSourceLabel } from '@/lib/botAttribution';

const fmt = (n, dec = 2) =>
  n == null ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });

function FmtPnl({ value }) {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  const pos = value >= 0;
  return (
    <span className={cn('num-mono font-bold', pos ? 'text-trading-up' : 'text-trading-down')}>
      {pos ? '+' : ''}${fmt(value)}
    </span>
  );
}

const STATUS_META = {
  FILLED:   { variant: 'buy', icon: CheckCircle2 },
  CANCELED: { variant: 'secondary', icon: XCircle },
  PENDING:  { variant: 'outline', icon: Clock, className: 'border-trading-warn/40 text-trading-warn' },
  REJECTED: { variant: 'destructive', icon: XCircle },
};

const DATE_RANGES = [
  { label: 'Today', days: 1 },
  { label: '7D', days: 7 },
  { label: '30D', days: 30 },
  { label: 'All', days: Infinity },
];

function SortTh({ children, field, sort, onSort, className }) {
  const active = sort.field === field;
  const Icon = active ? (sort.dir === 'asc' ? ChevronUp : ChevronDown) : ChevronsUpDown;
  return (
    <th
      onClick={() => onSort(field)}
      className={cn('cursor-pointer select-none', active && 'text-primary', className)}
    >
      <div className="flex items-center gap-0.5">
        {children}
        <Icon size={10} className={cn('shrink-0', active ? 'opacity-100' : 'opacity-35')} />
      </div>
    </th>
  );
}

export function TradeHistoryContent({ embedded = true, onClose }) {
  const tradeHistory = useStore((state) => state.tradeHistory);
  const tradeStats = useStore((state) => state.tradeStats);
  const activeBots = useStore((state) => state.activeBots);
  const botLookup = useMemo(() => buildBotLookup(activeBots), [activeBots]);

  const [loading, setLoading] = useState(false);
  const [symFilter, setSymFilter] = useState('ALL');
  const [sideFilter, setSide] = useState('ALL');
  const [statFilter, setStat] = useState('ALL');
  const [sourceFilter, setSourceFilter] = useState('ALL');
  const [dateRange, setDateRange] = useState('All');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState({ field: 'timestamp', dir: 'desc' });

  const fetchHistory = useCallback(() => {
    setLoading(true);
    sendAction(Action.GET_HISTORY);
  }, []);

  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  useEffect(() => {
    if (tradeHistory.length > 0 || tradeStats) setLoading(false);
  }, [tradeHistory, tradeStats]);

  const handleSort = (field) => {
    setSort(prev => ({
      field,
      dir: prev.field === field ? (prev.dir === 'asc' ? 'desc' : 'asc') : 'desc',
    }));
  };

  const symbols = useMemo(
    () => ['ALL', ...Array.from(new Set(tradeHistory.map(t => t.symbol)))],
    [tradeHistory],
  );

  const selectedRange = DATE_RANGES.find(r => r.label === dateRange);
  const cutoff = selectedRange?.days === Infinity ? 0 : Date.now() - selectedRange.days * 86400000;

  const filtered = useMemo(() => {
    let rows = tradeHistory;
    if (cutoff > 0) rows = rows.filter(t => t.timestamp >= cutoff);
    if (symFilter !== 'ALL') rows = rows.filter(t => t.symbol === symFilter);
    if (sideFilter !== 'ALL') rows = rows.filter(t => t.side === sideFilter);
    if (statFilter !== 'ALL') rows = rows.filter(t => t.status === statFilter);
    if (sourceFilter === 'BOT') rows = rows.filter(t => t.bot_id);
    if (sourceFilter === 'MANUAL') rows = rows.filter(t => !t.bot_id);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      rows = rows.filter(t =>
        t.symbol.toLowerCase().includes(q) ||
        t.id.toLowerCase().includes(q) ||
        t.type.toLowerCase().includes(q),
      );
    }
    return [...rows].sort((a, b) => {
      let av = a[sort.field], bv = b[sort.field];
      if (av == null) av = sort.dir === 'asc' ? Infinity : -Infinity;
      if (bv == null) bv = sort.dir === 'asc' ? Infinity : -Infinity;
      if (typeof av === 'string') av = av.toLowerCase(), bv = String(bv).toLowerCase();
      return sort.dir === 'asc' ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
    });
  }, [tradeHistory, cutoff, symFilter, sideFilter, statFilter, sourceFilter, search, sort]);

  const exportCSV = () => {
    const headers = ['Time', 'ID', 'Symbol', 'Source', 'Type', 'Side', 'Status', 'Qty', 'Fill Price', 'Value', 'Cost Basis', 'Realized P&L'];
    const rows = filtered.map(t => {
      const src = tradeSourceLabel(t, botLookup);
      return [
      new Date(t.timestamp).toISOString(),
      t.id, t.symbol, src.label, t.type, t.side, t.status,
      t.filled_quantity ?? t.quantity,
      t.average_fill_price ?? t.price ?? '',
      t.trade_value ?? '', t.cost_basis ?? '', t.realized_pnl ?? '',
    ];
    });
    const csv = [headers, ...rows].map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `trade_history_${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const stats = tradeStats;
  const filteredPnl = filtered.filter(t => t.realized_pnl != null).reduce((s, t) => s + t.realized_pnl, 0);
  const filteredVol = filtered.reduce((s, t) => s + (t.trade_value || 0), 0);

  const activeFilterCount = [
    dateRange !== 'All',
    symFilter !== 'ALL',
    sideFilter !== 'ALL',
    statFilter !== 'ALL',
    sourceFilter !== 'ALL',
    Boolean(search.trim()),
  ].filter(Boolean).length;

  const filterSummary = activeFilterCount === 0
    ? 'All trades'
    : `${activeFilterCount} filter${activeFilterCount === 1 ? '' : 's'} active`;

  const toolbarActions = (
    <>
      <Button variant="ghost" size="xs" onClick={() => { setLoading(true); fetchHistory(); }}>
        <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
        Refresh
      </Button>
      <Button variant="outline" size="xs" onClick={exportCSV}>
        <Download size={11} />
        {embedded ? 'CSV' : 'Export CSV'}
      </Button>
    </>
  );

  return (
    <div className={cn('history-panel', !embedded && 'history-panel--expanded')}>
      {!embedded && (
        <div className="history-sheet-header">
          <div className="icon-label-loose">
            <Activity size={14} className="text-primary" aria-hidden />
            <span className="text-sm font-extrabold">Transaction History</span>
            <Badge variant="secondary">{filtered.length} records</Badge>
          </div>
          <div className="flex items-center gap-[var(--icon-gap-loose)]">
            {toolbarActions}
            {onClose && (
              <Button variant="ghost" size="icon-sm" onClick={onClose}>
                <X />
              </Button>
            )}
          </div>
        </div>
      )}

      {embedded && (
        <WidgetToolbar className="justify-between">
          <Badge variant="secondary" className="font-normal">{filtered.length} records</Badge>
          <div className="flex items-center gap-[var(--icon-gap)]">{toolbarActions}</div>
        </WidgetToolbar>
      )}

      {stats && (
        <div className="history-stats-row">
          <StatCard
            label="Realized P&L"
            icon={stats.total_pnl >= 0 ? TrendingUp : TrendingDown}
            value={`${stats.total_pnl >= 0 ? '+' : ''}$${fmt(stats.total_pnl)}`}
            tone={stats.total_pnl > 0 ? 'up' : stats.total_pnl < 0 ? 'down' : 'neutral'}
            sub={`${stats.wins}W / ${stats.losses}L`}
          />
          <StatCard
            label="Win Rate"
            icon={Target}
            value={`${fmt(stats.win_rate, 1)}%`}
            tone={stats.win_rate >= 50 ? 'up' : stats.win_rate < 40 ? 'down' : 'neutral'}
            sub={`${stats.total_sells} closed`}
          />
          <StatCard
            label="Profit Factor"
            icon={BarChart2}
            value={stats.profit_factor != null ? fmt(stats.profit_factor) : '—'}
            tone={stats.profit_factor > 1.5 ? 'up' : stats.profit_factor < 1 ? 'down' : 'neutral'}
            sub="Win÷Loss PnL"
          />
          <StatCard
            label="Best Trade"
            icon={Award}
            value={`+$${fmt(stats.best_trade)}`}
            tone="up"
            sub={`Avg win: +$${fmt(stats.avg_win)}`}
          />
          <StatCard
            label="Worst Trade"
            icon={TrendingDown}
            value={`-$${fmt(Math.abs(stats.worst_trade))}`}
            tone="down"
            sub={`Avg loss: -$${fmt(Math.abs(stats.avg_loss))}`}
          />
          <StatCard
            label="Total Fills"
            icon={Activity}
            value={stats.total_fills}
            tone="accent"
            sub={`Vol: $${fmt(stats.gross_volume)}`}
          />
        </div>
      )}

      <div className="history-filter-bar">
        <div className="history-filter-bar__header">
          <div className="history-filter-bar__lead">
            <Filter size={12} className="history-filter-bar__icon" aria-hidden />
            <div className="history-filter-bar__titles">
              <span className="history-filter-bar__title">Trade Filters</span>
              <span
                className={cn(
                  'history-filter-bar__summary num-mono',
                  activeFilterCount > 0 && 'history-filter-bar__summary--active',
                )}
              >
                {filterSummary}
              </span>
            </div>
          </div>

          <div className="history-filter-bar__search">
            <Input
              type="text"
              placeholder="Search symbol, ID, type…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="terminal-search-input"
            />
          </div>
        </div>

        <div className="history-filter-bar__groups scroll-panel-x no-scrollbar">
          <div className="history-filter-group">
            <span className="history-filter-group__label">Period</span>
            <ToggleGroup
              type="single"
              size="sm"
              spacing={1}
              value={dateRange}
              onValueChange={v => v && setDateRange(v)}
              className="history-filter-group__controls"
            >
              {DATE_RANGES.map(r => (
                <ToggleGroupItem key={r.label} value={r.label} className="history-filter-chip">
                  {r.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>

          <div className="history-filter-group">
            <span className="history-filter-group__label">Symbol</span>
            <Select value={symFilter} onValueChange={setSymFilter}>
              <SelectTrigger size="sm" className="history-filter-select">
                <SelectValue placeholder="Symbol" />
              </SelectTrigger>
              <SelectContent>
                {symbols.map(s => (
                  <SelectItem key={s} value={s} className="text-xs">
                    {s === 'ALL' ? 'All Symbols' : s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="history-filter-group">
            <span className="history-filter-group__label">Side</span>
            <ToggleGroup
              type="single"
              size="sm"
              spacing={1}
              value={sideFilter}
              onValueChange={v => v && setSide(v)}
              className="history-filter-group__controls"
            >
              {['ALL', 'BUY', 'SELL'].map(s => (
                <ToggleGroupItem
                  key={s}
                  value={s}
                  variant={s === 'BUY' ? 'buy' : s === 'SELL' ? 'sell' : 'default'}
                  className="history-filter-chip"
                >
                  {s === 'ALL' ? 'All' : s}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>

          <div className="history-filter-group">
            <span className="history-filter-group__label">Status</span>
            <ToggleGroup
              type="single"
              size="sm"
              spacing={1}
              value={statFilter}
              onValueChange={v => v && setStat(v)}
              className="history-filter-group__controls"
            >
              {['ALL', 'FILLED', 'PENDING', 'CANCELED'].map(s => (
                <ToggleGroupItem key={s} value={s} className="history-filter-chip">
                  {s === 'ALL' ? 'All' : s.charAt(0) + s.slice(1).toLowerCase()}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>

          <div className="history-filter-group">
            <span className="history-filter-group__label">Source</span>
            <ToggleGroup
              type="single"
              size="sm"
              spacing={1}
              value={sourceFilter}
              onValueChange={v => v && setSourceFilter(v)}
              className="history-filter-group__controls"
            >
              {['ALL', 'BOT', 'MANUAL'].map(s => (
                <ToggleGroupItem key={s} value={s} className="history-filter-chip">
                  {s === 'ALL' ? 'All' : s === 'BOT' ? 'Bot' : 'Manual'}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>
        </div>
      </div>

      <div className="history-table-scroll scroll-panel-y scroll-panel-y-0">
        {loading ? (
          <div className="flex h-full min-h-[120px] items-center justify-center gap-[var(--icon-gap-loose)] text-muted-foreground">
            <RefreshCw size={14} className="animate-spin" aria-hidden />
            Loading…
          </div>
        ) : filtered.length === 0 ? (
          <WidgetEmpty icon={Activity} message="No transactions match your filters" />
        ) : (
          <table className="terminal-table text-xs">
            <thead>
              <tr>
                <SortTh field="timestamp" sort={sort} onSort={handleSort}>Time</SortTh>
                <SortTh field="symbol" sort={sort} onSort={handleSort}>Symbol</SortTh>
                <th>Source</th>
                <SortTh field="side" sort={sort} onSort={handleSort}>Side</SortTh>
                <SortTh field="type" sort={sort} onSort={handleSort}>Type</SortTh>
                <SortTh field="status" sort={sort} onSort={handleSort}>Status</SortTh>
                <SortTh field="filled_quantity" sort={sort} onSort={handleSort} className="text-right">Qty</SortTh>
                <SortTh field="average_fill_price" sort={sort} onSort={handleSort} className="text-right">Fill Price</SortTh>
                <SortTh field="trade_value" sort={sort} onSort={handleSort} className="text-right">Value</SortTh>
                <SortTh field="realized_pnl" sort={sort} onSort={handleSort} className="text-right">Realized P&L</SortTh>
              </tr>
            </thead>
            <tbody>
              {filtered.map(trade => {
                const meta = STATUS_META[trade.status] || STATUS_META.PENDING;
                const StatusIcon = meta.icon;
                const src = tradeSourceLabel(trade, botLookup);
                const qty = trade.filled_quantity ?? trade.quantity;
                const fp = trade.average_fill_price || trade.price;
                const pdec = (
                  trade.symbol?.includes('XRP') ||
                  trade.symbol?.includes('ADA') ||
                  trade.symbol?.includes('DOGE') ||
                  (fp && fp < 2.0)
                ) ? 4 : 2;

                return (
                  <tr key={trade.id}>
                    <td>
                      <span className="num-mono text-[0.62rem] text-muted-foreground">
                        {(() => {
                          const d = parseTradeTimestamp(trade.timestamp);
                          return d ? d.toLocaleString('en-GB', {
                            day: '2-digit', month: 'short',
                            hour: '2-digit', minute: '2-digit', second: '2-digit',
                          }) : '—';
                        })()}
                      </span>
                    </td>
                    <td><span className="font-bold">{trade.symbol}</span></td>
                    <td>
                      <Badge variant={src.kind === 'bot' ? 'secondary' : 'outline'} className="text-[0.58rem]">
                        {src.label}
                      </Badge>
                    </td>
                    <td>
                      <Badge variant={trade.side === 'BUY' ? 'buy' : 'sell'}>{trade.side}</Badge>
                    </td>
                    <td className="text-secondary-foreground">{trade.type}</td>
                    <td>
                      <Badge variant={meta.variant} className={meta.className}>
                        <StatusIcon data-icon="inline-start" />
                        {trade.status}
                      </Badge>
                    </td>
                    <td className="num-mono text-right">
                      {qty != null ? qty.toFixed(qty < 1 ? 6 : 4) : '—'}
                    </td>
                    <td className="num-mono text-right font-semibold">
                      {fp ? `$${fmt(fp, pdec)}` : '—'}
                    </td>
                    <td className="num-mono text-right text-secondary-foreground">
                      {trade.trade_value ? `$${fmt(trade.trade_value)}` : '—'}
                    </td>
                    <td className="text-right">
                      <FmtPnl value={trade.realized_pnl} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="history-footer-bar">
        <span>Showing {filtered.length} of {tradeHistory.length}</span>
        {filtered.length > 0 && (
          <span className="flex gap-3">
            <span>
              Filtered P&L:{' '}
              <FmtPnl value={filteredPnl} />
            </span>
            <span>
              Vol:{' '}
              <span className="num-mono text-secondary-foreground">${fmt(filteredVol)}</span>
            </span>
          </span>
        )}
        <span className="opacity-50">FIFO · local time</span>
      </div>
    </div>
  );
}

export default TradeHistoryContent;
