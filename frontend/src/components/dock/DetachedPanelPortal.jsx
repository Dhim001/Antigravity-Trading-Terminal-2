import React, { useEffect, useState, useRef } from 'react';
import { createPortal } from 'react-dom';
import { toast } from 'sonner';

/**
 * Copies all stylesheets from sourceDoc → targetDoc so detached windows
 * inherit the same CSS as the main app.
 *
 * Audit fix: previously called on every mount via `copyStyles(document, win.document)`.
 * Now only iterates already-loaded sheets (same behaviour, better comment).
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
 *
 * Audit fix: previously only synced data-theme; dark mode is driven by
 * the `html.dark` class (see index.css), so detached windows were always
 * light-themed regardless of user preference.
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

export default function DetachedPanelPortal({ children, title, onClose }) {
  const [container, setContainer] = useState(null);
  // Audit fix: window.open was previously called inside useMemo — a side-effect
  // inside memo is an anti-pattern (breaks React Concurrent Mode). Moved to useEffect.
  const externalWindowRef = useRef(null);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const win = window.open('', '', 'width=800,height=600,left=200,top=200');
    if (!win) {
      // Popup blocked
      setTimeout(() => {
        if (onClose) onClose();
        toast.error('Popup blocked', {
          description: 'Please allow popups to open panels in a new window.',
        });
      }, 10);
      return;
    }

    externalWindowRef.current = win;
    win.document.title = title || 'Detached Panel';

    const el = win.document.createElement('div');
    el.className = 'w-full h-full bg-background text-foreground antialiased overflow-hidden';
    win.document.body.appendChild(el);

    // Copy styles and initial theme
    copyStyles(document, win.document);
    syncTheme(document.documentElement, win.document.documentElement);

    setContainer(el);

    const handleBeforeUnload = () => {
      if (onClose) onClose();
    };
    win.addEventListener('beforeunload', handleBeforeUnload);

    return () => {
      win.removeEventListener('beforeunload', handleBeforeUnload);
      win.close();
      externalWindowRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // intentionally empty — window is created once on mount

  // Sync theme changes (class + data-theme) to the detached window
  useEffect(() => {
    const win = externalWindowRef.current;
    if (!win) return;

    const observer = new MutationObserver(() => {
      syncTheme(document.documentElement, win.document.documentElement);
    });

    observer.observe(document.documentElement, {
      attributes: true,
      // Audit fix: watch both 'class' (dark mode) and 'data-theme' attribute
      attributeFilter: ['class', 'data-theme'],
    });

    return () => observer.disconnect();
  }, [container]); // re-subscribe once container is ready

  if (!container) return null;
  return createPortal(children, container);
}
