/**
 * Backtest Lab — draggable, resizable floating report panel.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Activity, GripVertical, Maximize2, Minimize2, Move } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useStore } from '../store/useStore';
import BacktestResultsPanel from './BacktestResultsPanel';
import BacktestProgressBar from './BacktestProgressBar';
import ErrorBoundary from './ErrorBoundary';

const BOUNDS_KEY = 'terminal_backtest_lab_bounds';

const WIDTH_MIN = 480;
const WIDTH_MAX = 1280;
const HEIGHT_MIN = 360;
const HEIGHT_DEFAULT = 680;
const WIDTH_DEFAULT = 820;

function isBoundsVisible({ x, y, w, h }) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const visibleW = Math.min(x + w, vw) - Math.max(x, 0);
  const visibleH = Math.min(y + h, vh) - Math.max(y, 0);
  return visibleW >= 200 && visibleH >= 200;
}

function readBounds() {
  try {
    const raw = localStorage.getItem(BOUNDS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      const x = Number(parsed?.x);
      const y = Number(parsed?.y);
      const w = Number(parsed?.w);
      const h = Number(parsed?.h);
      if ([x, y, w, h].every((n) => Number.isFinite(n) && n > 0)) {
        const bounds = clampBounds({ x, y, w, h });
        if (isBoundsVisible(bounds)) return bounds;
      }
    }
  } catch (_) {}
  return defaultBounds();
}

function defaultBounds() {
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1280;
  const vh = typeof window !== 'undefined' ? window.innerHeight : 800;
  const w = Math.min(WIDTH_DEFAULT, vw - 32);
  const h = Math.min(HEIGHT_DEFAULT, vh - 64);
  return clampBounds({
    x: Math.max(16, vw - w - 20),
    y: 48,
    w,
    h,
  });
}

function expandedBounds() {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const w = Math.min(WIDTH_MAX, Math.floor(vw * 0.92));
  const h = Math.floor(vh * 0.88);
  return clampBounds({
    x: Math.max(16, Math.floor((vw - w) / 2)),
    y: 32,
    w,
    h,
  });
}

function clampBounds({ x, y, w, h }) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const width = Math.min(WIDTH_MAX, Math.max(WIDTH_MIN, Math.min(w, vw - 16)));
  const height = Math.max(HEIGHT_MIN, Math.min(h, vh - 16));
  const minVisible = 120;
  return {
    x: Math.min(Math.max(x, 8), Math.max(8, vw - minVisible)),
    y: Math.min(Math.max(y, 8), Math.max(8, vh - minVisible)),
    w: width,
    h: height,
  };
}

export default function BacktestLabSheet() {
  const open = useStore((s) => s.backtestLabOpen);
  const setOpen = useStore((s) => s.setBacktestLabOpen);
  const backtestResults = useStore((s) => s.backtestResults);
  const backtestRuns = useStore((s) => s.backtestRuns);
  const backtestRunning = useStore((s) => s.backtestRunning);
  const activeSymbol = useStore((s) => s.activeSymbol);
  const botStrategy = useStore((s) => s.botStrategy);
  const botTimeframe = useStore((s) => s.botTimeframe);
  const backtestSnapshot = useStore((s) => s.backtestSnapshot);

  const [bounds, setBounds] = useState(defaultBounds);
  const [expanded, setExpanded] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [resizing, setResizing] = useState(null);

  const preExpandBounds = useRef(null);
  const dragStart = useRef({ x: 0, y: 0, bounds: bounds });
  const resizeStart = useRef({ x: 0, y: 0, bounds });
  const hydrated = useRef(false);

  const days = backtestResults?.meta?.days;

  useEffect(() => {
    if (!open) return;
    if (!hydrated.current) {
      hydrated.current = true;
      setBounds(readBounds());
    }
  }, [open]);

  useEffect(() => {
    if (!open) {
      hydrated.current = false;
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    try {
      localStorage.setItem(BOUNDS_KEY, JSON.stringify(bounds));
    } catch (_) {}
  }, [bounds, open]);

  useEffect(() => {
    if (!open) return undefined;
    const onResize = () => setBounds((b) => clampBounds(b));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [open]);

  const beginDrag = useCallback((e) => {
    if (e.button !== 0) return;
    if (e.target.closest('button, a, input, select, [role="combobox"]')) return;
    e.preventDefault();
    setDragging(true);
    dragStart.current = { x: e.clientX, y: e.clientY, bounds };
    document.body.style.cursor = 'grabbing';
    document.body.style.userSelect = 'none';
  }, [bounds]);

  const beginResize = useCallback((mode) => (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    setResizing(mode);
    resizeStart.current = { x: e.clientX, y: e.clientY, bounds };
    document.body.style.userSelect = 'none';
    document.body.style.cursor = mode === 'h' ? 'ns-resize' : mode === 'both' ? 'nesw-resize' : 'ew-resize';
  }, [bounds]);

  useEffect(() => {
    const onMove = (e) => {
      if (dragging) {
        const dx = e.clientX - dragStart.current.x;
        const dy = e.clientY - dragStart.current.y;
        const { bounds: start } = dragStart.current;
        setBounds(clampBounds({
          ...start,
          x: start.x + dx,
          y: start.y + dy,
        }));
        return;
      }
      if (!resizing) return;
      const dx = e.clientX - resizeStart.current.x;
      const dy = e.clientY - resizeStart.current.y;
      const { bounds: start } = resizeStart.current;
      let next = { ...start };

      if (resizing === 'w' || resizing === 'both') {
        const newW = start.w - dx;
        const newX = start.x + dx;
        if (newW >= WIDTH_MIN && newW <= WIDTH_MAX) {
          next.w = newW;
          next.x = newX;
        }
      }
      if (resizing === 'h' || resizing === 'both') {
        next.h = Math.max(HEIGHT_MIN, start.h + dy);
      }
      setBounds(clampBounds(next));
    };

    const onUp = () => {
      if (!dragging && !resizing) return;
      setDragging(false);
      setResizing(null);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [dragging, resizing]);

  const toggleExpanded = useCallback(() => {
    if (expanded) {
      setBounds(preExpandBounds.current ?? readBounds());
      setExpanded(false);
      return;
    }
    preExpandBounds.current = bounds;
    setBounds(expandedBounds());
    setExpanded(true);
  }, [bounds, expanded]);

  const isInteracting = dragging || Boolean(resizing);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent
        floating
        showCloseButton
        overlayClassName="backtest-lab-overlay"
        className={cn(
          'terminal-sheet backtest-lab w-full sm:max-w-none',
          isInteracting && 'backtest-lab--interacting',
          expanded && 'backtest-lab--expanded',
        )}
        style={{
          top: bounds.y,
          left: bounds.x,
          width: bounds.w,
          height: bounds.h,
        }}
      >
        <div
          className={cn('backtest-lab__resize backtest-lab__resize--left', resizing === 'w' && 'dragging')}
          onMouseDown={beginResize('w')}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize panel width"
          title="Drag to resize width"
        >
          <span className="backtest-lab__resize-grip" aria-hidden>
            <GripVertical />
          </span>
        </div>

        <div
          className={cn('backtest-lab__resize backtest-lab__resize--bottom', resizing === 'h' && 'dragging')}
          onMouseDown={beginResize('h')}
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize panel height"
          title="Drag to resize height"
        />

        <div
          className={cn('backtest-lab__resize backtest-lab__resize--corner', resizing === 'both' && 'dragging')}
          onMouseDown={beginResize('both')}
          role="separator"
          aria-label="Resize panel"
          title="Drag to resize"
        />

        <SheetHeader
          className={cn(
            'terminal-sheet__header backtest-lab__header',
            dragging && 'backtest-lab__header--dragging',
          )}
          onMouseDown={beginDrag}
        >
          <div className="backtest-lab__header-main">
            <span className="backtest-lab__drag-hint" aria-hidden title="Drag to move">
              <Move size={14} />
            </span>
            <div className="min-w-0">
              <SheetTitle className="backtest-lab__title">
                <Activity aria-hidden />
                Backtest Lab
              </SheetTitle>
              <SheetDescription className="backtest-lab__description">
                Strategy replay report — equity, trades, and run history
              </SheetDescription>
            </div>
          </div>
          <div className="backtest-lab__header-tools">
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="backtest-lab__expand-btn"
              onClick={toggleExpanded}
              title={expanded ? 'Restore panel size' : 'Expand panel'}
            >
              {expanded ? <Minimize2 /> : <Maximize2 />}
              <span className="sr-only">{expanded ? 'Restore size' : 'Expand panel'}</span>
            </Button>
          </div>
        </SheetHeader>

        <div className="terminal-sheet__body backtest-lab__body">
          <div className="terminal-sheet__scroll backtest-lab__scroll">
            <BacktestProgressBar />
            {backtestRunning && !backtestResults && (
              <p className="backtest-lab__loading text-sm text-muted-foreground">
                Running backtest…
              </p>
            )}
            {backtestResults ? (
              <ErrorBoundary name="Backtest report">
                <BacktestResultsPanel
                  variant="full"
                  results={backtestResults}
                  backtestDays={days != null ? String(days) : '7'}
                  backtestTimeframe={backtestResults?.meta?.timeframe ?? botTimeframe}
                  symbol={backtestResults?.meta?.symbol ?? activeSymbol}
                  strategy={backtestResults?.meta?.strategy ?? botStrategy}
                  recentRuns={backtestRuns}
                  snapshot={backtestSnapshot}
                />
              </ErrorBoundary>
            ) : !backtestRunning && (
              <div className="backtest-lab__empty">
                <p className="text-sm text-muted-foreground">
                  Run a backtest from the Algo Bot deploy panel to see results here.
                </p>
              </div>
            )}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
