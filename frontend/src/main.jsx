import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ThemeProvider } from 'next-themes'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Toaster } from '@/components/ui/sonner'
import { setupHmrAccept } from './services/hmrState'
import { forceMarketSnapshotSave } from './services/marketSnapshot'
import { useStore } from './store/useStore'
import './index.css'
import App from './App.jsx'

setupHmrAccept()

if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', () => {
    forceMarketSnapshotSave(() => useStore.getState());
  });
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
      <TooltipProvider delayDuration={300}>
        <App />
        <Toaster position="top-right" richColors closeButton />
      </TooltipProvider>
    </ThemeProvider>
  </StrictMode>,
)
