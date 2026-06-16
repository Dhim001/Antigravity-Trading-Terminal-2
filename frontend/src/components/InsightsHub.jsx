/**
 * InsightsHub — Scanner + Analyst combined sheet (UX-5).
 */
import { Suspense, lazy } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Radar, Brain } from 'lucide-react';
import { WidgetEmpty } from './WidgetShell';

const ScannerTab = lazy(() => import('./ScannerTab'));
const AnalystTab = lazy(() => import('./AnalystTab'));

function TabFallback() {
  return <WidgetEmpty message="Loading…" />;
}

export default function InsightsHub({ open, onOpenChange }) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="insights-hub w-full sm:max-w-4xl p-0 flex flex-col">
        <SheetHeader className="insights-hub__header px-4 py-3 border-b border-border/50">
          <SheetTitle className="text-sm">Insights Hub</SheetTitle>
          <SheetDescription className="text-xs">
            Market scanner and chart analyst in one workspace
          </SheetDescription>
        </SheetHeader>
        <Tabs defaultValue="scanner" className="insights-hub__tabs flex min-h-0 flex-1 flex-col">
          <TabsList className="mx-4 mt-2 h-8 w-fit">
            <TabsTrigger value="scanner" className="text-xs h-7 px-3">
              <Radar data-icon="inline-start" />
              Scanner
            </TabsTrigger>
            <TabsTrigger value="analyst" className="text-xs h-7 px-3">
              <Brain data-icon="inline-start" />
              Analyst
            </TabsTrigger>
          </TabsList>
          <TabsContent value="scanner" className="insights-hub__body mt-0 flex-1 overflow-hidden data-[state=inactive]:hidden">
            <Suspense fallback={<TabFallback />}>
              <ScannerTab />
            </Suspense>
          </TabsContent>
          <TabsContent value="analyst" className="insights-hub__body mt-0 flex-1 overflow-hidden data-[state=inactive]:hidden">
            <Suspense fallback={<TabFallback />}>
              <AnalystTab />
            </Suspense>
          </TabsContent>
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}
