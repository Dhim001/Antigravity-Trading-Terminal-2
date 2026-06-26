/**
 * Shared terminal data-table primitive — shadcn Table parts + terminal density/sort/a11y.
 * Use inside a single scroll owner (ScrollTablePanel); omit shadcn Table wrapper to avoid nested scroll.
 */
import React, { useCallback } from 'react';
import {
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import { cn } from '@/lib/utils';

/** Root `<table>` — pass variant for watchlist vs dock styling hooks. */
export function DataTableRoot({ variant = 'terminal', className, children, ...props }) {
  return (
    <table
      data-variant={variant}
      className={cn('data-table', `data-table--${variant}`, className)}
      {...props}
    >
      {children}
    </table>
  );
}

export function DataTableHeader(props) {
  return <TableHeader {...props} />;
}

export function DataTableBody(props) {
  return <TableBody {...props} />;
}

/** Row with optional deferred paint for long lists. */
export function DataTableRow({ deferred = false, rowVariant, className, ...props }) {
  return (
    <TableRow
      className={cn(
        deferred && 'data-table-row-deferred',
        rowVariant === 'watchlist' && 'data-table-row--watchlist border-0 hover:bg-transparent',
        className,
      )}
      {...props}
    />
  );
}

export function DataTableHead({ className, align, ...props }) {
  return (
    <TableHead
      className={cn(
        'data-table__th',
        align === 'right' && 'text-right',
        align === 'center' && 'text-center',
        className,
      )}
      {...props}
    />
  );
}

export function DataTableCell({ className, align, numeric = false, ...props }) {
  return (
    <TableCell
      className={cn(
        'data-table__td',
        numeric && 'num-mono',
        align === 'right' && 'text-right',
        align === 'center' && 'text-center',
        className,
      )}
      {...props}
    />
  );
}

/** Sticky section divider row (watchlist asset groups). */
export function DataTableSectionRow({ colSpan, label, count, className }) {
  return (
    <tr className={cn('data-table-section-row', className)} aria-hidden={false}>
      <th
        scope="rowgroup"
        colSpan={colSpan}
        className="data-table-section-row__label"
      >
        <span>{label}</span>
        {count != null && (
          <span className="data-table-section-row__count num-mono">{count}</span>
        )}
      </th>
    </tr>
  );
}

/**
 * Sortable column header — 3-click cycle: asc → desc → clear (caller handles state).
 * @param {{ field: string, sort: { field: string, dir: string }, onSort: (field: string) => void, label: React.ReactNode, align?: string, className?: string, title?: string }} props
 */
export function SortableDataTableHead({
  field,
  sort,
  onSort,
  label,
  align,
  className,
  title,
}) {
  const active = sort.field === field;
  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSort(field);
    }
  }, [field, onSort]);

  return (
    <th
      scope="col"
      title={title}
      tabIndex={0}
      aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
      onClick={() => onSort(field)}
      onKeyDown={handleKeyDown}
      className={cn(
        'data-table__th h-auto px-1 py-2 text-left align-middle text-[0.72rem] font-semibold uppercase tracking-wide whitespace-nowrap text-muted-foreground',
        align === 'right' && 'text-right',
        'cursor-pointer hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
        active && 'text-trading-accent',
        className,
      )}
    >
      {label}
      {active && (
        <span className="ml-0.5 opacity-70" aria-hidden>
          {sort.dir === 'asc' ? '↑' : '↓'}
        </span>
      )}
    </th>
  );
}

export {
  TableHeader as DataTableHeaderPrimitive,
  TableBody as DataTableBodyPrimitive,
  TableRow as DataTableRowPrimitive,
  TableHead as DataTableHeadPrimitive,
  TableCell as DataTableCellPrimitive,
};
