/**
 * StatsBreakdownTable — win rate / expectancy by strategy, symbol, or timeframe.
 */
import { useMemo, useState } from 'react';
import {
  DataTableRoot, DataTableHeader, DataTableBody, DataTableRow,
  DataTableCell, SortableDataTableHead,
} from '../DataTableShell';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { cn } from '@/lib/utils';
import { fmtPct, fmtUsd, nextSortState, pnlTone, sortBreakdownRows } from '@/lib/analytics/helpers';

const GROUPS = [
  { id: 'strategy', label: 'Strategy' },
  { id: 'symbol', label: 'Symbol' },
  { id: 'timeframe', label: 'Timeframe' },
];

export default function StatsBreakdownTable({
  rows = [],
  groupBy = 'strategy',
  onGroupChange,
  className = '',
}) {
  const [sort, setSort] = useState({ field: 'total_pnl', dir: 'desc' });

  const sorted = useMemo(
    () => sortBreakdownRows(rows, sort),
    [rows, sort],
  );

  const onSort = (field) => setSort((s) => nextSortState(s, field));

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-muted-foreground">Win Rate / Expectancy</span>
        <ToggleGroup
          type="single"
          size="sm"
          value={groupBy}
          onValueChange={(v) => v && onGroupChange?.(v)}
        >
          {GROUPS.map((g) => (
            <ToggleGroupItem key={g.id} value={g.id} className="px-2 text-[0.62rem]">
              {g.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
      <div className="scroll-panel-x max-h-[240px] overflow-auto rounded-md border border-border/40">
        <DataTableRoot variant="dock" className="w-full">
          <DataTableHeader>
            <tr>
              <SortableDataTableHead field="key" sort={sort} onSort={onSort} label="Group" />
              <SortableDataTableHead field="trade_count" sort={sort} onSort={onSort} label="Trades" align="right" />
              <SortableDataTableHead field="win_rate" sort={sort} onSort={onSort} label="Win %" align="right" />
              <SortableDataTableHead field="expectancy" sort={sort} onSort={onSort} label="Expectancy" align="right" />
              <SortableDataTableHead field="profit_factor" sort={sort} onSort={onSort} label="PF" align="right" />
              <SortableDataTableHead field="total_pnl" sort={sort} onSort={onSort} label="Total P&L" align="right" />
            </tr>
          </DataTableHeader>
          <DataTableBody>
            {sorted.length === 0 ? (
              <DataTableRow>
                <DataTableCell colSpan={6} className="py-6 text-center text-xs text-muted-foreground">
                  No closed trades in this period
                </DataTableCell>
              </DataTableRow>
            ) : sorted.map((row) => (
              <DataTableRow key={row.key}>
                <DataTableCell className="font-medium">{row.key}</DataTableCell>
                <DataTableCell align="right" numeric>{row.trade_count}</DataTableCell>
                <DataTableCell align="right" numeric>{fmtPct(row.win_rate)}</DataTableCell>
                <DataTableCell align="right" numeric className={cn(pnlTone(row.expectancy) === 'up' ? 'text-trading-up' : pnlTone(row.expectancy) === 'down' ? 'text-trading-down' : '')}>
                  {fmtUsd(row.expectancy)}
                </DataTableCell>
                <DataTableCell align="right" numeric>{row.profit_factor ?? '—'}</DataTableCell>
                <DataTableCell align="right" numeric className={cn(pnlTone(row.total_pnl) === 'up' ? 'text-trading-up' : pnlTone(row.total_pnl) === 'down' ? 'text-trading-down' : '')}>
                  {fmtUsd(row.total_pnl)}
                </DataTableCell>
              </DataTableRow>
            ))}
          </DataTableBody>
        </DataTableRoot>
      </div>
    </div>
  );
}
