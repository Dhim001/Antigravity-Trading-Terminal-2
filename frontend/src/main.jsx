import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ThemeProvider } from 'next-themes'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Toaster } from '@/components/ui/sonner'
import { setupHmrAccept } from './services/hmrState'
import { forceMarketSnapshotSave } from './services/marketSnapshot'
import { useStore } from './store/useStore'
import { useResearchStore } from './store/useResearchStore'
import { startMemoryGuard } from './services/memoryGuard'
import './index.css'
import App from './App.jsx'
import ErrorBoundary from './components/ErrorBoundary'

setupHmrAccept()

if (typeof window !== 'undefined') {
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

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
      <TooltipProvider delayDuration={300}>
        <ErrorBoundary name="Terminal">
          <App />
        </ErrorBoundary>
        <Toaster position="top-right" richColors closeButton />
      </TooltipProvider>
    </ThemeProvider>
  </StrictMode>,
)
