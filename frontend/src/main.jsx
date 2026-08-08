import { StrictMode, lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { ThemeProvider } from 'next-themes'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Toaster } from '@/components/ui/sonner'
import { setupHmrAccept } from './services/hmrState'
import { forceMarketSnapshotSave } from './services/marketSnapshot'
import { useStore } from './store/useStore'
import { useResearchStore } from './store/useResearchStore'
import { startMemoryGuard } from './services/memoryGuard'
import {
  getStandalonePanelDef,
  isStandaloneLocation,
  readStandalonePanelQuery,
} from './lib/standalonePanels'
import './index.css'
import App from './App.jsx'
import ErrorBoundary from './components/ErrorBoundary'

const standalonePanelId = typeof window !== 'undefined' ? readStandalonePanelQuery() : null
const standalone = Boolean(standalonePanelId) && isStandaloneLocation()
const standaloneTitle = standalonePanelId
  ? (getStandalonePanelDef(standalonePanelId)?.title || 'Standalone')
  : 'Terminal'

/** Lazy so a standalone-page import error cannot blank the main terminal. */
const StandaloneRouter = lazy(() => import('./pages/StandaloneRouter.jsx'))

setupHmrAccept()

if (typeof window !== 'undefined' && !standalone) {
  window.addEventListener('beforeunload', () => {
    forceMarketSnapshotSave(() => useStore.getState());
  });

  if ('serviceWorker' in navigator && !window.terminalDesktop?.isDesktop) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  } else if ('serviceWorker' in navigator && window.terminalDesktop?.isDesktop) {
    // SW + Vite dev server conflict in Electron — clear any prior registration.
    navigator.serviceWorker.getRegistrations()
      .then((regs) => Promise.all(regs.map((r) => r.unregister())))
      .catch(() => {});
  }

  startMemoryGuard(() => useStore, () => useResearchStore);
}

// Dev/E2E: inspect live ticker without digging through React fiber.
if (typeof window !== 'undefined' && import.meta.env.DEV) {
  window.__ttGetState = () => useStore.getState();
}

const rootEl = document.getElementById('root')
// HMR / Fast Refresh re-executes this module — reuse the root (avoids
// "createRoot() on a container that has already been passed to createRoot()").
const root = (import.meta.hot?.data?.root)
  ?? createRoot(rootEl)
if (import.meta.hot) {
  import.meta.hot.data.root = root
}

root.render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
      <TooltipProvider delayDuration={300}>
        <ErrorBoundary name={standalone ? standaloneTitle : 'Terminal'}>
          {standalone ? (
            <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Loading panel…</div>}>
              <StandaloneRouter />
            </Suspense>
          ) : (
            <App />
          )}
        </ErrorBoundary>
        <Toaster position="top-right" richColors closeButton />
      </TooltipProvider>
    </ThemeProvider>
  </StrictMode>,
)
