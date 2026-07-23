import { Suspense, lazy, useEffect, useState } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Brain, Newspaper, Radar } from 'lucide-react';
import { WidgetEmpty } from '../../components/WidgetShell';

const ScannerTab = lazy(() => import('../../components/ScannerTab'));
const AnalystTab = lazy(() => import('../../components/AnalystTab'));
const NewsTab = lazy(() => import('../../components/NewsTab'));

const TAB_KEY = 'terminal_insights_hub_tab';

function readTab() {
  try {
    const t = localStorage.getItem(TAB_KEY);
    if (t === 'scanner' || t === 'analyst' || t === 'news') return t;
  } catch {
    /* ignore */
  }
  return 'scanner';
}

function TabFallback() {
  return <WidgetEmpty message="Loading…" />;
}

export default function InsightsStandaloneContent() {
  const [hubTab, setHubTab] = useState(() => readTab());

  useEffect(() => {
    try {
      localStorage.setItem(TAB_KEY, hubTab);
    } catch {
      /* ignore */
    }
  }, [hubTab]);

  return (
    <Tabs
      value={hubTab}
      onValueChange={setHubTab}
      className="flex h-full min-h-0 flex-col overflow-hidden"
    >
      <TabsList className="mx-3 mt-2 w-auto shrink-0 self-start">
        <TabsTrigger value="scanner" className="gap-1">
          <Radar size={14} aria-hidden />
          Scanner
        </TabsTrigger>
        <TabsTrigger value="analyst" className="gap-1">
          <Brain size={14} aria-hidden />
          Analyst
        </TabsTrigger>
        <TabsTrigger value="news" className="gap-1">
          <Newspaper size={14} aria-hidden />
          News
        </TabsTrigger>
      </TabsList>
      <div className="min-h-0 flex-1 overflow-hidden">
        <TabsContent value="scanner" className="mt-0 h-full overflow-auto data-[state=inactive]:hidden">
          <Suspense fallback={<TabFallback />}>
            <ScannerTab />
          </Suspense>
        </TabsContent>
        <TabsContent value="analyst" className="mt-0 h-full overflow-auto data-[state=inactive]:hidden">
          <Suspense fallback={<TabFallback />}>
            <AnalystTab />
          </Suspense>
        </TabsContent>
        <TabsContent value="news" className="mt-0 h-full overflow-auto data-[state=inactive]:hidden">
          <Suspense fallback={<TabFallback />}>
            <NewsTab />
          </Suspense>
        </TabsContent>
      </div>
    </Tabs>
  );
}
