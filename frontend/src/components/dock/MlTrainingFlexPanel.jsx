import { Suspense } from 'react';
import { lazyImport } from '../../lib/lazyImport';
import ErrorBoundary from '../ErrorBoundary';
import StandaloneDockPanel from './StandaloneDockPanel';

const ModelTrainingDashboard = lazyImport(
  () => import('./ModelTrainingDashboard'),
  'ml-training',
);

function PanelFallback({ label = 'Loading…' }) {
  return (
    <div className="flex min-h-[120px] flex-1 items-center justify-center text-xs text-muted-foreground">
      {label}
    </div>
  );
}

/** FlexLayout / dock ML tab → docked Lab or standalone link. */
export default function MlTrainingFlexPanel() {
  return (
    <StandaloneDockPanel
      panelId="ml-lab"
      dockTabId="ml-training"
      showDetachBar={false}
    >
      {({ onDetach }) => (
        <ErrorBoundary name="Model Training">
          <Suspense fallback={<PanelFallback label="Loading ML Training…" />}>
            <ModelTrainingDashboard onDetach={onDetach} />
          </Suspense>
        </ErrorBoundary>
      )}
    </StandaloneDockPanel>
  );
}
