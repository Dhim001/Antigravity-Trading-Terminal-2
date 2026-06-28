import React, { useEffect, useState, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { toast } from 'sonner';

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

  // Also copy data-theme attribute for dark mode
  const theme = sourceDoc.documentElement.getAttribute('data-theme');
  if (theme) {
    targetDoc.documentElement.setAttribute('data-theme', theme);
  }
}

export default function DetachedPanelPortal({ children, title, onClose }) {
  const [container, setContainer] = useState(null);
  const externalWindow = useMemo(() => {
    if (typeof window !== 'undefined') {
      const win = window.open('', '', 'width=800,height=600,left=200,top=200');
      if (!win) {
        // Popup blocked
        setTimeout(() => {
          if (onClose) onClose();
          toast.error('Popup blocked', {
            description: 'Please allow popups to open panels in a new window.',
          });
        }, 10);
      }
      return win;
    }
    return null;
  }, [onClose]);

  useEffect(() => {
    if (externalWindow) {
      externalWindow.document.title = title || 'Detached Panel';
      const el = externalWindow.document.createElement('div');
      el.className = 'w-full h-full bg-background text-foreground antialiased overflow-hidden';
      externalWindow.document.body.appendChild(el);
      
      // Copy styles
      copyStyles(document, externalWindow.document);

      setContainer(el);

      const handleBeforeUnload = () => {
        if (onClose) onClose();
      };
      externalWindow.addEventListener('beforeunload', handleBeforeUnload);

      return () => {
        externalWindow.removeEventListener('beforeunload', handleBeforeUnload);
        externalWindow.close();
      };
    }
  }, [externalWindow, title, onClose]);

  // Sync theme changes
  useEffect(() => {
    if (!externalWindow) return;
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.attributeName === 'data-theme') {
          const theme = document.documentElement.getAttribute('data-theme');
          if (theme) {
            externalWindow.document.documentElement.setAttribute('data-theme', theme);
          } else {
            externalWindow.document.documentElement.removeAttribute('data-theme');
          }
        }
      });
    });
    observer.observe(document.documentElement, { attributes: true });
    return () => observer.disconnect();
  }, [externalWindow]);

  if (!container) return null;
  return createPortal(children, container);
}
