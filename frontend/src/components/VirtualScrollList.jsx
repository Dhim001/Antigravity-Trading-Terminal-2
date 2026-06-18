import { useWindowedRows } from '../hooks/useWindowedRows';

/**
 * Windowed scroll container for long div lists (bot logs, etc.).
 */
export default function VirtualScrollList({
  items,
  rowHeight = 22,
  className = '',
  empty = null,
  renderItem,
  getKey,
}) {
  const { onScroll, window: win } = useWindowedRows(items, { rowHeight, overscan: 12 });

  if (!items.length) return empty;

  return (
    <div className={className} onScroll={onScroll} style={{ overflow: 'auto', height: '100%' }}>
      <div style={{ height: win.topPad }} aria-hidden />
      {win.slice.map((item, i) => {
        const idx = win.start + i;
        const key = getKey ? getKey(item, idx) : idx;
        return (
          <div key={key} style={{ minHeight: rowHeight }}>
            {renderItem(item, idx)}
          </div>
        );
      })}
      <div style={{ height: win.bottomPad }} aria-hidden />
    </div>
  );
}
