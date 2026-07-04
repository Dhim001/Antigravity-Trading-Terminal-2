/**
 * Capped blocked-entry log — filter, parity, and risk rejects during replay.
 */
import React, { useMemo } from 'react';
import { cn } from '@/lib/utils';
import { useVirtualRows, VirtualTablePadding } from './VirtualTableBody';
import {
  blockedEventKindLabel,
  resolveBlockedEvents,
} from '@/lib/backtestBlockedEvents';

function fmtBlockedTime(sec) {
  if (sec == null || sec === '') return '—';
  const n = Number(sec);
  if (!Number.isFinite(n) || n <= 0) return '—';
  const ms = n > 1e11 ? n : n * 1000;
  try {
    return new Date(ms).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return String(sec);
  }
}

export default function BacktestBlockedLog({ results, className, onFocusBar }) {
  const { events, total, truncated } = useMemo(
    () => resolveBlockedEvents(results),
    [results],
  );

  const { onScroll, window: rowWindow } = useVirtualRows(events, {
    rowHeight: 26,
    overscan: 8,
  });

  if (!events.length && !total) return null;

  return (
    <section className={cn('algo-backtest-blocked-log', className)}>
      <p className="algo-backtest-blocked-log__title">
        Blocked trade log
        <span className="text-muted-foreground font-normal normal-case tracking-normal">
          {` · ${total} reject${total === 1 ? '' : 's'}`}
          {truncated ? ' (sampled)' : ''}
        </span>
      </p>
      <p className="algo-backtest-blocked-log__hint text-muted-foreground">
        Entry signals rejected by analyst filters, live-parity gates, or risk limits — not just counts.
      </p>
      <div
        className={cn('algo-backtest-table-scroll', 'algo-backtest-table-scroll--blocked')}
        onScroll={onScroll}
      >
        <table className="terminal-table algo-backtest-table m-0 text-[0.58rem]">
          <thead>
            <tr>
              <th>Time</th>
              <th>Kind</th>
              <th>Bucket</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            <VirtualTablePadding height={rowWindow.topPad} colSpan={4} />
            {rowWindow.slice.map((ev, i) => (
              <tr
                key={`${ev.time}-${ev.kind}-${rowWindow.start + i}`}
                className={cn(onFocusBar && 'cursor-pointer hover:bg-muted/30')}
                onClick={() => onFocusBar?.(ev.time)}
                title={onFocusBar ? 'Focus chart on this bar' : undefined}
              >
                <td className="text-muted-foreground whitespace-nowrap">{fmtBlockedTime(ev.time)}</td>
                <td className="whitespace-nowrap">{blockedEventKindLabel(ev.kind)}</td>
                <td className="text-muted-foreground whitespace-nowrap">{ev.bucket ?? '—'}</td>
                <td className="max-w-[14rem] truncate" title={ev.reason}>{ev.reason}</td>
              </tr>
            ))}
            <VirtualTablePadding height={rowWindow.bottomPad} colSpan={4} />
          </tbody>
        </table>
      </div>
    </section>
  );
}
