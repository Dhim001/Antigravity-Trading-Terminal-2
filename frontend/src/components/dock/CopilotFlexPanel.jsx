import { Suspense } from 'react';
import { lazyImport } from '../../lib/lazyImport';
import StandaloneDockPanel from './StandaloneDockPanel';

const CopilotTab = lazyImport(() => import('./CopilotTab'), 'copilot');

function Fallback() {
  return (
    <div className="flex min-h-[120px] flex-1 items-center justify-center text-xs text-muted-foreground">
      Loading Copilot…
    </div>
  );
}

export default function CopilotFlexPanel() {
  return (
    <StandaloneDockPanel panelId="copilot" dockTabId="copilot">
      <Suspense fallback={<Fallback />}>
        <CopilotTab />
      </Suspense>
    </StandaloneDockPanel>
  );
}
