import React from 'react';
import { Download, X } from 'lucide-react';
import { usePwaInstall } from '../hooks/usePwaInstall';
import { Button } from '@/components/ui/button';

export default function PwaInstallBanner() {
  const { canInstall, install, dismiss } = usePwaInstall();

  if (!canInstall) return null;

  return (
    <div
      className="fixed bottom-3 left-1/2 z-[60] flex w-[min(420px,calc(100vw-1.5rem))] -translate-x-1/2 items-center gap-2 rounded-lg border border-border/70 bg-background/95 px-3 py-2 shadow-lg backdrop-blur-sm"
      role="region"
      aria-label="Install app"
    >
      <Download size={16} className="shrink-0 text-primary" aria-hidden />
      <p className="flex-1 text-xs text-foreground">
        Install Trading Terminal for quick access and background push alerts.
      </p>
      <Button size="xs" onClick={() => install()}>
        Install
      </Button>
      <Button size="icon-xs" variant="ghost" onClick={dismiss} aria-label="Dismiss install prompt">
        <X size={14} aria-hidden />
      </Button>
    </div>
  );
}
