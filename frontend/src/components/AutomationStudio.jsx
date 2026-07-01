/**
 * AutomationStudio — full-height algo bot workspace (UX-5).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Cpu, GripVertical } from 'lucide-react';
import { useStore } from '../store/useStore';
import { openBacktestLabResults } from '../lib/backtestLab';
import { fetchBots } from '../api/endpoints';
import { getStoreActions } from '../api/dispatch';
import { AlgoTab } from './ResizableDock';
import ErrorBoundary from './ErrorBoundary';
import { Button } from '@/components/ui/button';
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

  const setBotDrawerOpen = useStore((s) => s.setBotDrawerOpen);
  const backtestResults = useStore((s) => s.backtestResults);

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

  useEffect(() => {
    if (!open) return;
    fetchBots(getStoreActions()).catch(() => {});
  }, [open]);

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
          className={cn(
            'terminal-sheet automation-studio w-full sm:max-w-none',
            resizing && 'automation-studio--resizing',
          )}
          data-tour="automation-studio"
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

          <SheetHeader className="terminal-sheet__header automation-studio__header">
            <SheetTitle className="automation-studio__title">
              <Cpu aria-hidden />
              Automation Studio
            </SheetTitle>
            <SheetDescription className="automation-studio__description">
              Deploy bots, run backtests, and manage live execution
            </SheetDescription>
          </SheetHeader>

          <div className="terminal-sheet__body automation-studio__body flex min-h-0 flex-1 flex-col">
            <ErrorBoundary name="Automation studio algo">
              <AlgoTab hideToolbar />
            </ErrorBoundary>
            {backtestResults && (
              <div className="automation-studio__backtest-strip border-t px-3 py-2">
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  className="h-6 text-[0.62rem]"
                onClick={() => openBacktestLabResults()}
              >
                Open Backtest Lab (Results)
                </Button>
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
