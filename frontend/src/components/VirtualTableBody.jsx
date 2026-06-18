import { useWindowedRows } from '../hooks/useWindowedRows';

/** Padding rows for virtualized table bodies. */
export function VirtualTablePadding({ height, colSpan }) {
  if (!height) return null;
  return (
    <tr aria-hidden style={{ height, pointerEvents: 'none' }}>
      <td colSpan={colSpan} style={{ padding: 0, border: 0 }} />
    </tr>
  );
}

/** Hook wrapper for virtualized tables/lists sharing scroll container. */
export function useVirtualRows(rows, options) {
  return useWindowedRows(rows, options);
}

export { useWindowedRows };
