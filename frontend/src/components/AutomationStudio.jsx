/**
 * AutomationStudio — full-height algo bot workspace (UX-5).
 */
import { useEffect, useState } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Cpu } from 'lucide-react';
import AlgoTabLauncher from './AlgoTabLauncher';

export default function AutomationStudio({ open, onOpenChange }) {
  const [internalOpen, setInternalOpen] = useState(open);

  useEffect(() => {
    setInternalOpen(open);
  }, [open]);

  useEffect(() => {
    const onOpen = () => {
      setInternalOpen(true);
      onOpenChange?.(true);
    };
    window.addEventListener('automation-studio-open', onOpen);
    return () => window.removeEventListener('automation-studio-open', onOpen);
  }, [onOpenChange]);

  const handleChange = (v) => {
    setInternalOpen(v);
    onOpenChange?.(v);
  };

  return (
    <Sheet open={internalOpen} onOpenChange={handleChange}>
      <SheetContent side="right" className="automation-studio w-full sm:max-w-3xl p-0 flex flex-col">
        <SheetHeader className="automation-studio__header px-4 py-3 border-b border-border/50">
          <SheetTitle className="text-sm flex items-center gap-2">
            <Cpu size={16} aria-hidden />
            Automation Studio
          </SheetTitle>
          <SheetDescription className="text-xs">
            Deploy bots, run backtests, and manage execution
          </SheetDescription>
        </SheetHeader>
        <div className="automation-studio__body min-h-0 flex-1 overflow-hidden">
          <AlgoTabLauncher full />
        </div>
      </SheetContent>
    </Sheet>
  );
}
