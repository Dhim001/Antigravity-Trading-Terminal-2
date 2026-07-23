import { Suspense } from 'react';
import { lazyImport } from '../../lib/lazyImport';
import StandaloneDockPanel from './StandaloneDockPanel';

const ScannerTab = lazyImport(() => import('../ScannerTab'), 'scanner');
const AnalystTab = lazyImport(() => import('../AnalystTab'), 'analyst');

function Fallback({ label }) {
  return (
    <div className="flex min-h-[120px] flex-1 items-center justify-center text-xs text-muted-foreground">
      {label}
    </div>
  );
}

/** Scanner or Analyst dock tab — both map to Insights standalone window. */
export default function InsightsFlexPanel({ tab = 'scanner' }) {
  const Content = tab === 'analyst' ? AnalystTab : ScannerTab;
  const label = tab === 'analyst' ? 'Loading Analyst…' : 'Loading Scanner…';

  return (
    <StandaloneDockPanel panelId="insights" dockTabId={tab}>
      <Suspense fallback={<Fallback label={label} />}>
        <Content />
      </Suspense>
    </StandaloneDockPanel>
  );
}
