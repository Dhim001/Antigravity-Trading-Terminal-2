/**
 * Unmount FlexLayout tab children when the tab is not visible — with sticky
 * keep-alive so switching back does not flash a blank panel.
 *
 * Heavy panels (Algo, ML Training, etc.) reclaim React trees / ECharts /
 * pollers after ``keepAliveMs``. Zustand holds durable state across remounts.
 *
 * FlexLayout calls ``tabNode.setVisible(true)`` during the parent Tab render
 * *before* this component paints. Visibility listeners defer setState via
 * microtask (to avoid updating during FlexLayout render), so we must also
 * read ``node.isVisible()`` live on each render — otherwise a selected tab
 * can briefly (or until interaction) show ``Loading {component}…`` after
 * keep-alive expired.
 */
import { useEffect, useRef, useState } from 'react';

const DEFAULT_KEEP_ALIVE_MS = 60_000;

/** Pure gate used by render — exported for unit tests. */
export function shouldRenderPanelChildren({ nodeVisible, visible, keptWarm }) {
  return Boolean(nodeVisible || visible || keptWarm);
}

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
  const nodeRef = useRef(node);
  nodeRef.current = node;

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
            // Re-check: tab may have been re-selected before this microtask ran.
            if (nodeRef.current?.isVisible?.()) {
              setVisible(true);
              setKeptWarm(true);
              return;
            }
            setKeptWarm(false);
            return;
          }
          hideTimerRef.current = window.setTimeout(() => {
            hideTimerRef.current = null;
            if (cancelled) return;
            // Do not unmount if the tab is selected again (event may be deferred).
            if (nodeRef.current?.isVisible?.()) {
              setVisible(true);
              setKeptWarm(true);
              return;
            }
            setKeptWarm(false);
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

  // Live FlexLayout flag beats deferred React state (avoids Loading flash / stuck placeholder).
  const nodeVisible = Boolean(node?.isVisible?.());
  if (shouldRenderPanelChildren({ nodeVisible, visible, keptWarm })) return children;

  if (fallback != null) return fallback;
  return <PanelPlaceholder label={placeholderLabel} />;
}
