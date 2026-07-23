import { Suspense } from 'react';
import { lazyImport } from '../../lib/lazyImport';
import StandaloneDockPanel from './StandaloneDockPanel';

const AlgoTab = lazyImport(
  () => import('./AlgoPanel').then((m) => ({ default: m.AlgoTab })),
  'algo',
);

function Fallback() {
  return (
    <div className="flex min-h-[120px] flex-1 items-center justify-center text-xs text-muted-foreground">
      Loading Algo…
    </div>
  );
}

export default function AlgoFlexPanel() {
  return (
    <StandaloneDockPanel panelId="algo" dockTabId="algo">
      <Suspense fallback={<Fallback />}>
        <AlgoTab />
      </Suspense>
    </StandaloneDockPanel>
  );
}
