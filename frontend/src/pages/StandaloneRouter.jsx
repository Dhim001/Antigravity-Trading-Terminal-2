import { lazy, Suspense } from 'react';
import StandaloneShell from './StandaloneShell';
import { readStandalonePanelQuery } from '../lib/standalonePanels';

const LOADERS = {
  'ml-lab': lazy(() => import('./content/MlLabStandaloneContent')),
  algo: lazy(() => import('./content/AlgoStandaloneContent')),
  'backtest-lab': lazy(() => import('./content/BacktestStandaloneContent')),
  copilot: lazy(() => import('./content/CopilotStandaloneContent')),
  insights: lazy(() => import('./content/InsightsStandaloneContent')),
  automation: lazy(() => import('./content/AutomationStandaloneContent')),
  portfolio: lazy(() => import('./content/PortfolioStandaloneContent')),
};

function Fallback({ label }) {
  return (
    <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
      Loading {label}…
    </div>
  );
}

/**
 * Routes ?panel=… to the matching standalone content page.
 */
export default function StandaloneRouter() {
  const panelId = readStandalonePanelQuery() || 'ml-lab';
  const Content = LOADERS[panelId];

  if (!Content) {
    return (
      <StandaloneShell panelId={panelId}>
        <div className="p-6 text-sm text-muted-foreground">No content for panel “{panelId}”.</div>
      </StandaloneShell>
    );
  }

  return (
    <StandaloneShell panelId={panelId}>
      {({ onReattach }) => (
        <Suspense fallback={<Fallback label={panelId} />}>
          <Content onReattach={onReattach} />
        </Suspense>
      )}
    </StandaloneShell>
  );
}
