/**
 * TradeHistoryPanel.jsx
 * Trade blotter — embedded dock tab or expanded via Sheet (ResizableDock).
 */
import React, { useState, useMemo, useCallback, useEffect } from 'react';
import { useStore } from '../store/useStore';
import { sendWebSocketAction } from '../services/websocket';
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
import { ScrollArea } from '@/components/ui/scroll-area';
import { WidgetToolbar, WidgetToolbarDivider, WidgetEmpty } from './WidgetShell';
import { StatCard } from './StatCard';

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
  const { tradeHistory, tradeStats } = useStore();

  const [loading, setLoading] = useState(false);
  const [symFilter, setSymFilter] = useState('ALL');
  const [sideFilter, setSide] = useState('ALL');
  const [statFilter, setStat] = useState('ALL');
  const [dateRange, setDateRange] = useState('All');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState({ field: 'timestamp', dir: 'desc' });

  const fetchHistory = useCallback(() => {
    setLoading(true);
    sendWebSocketAction('get_history');
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
    a.href = url;
    a.download = `trade_history_${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const stats = tradeStats;
  const filteredPnl = filtered.filter(t => t.realized_pnl != null).reduce((s, t) => s + t.realized_pnl, 0);
  const filteredVol = filtered.reduce((s, t) => s + (t.trade_value || 0), 0);

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
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      {!embedded && (
        <div className="flex shrink-0 items-center justify-between border-b border-border bg-muted/30 px-5 py-3">
          <div className="flex items-center gap-2">
            <Activity size={14} className="text-primary" />
            <span className="text-sm font-extrabold">Transaction History</span>
            <Badge variant="secondary">{filtered.length} records</Badge>
          </div>
          <div className="flex items-center gap-2">
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
          <div className="flex items-center gap-1">{toolbarActions}</div>
        </WidgetToolbar>
      )}

      {stats && (
        <div className="flex shrink-0 flex-wrap gap-1.5 border-b border-border bg-muted/20 p-2">
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

      <WidgetToolbar className="scroll-panel-x no-scrollbar flex-nowrap">
        <Filter size={11} className="shrink-0 text-muted-foreground" />
        <ToggleGroup
          type="single"
          size="sm"
          spacing={1}
          value={dateRange}
          onValueChange={v => v && setDateRange(v)}
        >
          {DATE_RANGES.map(r => (
            <ToggleGroupItem key={r.label} value={r.label} className="px-2 text-[0.62rem] font-semibold">
              {r.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>

        <WidgetToolbarDivider />

        <Select value={symFilter} onValueChange={setSymFilter}>
          <SelectTrigger size="sm" className="h-7 w-[120px] text-[0.62rem]">
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

        <ToggleGroup
          type="single"
          size="sm"
          spacing={1}
          value={sideFilter}
          onValueChange={v => v && setSide(v)}
        >
          {['ALL', 'BUY', 'SELL'].map(s => (
            <ToggleGroupItem
              key={s}
              value={s}
              variant={s === 'BUY' ? 'buy' : s === 'SELL' ? 'sell' : 'default'}
              className="px-2 text-[0.62rem] font-semibold"
            >
              {s === 'ALL' ? 'All' : s}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>

        <ToggleGroup
          type="single"
          size="sm"
          spacing={1}
          value={statFilter}
          onValueChange={v => v && setStat(v)}
        >
          {['ALL', 'FILLED', 'PENDING', 'CANCELED'].map(s => (
            <ToggleGroupItem key={s} value={s} className="px-2 text-[0.62rem] font-semibold">
              {s === 'ALL' ? 'All Status' : s.charAt(0) + s.slice(1).toLowerCase()}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>

        <Input
          type="text"
          placeholder="Search…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="ml-auto h-7 w-40 text-[0.62rem]"
        />
      </WidgetToolbar>

      <ScrollArea className="min-h-0 flex-1">
        {loading ? (
          <div className="flex h-full min-h-[120px] items-center justify-center gap-2 text-muted-foreground">
            <RefreshCw size={14} className="animate-spin" />
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
                    <td className="text-muted-foreground">
                      <span className="num-mono text-[0.62rem]">
                        {trade.timestamp
                          ? new Date(trade.timestamp).toLocaleString('en-GB', {
                            day: '2-digit', month: 'short',
                            hour: '2-digit', minute: '2-digit', second: '2-digit',
                          })
                          : '—'}
                      </span>
                    </td>
                    <td><span className="font-bold">{trade.symbol}</span></td>
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
      </ScrollArea>

      <div className="flex shrink-0 items-center justify-between border-t border-border bg-muted/30 px-3 py-1.5 text-[0.62rem] text-muted-foreground">
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
