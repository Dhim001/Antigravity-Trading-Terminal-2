import { useCallback, useMemo, useState } from 'react';

/** Returns a visible slice of rows for scroll-windowed tables. */
export function useWindowedRows(rows, { rowHeight = 40, overscan = 8 } = {}) {
  const [scrollTop, setScrollTop] = useState(0);
  const [viewHeight, setViewHeight] = useState(400);

  const onScroll = useCallback((e) => {
    setScrollTop(e.currentTarget.scrollTop);
    setViewHeight(e.currentTarget.clientHeight);
  }, []);

  const window = useMemo(() => {
    const start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
    const count = Math.ceil(viewHeight / rowHeight) + overscan * 2;
    const end = Math.min(rows.length, start + count);
    return {
      start,
      end,
      topPad: start * rowHeight,
      bottomPad: Math.max(0, (rows.length - end) * rowHeight),
      slice: rows.slice(start, end),
    };
  }, [rows, scrollTop, viewHeight, rowHeight, overscan]);

  return { onScroll, window };
}
