import { useCallback, useLayoutEffect, useRef } from 'react';

/** Follow newest rows only while the viewport is at the top of the list. */
export const LIVE_PIN_PX = 8;
/** Ignore our own scrollTop writes so they do not flip pin state. */
const USER_SCROLL_GRACE_MS = 160;

export function isPinnedToLive(scrollTop, pinPx = LIVE_PIN_PX) {
  return (Number(scrollTop) || 0) <= pinPx;
}

export function escapeAttrValue(value) {
  const s = String(value);
  if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
    return CSS.escape(s);
  }
  return s.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

export function anchorQuery(id) {
  if (id == null || id === '') return null;
  return `[data-scroll-anchor-id="${escapeAttrValue(id)}"]`;
}

export function readVisibleAnchor(container) {
  if (!container) return { id: null, offset: 0 };
  const top = container.getBoundingClientRect().top;
  const rows = container.querySelectorAll('[data-scroll-anchor-id]');
  for (const row of rows) {
    const rect = row.getBoundingClientRect();
    if (rect.bottom > top + 0.5) {
      return {
        id: row.getAttribute('data-scroll-anchor-id'),
        offset: rect.top - top,
      };
    }
  }
  return { id: null, offset: 0 };
}

export function restoreAnchorScroll(container, anchor) {
  if (!container || !anchor?.id) return;
  const sel = anchorQuery(anchor.id);
  const row = sel ? container.querySelector(sel) : null;
  if (!row) return;
  const currentOffset = row.getBoundingClientRect().top - container.getBoundingClientRect().top;
  const delta = currentOffset - anchor.offset;
  if (Math.abs(delta) >= 0.5) container.scrollTop += delta;
}

/**
 * Newest-first lists: stay at the top while following live logs; when the user
 * is reading older rows, keep that row in place if items are prepended.
 * The scroll handler writes refs only — it must not setState (that re-renders
 * the parent on every wheel tick).
 */
export function usePinnedPrependScroll(items) {
  const ref = useRef(null);
  const pinnedRef = useRef(true);
  const anchorRef = useRef({ id: null, offset: 0 });
  const restoringRef = useRef(false);
  const lastUserScrollRef = useRef(0);

  const onScroll = useCallback((e) => {
    const el = e.currentTarget;
    if (restoringRef.current) return;
    lastUserScrollRef.current = typeof performance !== 'undefined' ? performance.now() : Date.now();
    pinnedRef.current = isPinnedToLive(el.scrollTop);
    if (!pinnedRef.current) {
      anchorRef.current = readVisibleAnchor(el);
    }
  }, []);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
    const userScrolling = now - lastUserScrollRef.current < USER_SCROLL_GRACE_MS;
    restoringRef.current = true;
    try {
      if (pinnedRef.current) {
        // Do not yank back to top while the user is actively scrolling away.
        if (!userScrolling && el.scrollTop !== 0) el.scrollTop = 0;
        return;
      }
      restoreAnchorScroll(el, anchorRef.current);
    } finally {
      restoringRef.current = false;
    }
  }, [items]);

  return { ref, onScroll };
}
