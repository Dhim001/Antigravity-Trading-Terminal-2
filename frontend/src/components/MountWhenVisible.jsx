/**
 * Unmount FlexLayout tab children when the tab is not visible — with sticky
 * keep-alive so switching back does not flash a blank panel.
 *
 * Heavy panels (Algo, ML Training, etc.) reclaim React trees / ECharts /
 * pollers after ``keepAliveMs``. Zustand holds durable state across remounts.
 */
import { useEffect, useRef, useState } from 'react';

const DEFAULT_KEEP_ALIVE_MS = 60_000;

function PanelPlaceholder({ label = 'Loading…' }) {
  return (
    <div className="flex min-h-[120px] flex-1 items-center justify-center text-xs text-muted-foreground">
      {label}
    </div>
  );
}

export default function MountWhenVisible({
  node,
  children,
  fallback = null,
  keepAliveMs = DEFAULT_KEEP_ALIVE_MS,
  placeholderLabel = 'Loading…',
}) {
  const [visible, setVisible] = useState(() => Boolean(node?.isVisible?.()));
  const [keptWarm, setKeptWarm] = useState(() => Boolean(node?.isVisible?.()));
  const hideTimerRef = useRef(null);

  useEffect(() => {
    if (!node?.setEventListener) return undefined;
    let cancelled = false;

    const clearHideTimer = () => {
      if (hideTimerRef.current != null) {
        clearTimeout(hideTimerRef.current);
        hideTimerRef.current = null;
      }
    };

    const apply = (next) => {
      // Defer so we never setState during FlexLayout Tab render.
      queueMicrotask(() => {
        if (cancelled) return;
        setVisible(next);
        if (next) {
          clearHideTimer();
          setKeptWarm(true);
        } else {
          clearHideTimer();
          const ms = Math.max(0, Number(keepAliveMs) || 0);
          if (ms === 0) {
            setKeptWarm(false);
            return;
          }
          hideTimerRef.current = window.setTimeout(() => {
            hideTimerRef.current = null;
            if (!cancelled) setKeptWarm(false);
          }, ms);
        }
      });
    };

    const onVisibility = (params) => {
      if (params && typeof params.visible === 'boolean') {
        apply(params.visible);
      } else {
        apply(Boolean(node.isVisible()));
      }
    };

    apply(Boolean(node.isVisible()));
    node.setEventListener('visibility', onVisibility);
    return () => {
      cancelled = true;
      clearHideTimer();
      node.removeEventListener('visibility');
    };
  }, [node, keepAliveMs]);

  if (visible || keptWarm) return children;

  if (fallback != null) return fallback;
  return <PanelPlaceholder label={placeholderLabel} />;
}
