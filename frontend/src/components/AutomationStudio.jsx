/**
 * AutomationStudio — full-height algo bot workspace (UX-5).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Cpu, GripVertical } from 'lucide-react';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { AlgoTab } from './ResizableDock';
import BotDetailDrawer from './BotDetailDrawer';
import ErrorBoundary from './ErrorBoundary';
import { cn } from '@/lib/utils';

const STUDIO_WIDTH_KEY = 'terminal_automation_studio_width';
const STUDIO_WIDTH_DEFAULT = 960;
const STUDIO_WIDTH_MIN = 560;
const STUDIO_WIDTH_MAX = 1480;

function readStudioWidth() {
  try {
    const n = parseInt(localStorage.getItem(STUDIO_WIDTH_KEY), 10);
    if (!Number.isNaN(n)) return Math.min(STUDIO_WIDTH_MAX, Math.max(STUDIO_WIDTH_MIN, n));
  } catch (_) {}
  return STUDIO_WIDTH_DEFAULT;
}

export default function AutomationStudio({ open = false, onOpenChange }) {
  const [panelWidth, setPanelWidth] = useState(() => readStudioWidth());
  const [resizing, setResizing] = useState(false);
  const isDragging = useRef(false);
  const startX = useRef(0);
  const startW = useRef(0);

  const selectedBotId = useStore((s) => s.selectedBotId);
  const botDrawerOpen = useStore((s) => s.botDrawerOpen);
  const setBotDrawerOpen = useStore((s) => s.setBotDrawerOpen);

  useEffect(() => {
    try { localStorage.setItem(STUDIO_WIDTH_KEY, String(panelWidth)); } catch (_) {}
  }, [panelWidth]);

  useEffect(() => {
    const onOpen = () => {
      // Defer so the opening click doesn't hit the new overlay
      requestAnimationFrame(() => onOpenChange?.(true));
    };
    window.addEventListener('automation-studio-open', onOpen);
    return () => window.removeEventListener('automation-studio-open', onOpen);
  }, [onOpenChange]);

  useEffect(() => {
    if (open) setBotDrawerOpen(false);
  }, [open, setBotDrawerOpen]);

  const onResizeMouseDown = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    isDragging.current = true;
    setResizing(true);
    startX.current = e.clientX;
    startW.current = panelWidth;
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
  }, [panelWidth]);

  useEffect(() => {
    const onMove = (e) => {
      if (!isDragging.current) return;
      const delta = startX.current - e.clientX;
      const next = Math.min(STUDIO_WIDTH_MAX, Math.max(STUDIO_WIDTH_MIN, startW.current + delta));
      setPanelWidth(next);
    };
    const onUp = () => {
      if (!isDragging.current) return;
      isDragging.current = false;
      setResizing(false);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, []);

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent
          side="right"
          showCloseButton
          overlayClassName="z-40"
          className={cn(
            'automation-studio pointer-events-auto z-50 w-full sm:max-w-none p-0 flex flex-col gap-0',
            resizing && 'automation-studio--resizing',
          )}
          style={{
            width: panelWidth,
            minWidth: STUDIO_WIDTH_MIN,
            maxWidth: 'min(96vw, 100%)',
          }}
        >
          <div
            className={cn('automation-studio__resize', resizing && 'dragging')}
            onMouseDown={onResizeMouseDown}
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize automation studio panel"
            title="Drag to resize"
          >
            <span className="automation-studio__resize-grip" aria-hidden>
              <GripVertical />
            </span>
          </div>

          <SheetHeader className="automation-studio__header">
            <SheetTitle className="automation-studio__title">
              <Cpu aria-hidden />
              Automation Studio
            </SheetTitle>
            <SheetDescription className="automation-studio__description">
              Deploy bots, run backtests, and manage live execution
            </SheetDescription>
          </SheetHeader>

          <div className="automation-studio__body flex min-h-0 flex-1 flex-col">
            <ErrorBoundary name="Automation studio algo">
              <AlgoTab hideToolbar />
            </ErrorBoundary>
          </div>
        </SheetContent>
      </Sheet>

      <ErrorBoundary name="Bot detail (studio)">
        <BotDetailDrawer
          open={open && botDrawerOpen && !!selectedBotId}
          onOpenChange={setBotDrawerOpen}
          onStop={(bot_id) => sendAction(Action.BOT_STOP, { bot_id })}
          onPause={(bot_id) => sendAction(Action.BOT_PAUSE, { bot_id })}
          onResume={(bot_id) => sendAction(Action.BOT_RESUME, { bot_id })}
        />
      </ErrorBoundary>
    </>
  );
}
