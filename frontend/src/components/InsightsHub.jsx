/**
 * InsightsHub — Scanner + Analyst combined sheet (UX-5).
 */
import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Radar, Brain, GripVertical } from 'lucide-react';
import { WidgetEmpty } from './WidgetShell';
import { cn } from '@/lib/utils';

const ScannerTab = lazy(() => import('./ScannerTab'));
const AnalystTab = lazy(() => import('./AnalystTab'));

const HUB_WIDTH_KEY = 'terminal_insights_hub_width';
const HUB_TAB_KEY = 'terminal_insights_hub_tab';
const HUB_WIDTH_DEFAULT = 896;
const HUB_WIDTH_MIN = 560;
const HUB_WIDTH_MAX = 1280;

function readHubWidth() {
  try {
    const n = parseInt(localStorage.getItem(HUB_WIDTH_KEY), 10);
    if (!Number.isNaN(n)) return Math.min(HUB_WIDTH_MAX, Math.max(HUB_WIDTH_MIN, n));
  } catch (_) {}
  return HUB_WIDTH_DEFAULT;
}

function readHubTab() {
  try {
    const t = localStorage.getItem(HUB_TAB_KEY);
    if (t === 'scanner' || t === 'analyst') return t;
  } catch (_) {}
  return 'scanner';
}

function TabFallback() {
  return <WidgetEmpty message="Loading…" />;
}

export default function InsightsHub({ open = false, onOpenChange }) {
  const [panelWidth, setPanelWidth] = useState(() => readHubWidth());
  const [hubTab, setHubTab] = useState(() => readHubTab());
  const [resizing, setResizing] = useState(false);
  const contentRef = useRef(null);
  const isDragging = useRef(false);
  const startX = useRef(0);
  const startW = useRef(0);

  useEffect(() => {
    try { localStorage.setItem(HUB_WIDTH_KEY, String(panelWidth)); } catch (_) {}
  }, [panelWidth]);

  useEffect(() => {
    try { localStorage.setItem(HUB_TAB_KEY, hubTab); } catch (_) {}
  }, [hubTab]);

  useEffect(() => {
    const onOpen = () => {
      requestAnimationFrame(() => onOpenChange?.(true));
    };
    window.addEventListener('insights-hub-open', onOpen);
    return () => window.removeEventListener('insights-hub-open', onOpen);
  }, [onOpenChange]);

  useEffect(() => {
    const onTab = (e) => {
      const next = e.detail;
      if (next === 'scanner' || next === 'analyst') setHubTab(next);
    };
    window.addEventListener('insights-hub-tab', onTab);
    return () => window.removeEventListener('insights-hub-tab', onTab);
  }, []);

  useEffect(() => {
    if (open && contentRef.current) {
      const t = setTimeout(() => contentRef.current?.focus(), 120);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [open, hubTab]);

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
      const next = Math.min(HUB_WIDTH_MAX, Math.max(HUB_WIDTH_MIN, startW.current + delta));
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
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        showCloseButton
        className={cn(
          'terminal-sheet insights-hub w-full sm:max-w-none',
          resizing && 'insights-hub--resizing',
        )}
        data-tour="insights-hub"
        aria-label="Insights Hub"
        style={{
          width: panelWidth,
          minWidth: HUB_WIDTH_MIN,
          maxWidth: 'min(96vw, 100%)',
        }}
      >
        <div ref={contentRef} tabIndex={-1} className="flex min-h-0 flex-1 flex-col outline-none">
        <div
          className={cn('insights-hub__resize', resizing && 'dragging')}
          onMouseDown={onResizeMouseDown}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize insights hub panel"
          title="Drag to resize"
        >
          <span className="insights-hub__resize-grip" aria-hidden>
            <GripVertical />
          </span>
        </div>

        <SheetHeader className="terminal-sheet__header insights-hub__header">
          <SheetTitle className="insights-hub__title">
            <Radar aria-hidden />
            Insights Hub
          </SheetTitle>
          <SheetDescription className="insights-hub__description">
            Resizable scanner + analyst workspace (⌘I). Dock intelligence tabs are for quick peek while trading; use this hub for extended review and wider layout.
          </SheetDescription>
        </SheetHeader>

        <Tabs value={hubTab} onValueChange={setHubTab} className="terminal-tabs insights-hub__tabs flex min-h-0 flex-1 flex-col">
          <TabsList className="terminal-tabs__list insights-hub__tablist">
            <TabsTrigger value="scanner" className="insights-hub__tab">
              <Radar data-icon="inline-start" />
              Scanner
            </TabsTrigger>
            <TabsTrigger value="analyst" className="insights-hub__tab">
              <Brain data-icon="inline-start" />
              Analyst
            </TabsTrigger>
          </TabsList>

          <div className="insights-hub__tab-panels">
            <TabsContent
              value="scanner"
              forceMount
              className="terminal-tabs__body insights-hub__body insights-hub__body--cached mt-0 flex-1 overflow-hidden"
            >
              <Suspense fallback={<TabFallback />}>
                <ScannerTab />
              </Suspense>
            </TabsContent>
            <TabsContent
              value="analyst"
              forceMount
              className="terminal-tabs__body insights-hub__body insights-hub__body--cached mt-0 flex-1 overflow-hidden"
            >
              <Suspense fallback={<TabFallback />}>
                <AnalystTab />
              </Suspense>
            </TabsContent>
          </div>
        </Tabs>
        </div>
      </SheetContent>
    </Sheet>
  );
}
