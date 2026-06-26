/**
 * WatchlistWidget.jsx — Phase C: DataTableShell, column presets, asset sections.
 */
import React, { useEffect, useState, useMemo, useCallback } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
import { useMassiveHealth } from '../hooks/useMassiveHealth';
import { massiveWatchlistBadge } from '../lib/massiveMarket';
import { getCandles } from '../services/candleBuffer';
import {
  formatChangeAbs,
  formatChangePct,
  formatPrice,
  formatVolCompact,
  absoluteChangeFromPct,
} from '../lib/formatPrice';
import {
  normalizeWatchlistColumns,
  watchlistColumnsEqual,
  watchlistColumnDefs,
  visibleWatchlistColumns,
  watchlistColumnPrefAttrs,
} from '../settings/watchlistColumns';
import {
  BUILTIN_WATCHLIST_COLUMN_PRESETS,
  resolveWatchlistColumnPresetId,
} from '../settings/watchlistColumnPresets';
import {
  DataTableRoot,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableCell,
  DataTableSectionRow,
  SortableDataTableHead,
} from './DataTableShell';
import { WidgetShell, WidgetToolbar, WidgetEmpty, ScrollTablePanel } from './WidgetShell';
import { Input } from '@/components/ui/input';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { Separator } from '@/components/ui/separator';
import {
  Popover,
  PopoverContent,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from '@/components/ui/popover';
import { cn } from '@/lib/utils';
import { Search, Activity, Columns3 } from 'lucide-react';

const ROLLING_24H_SEC = 86400;

const WATCHLIST_SECTIONS = [
  { key: 'CRYPTO', label: 'Crypto' },
  { key: 'EQUITY', label: 'Equities' },
  { key: 'ETF', label: 'ETFs' },
];

const isCrypto = (sym) => sym.includes('USDT');
const isETF = (sym) => ['SPY', 'QQQ'].includes(sym);
const getCategory = (sym) => (isCrypto(sym) ? 'CRYPTO' : isETF(sym) ? 'ETF' : 'EQUITY');

function avgBarVolume(symbol, volume24h) {
  const candles = getCandles(symbol, '1m');
  if (candles?.length) {
    const cutoff = Math.floor(Date.now() / 1000) - ROLLING_24H_SEC;
    let window = candles.filter((c) => c.time >= cutoff);
    if (!window.length) window = candles.slice(-1440);
    if (!window.length) return null;
    const total = window.reduce((s, c) => s + (c.volume || 0), 0);
    return total / window.length;
  }
  if (volume24h > 0) return volume24h / 1440;
  return null;
}

const OPTIONAL_COLUMN_LABELS = {
  change_abs: 'Change ($)',
  change_24h: 'Change (%)',
  volume_24h: 'Volume (24h)',
  avg_volume: 'Avg 1m volume',
};

const WatchlistColumnPicker = React.memo(function WatchlistColumnPicker({
  columns,
  activePresetId,
  presets,
  onChange,
  onApplyPreset,
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          className="shrink-0 text-muted-foreground"
          title="Choose watchlist columns"
          aria-label="Choose watchlist columns"
        >
          <Columns3 aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-60">
        <PopoverHeader>
          <PopoverTitle className="text-xs">Watchlist columns</PopoverTitle>
        </PopoverHeader>

        <div className="flex flex-col gap-1 pt-1">
          <span className="text-[0.62rem] font-semibold uppercase tracking-wide text-muted-foreground">
            Presets
          </span>
          {presets.map((preset) => (
            <Button
              key={preset.id}
              type="button"
              variant={activePresetId === preset.id ? 'secondary' : 'ghost'}
              size="sm"
              className="h-7 justify-start text-xs font-normal"
              title={preset.description}
              onClick={() => onApplyPreset(preset)}
            >
              {preset.name}
            </Button>
          ))}
          {activePresetId === 'custom' && (
            <span className="px-2 py-0.5 text-[0.62rem] text-muted-foreground">Custom layout</span>
          )}
        </div>

        <Separator className="my-2" />

        <div className="flex flex-col gap-2">
          {Object.entries(OPTIONAL_COLUMN_LABELS).map(([key, label]) => (
            <div key={key} className="flex items-center gap-2">
              <Checkbox
                id={`wl-col-${key}`}
                checked={columns[key] !== false}
                onCheckedChange={(checked) => {
                  const next = { ...columns, [key]: checked === true };
                  if (watchlistColumnsEqual(next, columns)) return;
                  onChange(next);
                }}
              />
              <Label htmlFor={`wl-col-${key}`} className="text-xs font-normal">
                {label}
              </Label>
            </div>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
});

const WatchlistRow = React.memo(function WatchlistRow({
  symbol,
  terminalMode,
  massiveHealth,
  visibleCols,
  onActivate,
  deferred = true,
}) {
  const info = useStore((state) => state.tickerData[symbol]);
  const direction = useStore((state) => state.priceDirections[symbol]);
  const candleRev = useStore((state) => state.candleRevision[symbol] || 0);
  const activeSymbol = useStore((state) => state.activeSymbol);

  const [flashState, setFlashState] = useState(null);

  useEffect(() => {
    if (direction && direction !== 'flat') {
      setFlashState({ dir: direction, key: Date.now() });
    }
  }, [direction]);

  const cat = getCategory(symbol);
  const isActive = symbol === activeSymbol;
  const isUp = info?.change_24h >= 0;
  const shortSym = symbol.replace('USDT', '');
  const flashCls = flashState ? (flashState.dir === 'up' ? 'flash-up' : 'flash-down') : '';
  const rowBadge = massiveWatchlistBadge(symbol, terminalMode, massiveHealth);
  const changeTone = isUp ? 'text-trading-up' : 'text-trading-down';

  const avgVol = useMemo(
    () => avgBarVolume(symbol, info?.volume_24h),
    [symbol, info?.volume_24h, candleRev],
  );

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onActivate(symbol);
    }
  };

  const cells = {
    symbol: (
      <DataTableCell key="symbol" className="watchlist-col-symbol pl-2 pr-1">
        <div className="icon-label-tight">
          <span
            className={cn(
              'size-1.5 shrink-0 rounded-full',
              cat === 'CRYPTO' && 'bg-trading-warn',
              cat === 'EQUITY' && 'bg-primary',
              cat === 'ETF' && 'bg-trading-accent',
              isActive && 'shadow-[0_0_5px_currentColor]',
            )}
          />
          <span className={cn('text-xs tracking-wide', isActive ? 'font-bold text-foreground' : 'font-semibold text-foreground/90')}>
            {shortSym}
          </span>
          {rowBadge === 'closed' && (
            <Badge variant="outline" className="h-4 px-1 text-[0.55rem] font-normal text-muted-foreground">
              Closed
            </Badge>
          )}
          {rowBadge === 'poll' && (
            <Badge variant="outline" className="h-4 px-1 text-[0.55rem] font-normal text-trading-warn">
              Poll
            </Badge>
          )}
        </div>
      </DataTableCell>
    ),
    price: (
      <DataTableCell
        key={flashState?.key ?? 'price'}
        numeric
        align="right"
        className={cn(
          'watchlist-col-price text-xs font-semibold',
          flashCls,
          flashState
            ? flashState.dir === 'up' ? 'text-trading-up' : 'text-trading-down'
            : 'text-foreground',
        )}
      >
        {info ? formatPrice(symbol, info.price) : '…'}
      </DataTableCell>
    ),
    change_abs: (
      <DataTableCell key="change_abs" numeric align="right" className={cn('watchlist-col-chg watchlist-cell font-semibold', changeTone)}>
        {info ? formatChangeAbs(symbol, info.price, info.change_24h) : '—'}
      </DataTableCell>
    ),
    change_24h: (
      <DataTableCell key="change_24h" numeric align="right" className={cn('watchlist-col-chgpct watchlist-cell font-semibold', changeTone)}>
        {info ? formatChangePct(info.change_24h) : '—'}
      </DataTableCell>
    ),
    volume_24h: (
      <DataTableCell key="volume_24h" numeric align="right" className="watchlist-col-vol watchlist-cell text-muted-foreground">
        {info ? formatVolCompact(info.volume_24h) : '—'}
      </DataTableCell>
    ),
    avg_volume: (
      <DataTableCell key="avg_volume" numeric align="right" className="watchlist-col-avgvol watchlist-cell text-muted-foreground">
        {formatVolCompact(avgVol)}
      </DataTableCell>
    ),
  };

  return (
    <DataTableRow
      deferred={deferred}
      rowVariant="watchlist"
      tabIndex={0}
      role="row"
      aria-selected={isActive}
      onClick={() => onActivate(symbol)}
      onKeyDown={handleKeyDown}
      className={cn(
        'cursor-pointer border-l-[3px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
        isActive ? 'border-l-primary' : 'border-l-transparent',
      )}
    >
      {visibleCols.map((col) => cells[col.id])}
    </DataTableRow>
  );
});

function groupSymbolsBySection(symbols) {
  const buckets = { CRYPTO: [], EQUITY: [], ETF: [] };
  for (const sym of symbols) {
    buckets[getCategory(sym)].push(sym);
  }
  return WATCHLIST_SECTIONS
    .filter(({ key }) => buckets[key].length > 0)
    .map(({ key, label }) => ({ key, label, symbols: buckets[key] }));
}

export default function WatchlistWidget() {
  const symbolsList = useStore((state) => state.symbolsList);
  const terminalMode = useStore((state) => state.terminalMode);
  const setActiveSymbol = useStore((state) => state.setActiveSymbol);

  const rawWatchlistColumns = useSettingsStore((s) => s.settings.workspace?.watchlistColumns);
  const watchlistColumns = useSettingsStore(
    useShallow((s) => normalizeWatchlistColumns(s.settings.workspace?.watchlistColumns)),
  );
  const watchlistSections = useSettingsStore((s) => s.settings.workspace?.watchlistSections !== false);
  const activePresetId = useSettingsStore(
    (s) => s.settings.workspace?.watchlistColumnPresetId ?? resolveWatchlistColumnPresetId(
      s.settings.workspace?.watchlistColumns,
      s.settings.watchlistColumnPresets,
    ),
  );
  const customPresets = useSettingsStore((s) => s.settings.watchlistColumnPresets ?? []);
  const updateWorkspace = useSettingsStore((s) => s.updateWorkspace);

  const columnPresets = useMemo(
    () => [...BUILTIN_WATCHLIST_COLUMN_PRESETS, ...customPresets],
    [customPresets],
  );

  const handleApplyPreset = useCallback((preset) => {
    const normalized = normalizeWatchlistColumns(preset.columns);
    updateWorkspace({
      watchlistColumns: normalized,
      watchlistColumnPresetId: preset.id,
    });
  }, [updateWorkspace]);

  const handleWatchlistColumnsChange = useCallback((next) => {
    const normalized = normalizeWatchlistColumns(next);
    if (watchlistColumnsEqual(normalized, rawWatchlistColumns)) return;
    updateWorkspace({
      watchlistColumns: normalized,
      watchlistColumnPresetId: resolveWatchlistColumnPresetId(normalized, customPresets),
    });
  }, [rawWatchlistColumns, updateWorkspace, customPresets]);

  const massiveHealth = useMassiveHealth();
  const [cat, setCat] = useState('ALL');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState({ field: 'symbol', dir: 'asc' });

  const columnDefs = useMemo(() => watchlistColumnDefs(terminalMode), [terminalMode]);
  const visibleCols = useMemo(
    () => visibleWatchlistColumns(watchlistColumns, columnDefs),
    [watchlistColumns, columnDefs],
  );
  const columnPrefAttrs = useMemo(
    () => watchlistColumnPrefAttrs(watchlistColumns),
    [watchlistColumns],
  );

  const tickerData = useStore((state) => (sort.field === 'symbol' ? null : state.tickerData));

  const handleSort = useCallback((field) => {
    setSort((prev) => {
      if (prev.field === field) {
        if (prev.dir === 'asc') return { field, dir: 'desc' };
        return { field: 'symbol', dir: 'asc' };
      }
      return { field, dir: 'asc' };
    });
  }, []);

  const sortValue = useCallback((sym, field) => {
    const info = tickerData?.[sym];
    if (!info) return 0;
    if (field === 'change_abs') {
      return absoluteChangeFromPct(info.price, info.change_24h) ?? 0;
    }
    if (field === 'avg_volume') {
      return avgBarVolume(sym, info.volume_24h) ?? 0;
    }
    return info[field] ?? 0;
  }, [tickerData]);

  const displaySymbols = useMemo(() => {
    let list = symbolsList;
    if (cat !== 'ALL') list = list.filter((s) => getCategory(s) === cat);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter((s) => s.toLowerCase().includes(q));
    }
    return [...list].sort((a, b) => {
      if (sort.field === 'symbol') {
        return sort.dir === 'asc' ? a.localeCompare(b) : b.localeCompare(a);
      }
      const va = sortValue(a, sort.field);
      const vb = sortValue(b, sort.field);
      return sort.dir === 'asc' ? va - vb : vb - va;
    });
  }, [symbolsList, cat, search, sort, sortValue]);

  const showSections = cat === 'ALL' && watchlistSections && !search.trim() && sort.field === 'symbol';
  const sectionGroups = useMemo(
    () => (showSections ? groupSymbolsBySection(displaySymbols) : []),
    [showSections, displaySymbols],
  );

  const counts = useMemo(() => ({
    ALL: symbolsList.length,
    CRYPTO: symbolsList.filter(isCrypto).length,
    EQUITY: symbolsList.filter((s) => !isCrypto(s) && !isETF(s)).length,
    ETF: symbolsList.filter(isETF).length,
  }), [symbolsList]);

  const filterSummary = search.trim()
    ? `${displaySymbols.length} match${displaySymbols.length === 1 ? '' : 'es'}`
    : `${symbolsList.length} symbols`;

  const renderRows = (symbols) => symbols.map((symbol) => (
    <WatchlistRow
      key={symbol}
      symbol={symbol}
      terminalMode={terminalMode}
      massiveHealth={massiveHealth}
      visibleCols={visibleCols}
      onActivate={setActiveSymbol}
    />
  ));

  const toolbar = (
    <div className="watchlist-toolbar-stack">
      <div className="watchlist-filter-row">
        <Search className="watchlist-filter-row__icon shrink-0 text-muted-foreground" aria-hidden />
        <Input
          type="search"
          placeholder="Filter symbols…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="terminal-search-input watchlist-search-input-compact h-[var(--control-h)] flex-1 min-w-0 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
          aria-label="Filter watchlist symbols"
        />
        <span className={cn('watchlist-filter-count num-mono', search.trim() && 'text-foreground')}>
          {filterSummary}
        </span>
      </div>

      <WidgetToolbar compact className="watchlist-cat-toolbar">
        <div className="scroll-fade-x watchlist-cat-scroll flex-1 min-w-0">
          <Tabs value={cat} onValueChange={(v) => { if (v) setCat(v); }} className="w-full">
            <TabsList variant="line" className="scroll-panel-x no-scrollbar h-7 w-full justify-start rounded-none border-0 bg-transparent px-0">
              {[['ALL', 'All'], ['CRYPTO', 'Crypto'], ['EQUITY', 'Equity'], ['ETF', 'ETF']].map(([key, label]) => (
                <TabsTrigger key={key} value={key} className="px-2 text-[0.68rem]">
                  {label}
                  <Badge variant="secondary" className="h-4 min-w-4 px-1 text-[0.58rem] font-semibold">
                    {counts[key]}
                  </Badge>
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>
      </WidgetToolbar>
    </div>
  );

  const headerRight = useMemo(
    () => (
      <WatchlistColumnPicker
        columns={watchlistColumns}
        activePresetId={activePresetId}
        presets={columnPresets}
        onApplyPreset={handleApplyPreset}
        onChange={handleWatchlistColumnsChange}
      />
    ),
    [watchlistColumns, activePresetId, columnPresets, handleApplyPreset, handleWatchlistColumnsChange],
  );

  return (
    <WidgetShell
      icon={Activity}
      title="Watchlist"
      toolbar={toolbar}
      headerRight={headerRight}
      contentClassName="flex min-h-0 flex-col overflow-hidden p-0"
    >
      <ScrollTablePanel className="watchlist-table-panel">
        <DataTableRoot variant="watchlist" className="watchlist-table text-xs" {...columnPrefAttrs}>
          <colgroup>
            {visibleCols.map((col) => (
              <col key={col.id} className={col.col} />
            ))}
          </colgroup>
          <DataTableHeader>
            <tr className="watchlist-table__header-row border-b border-border hover:bg-transparent">
              {visibleCols.map(({ field, label, align, col, title }) => (
                <SortableDataTableHead
                  key={col}
                  field={field}
                  sort={sort}
                  onSort={handleSort}
                  title={title}
                  align={align}
                  className={col}
                  label={label}
                />
              ))}
            </tr>
          </DataTableHeader>
          <DataTableBody>
            {showSections
              ? sectionGroups.map((section) => (
                <React.Fragment key={section.key}>
                  <DataTableSectionRow
                    colSpan={visibleCols.length}
                    label={section.label}
                    count={section.symbols.length}
                  />
                  {renderRows(section.symbols)}
                </React.Fragment>
              ))
              : renderRows(displaySymbols)}
          </DataTableBody>
        </DataTableRoot>
        {displaySymbols.length === 0 && (
          <WidgetEmpty message="No symbols match your filter" />
        )}
      </ScrollTablePanel>
    </WidgetShell>
  );
}
