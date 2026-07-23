import React, { useEffect, useState, useRef } from 'react';
import { createPortal } from 'react-dom';
import { toast } from 'sonner';

/**
 * Copies all stylesheets from sourceDoc → targetDoc so detached windows
 * inherit the same CSS as the main app.
 */
function copyStyles(sourceDoc, targetDoc) {
  Array.from(sourceDoc.styleSheets).forEach((styleSheet) => {
    try {
      if (styleSheet.cssRules) {
        const newStyleEl = sourceDoc.createElement('style');
        Array.from(styleSheet.cssRules).forEach((cssRule) => {
          newStyleEl.appendChild(sourceDoc.createTextNode(cssRule.cssText));
        });
        targetDoc.head.appendChild(newStyleEl);
      } else if (styleSheet.href) {
        const newLinkEl = sourceDoc.createElement('link');
        newLinkEl.rel = 'stylesheet';
        newLinkEl.href = styleSheet.href;
        targetDoc.head.appendChild(newLinkEl);
      }
    } catch (e) {
      console.warn('DetachedPanelPortal: Could not copy stylesheet', e);
    }
  });
}

/**
 * Sync both `data-theme` attribute AND `.dark` class from the main document
 * to the detached window's <html> element.
 */
function syncTheme(sourceHtml, targetHtml) {
  const theme = sourceHtml.getAttribute('data-theme');
  if (theme) {
    targetHtml.setAttribute('data-theme', theme);
  } else {
    targetHtml.removeAttribute('data-theme');
  }

  if (sourceHtml.classList.contains('dark')) {
    targetHtml.classList.add('dark');
  } else {
    targetHtml.classList.remove('dark');
  }
}

function detachedRegistry() {
  if (typeof window === 'undefined') return null;
  if (!window.__ttDetachedPanels) window.__ttDetachedPanels = {};
  return window.__ttDetachedPanels;
}

export const DEFAULT_DETACH_FEATURES =
  'width=1100,height=800,left=120,top=60,resizable=yes,scrollbars=yes';

function applyWindowChrome(win, title) {
  try {
    win.document.title = title || 'Detached Panel';
    win.document.body.style.margin = '0';
    win.document.body.style.height = '100%';
    win.document.body.style.overflow = 'hidden';
    win.document.documentElement.style.height = '100%';
  } catch {
    /* ignore */
  }
}

/**
 * Open (or focus) a detached panel window synchronously during a user gesture.
 * Browsers block window.open from useEffect after a React state update — call
 * this from the Detach click handler before flipping detachedTabs.
 *
 * @returns {Window | null}
 */
export function prepareDetachedWindow(
  panelId,
  {
    features = DEFAULT_DETACH_FEATURES,
    title = 'Detached Panel',
  } = {},
) {
  if (typeof window === 'undefined') return null;
  const reg = detachedRegistry();
  const existing = panelId ? reg?.[panelId] : null;
  if (existing && !existing.closed) {
    try {
      existing.focus();
    } catch {
      /* ignore */
    }
    return existing;
  }

  const win = window.open('', panelId || '', features);
  if (!win) return null;

  applyWindowChrome(win, title);
  if (reg && panelId) reg[panelId] = win;
  return win;
}

/** Bring an open detached panel window to the front (if still open). */
export function focusDetachedPanel(panelId) {
  if (!panelId || typeof window === 'undefined') return false;
  const win = detachedRegistry()?.[panelId];
  if (!win || win.closed) return false;
  try {
    win.focus();
    return true;
  } catch {
    return false;
  }
}

/**
 * @param {{
 *   children: React.ReactNode,
 *   title?: string,
 *   onClose?: () => void,
 *   panelId?: string,
 *   features?: string,
 * }} props
 */
export default function DetachedPanelPortal({
  children,
  title,
  onClose,
  panelId,
  features = DEFAULT_DETACH_FEATURES,
}) {
  const [container, setContainer] = useState(null);
  const externalWindowRef = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;

    const reg = detachedRegistry();
    let win = panelId ? reg?.[panelId] : null;
    let openedHere = false;

    if (!win || win.closed) {
      win = window.open('', panelId || '', features);
      openedHere = true;
      if (!win) {
        setTimeout(() => {
          onCloseRef.current?.();
          toast.error('Popup blocked', {
            description: 'Please allow popups to open panels in a new window.',
          });
        }, 10);
        return undefined;
      }
      if (reg && panelId) reg[panelId] = win;
    }

    externalWindowRef.current = win;
    applyWindowChrome(win, title);

    // Fresh mount root each time (re-attach / re-detach safe).
    try {
      win.document.body.innerHTML = '';
    } catch {
      /* ignore */
    }

    const el = win.document.createElement('div');
    el.className = 'w-full h-full bg-background text-foreground antialiased overflow-hidden';
    el.style.height = '100%';
    win.document.body.appendChild(el);

    copyStyles(document, win.document);
    syncTheme(document.documentElement, win.document.documentElement);

    setContainer(el);

    const handleBeforeUnload = () => {
      onCloseRef.current?.();
    };
    win.addEventListener('beforeunload', handleBeforeUnload);

    return () => {
      win.removeEventListener('beforeunload', handleBeforeUnload);
      if (panelId) {
        const r = detachedRegistry();
        if (r && r[panelId] === win) delete r[panelId];
      }
      try {
        if (!win.closed) win.close();
      } catch {
        /* ignore */
      }
      externalWindowRef.current = null;
      // openedHere unused except documenting intent — window always owned by portal lifecycle
      void openedHere;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // window is created / claimed once on mount

  useEffect(() => {
    const win = externalWindowRef.current;
    if (!win) return undefined;

    const observer = new MutationObserver(() => {
      syncTheme(document.documentElement, win.document.documentElement);
    });

    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class', 'data-theme'],
    });

    return () => observer.disconnect();
  }, [container]);

  if (!container) return null;
  return createPortal(children, container);
}
