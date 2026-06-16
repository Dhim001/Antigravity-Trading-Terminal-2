/**
 * AutomationStudio — full-height algo bot workspace (UX-5).
 */
import { useEffect, useState } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Cpu } from 'lucide-react';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { AlgoTab } from './ResizableDock';
import BotDetailDrawer from './BotDetailDrawer';
import ErrorBoundary from './ErrorBoundary';

export default function AutomationStudio({ open, onOpenChange }) {
  const [internalOpen, setInternalOpen] = useState(open);
  const selectedBotId = useStore((s) => s.selectedBotId);
  const botDrawerOpen = useStore((s) => s.botDrawerOpen);
  const setBotDrawerOpen = useStore((s) => s.setBotDrawerOpen);

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
    <>
      <Sheet open={internalOpen} onOpenChange={handleChange}>
        <SheetContent side="right" className="automation-studio w-full sm:max-w-[min(96vw,1200px)] p-0 flex flex-col">
          <SheetHeader className="automation-studio__header px-4 py-3 border-b border-border/50 shrink-0">
            <SheetTitle className="text-sm flex items-center gap-2">
              <Cpu size={16} aria-hidden />
              Automation Studio
            </SheetTitle>
            <SheetDescription className="text-xs">
              Deploy bots, run backtests, and manage execution
            </SheetDescription>
          </SheetHeader>
          <div className="automation-studio__body min-h-0 flex-1 overflow-hidden">
            <ErrorBoundary name="Automation studio algo">
              <AlgoTab />
            </ErrorBoundary>
          </div>
        </SheetContent>
      </Sheet>

      <ErrorBoundary name="Bot detail (studio)">
        <BotDetailDrawer
          open={internalOpen && botDrawerOpen && !!selectedBotId}
          onOpenChange={setBotDrawerOpen}
          onStop={(bot_id) => sendAction(Action.BOT_STOP, { bot_id })}
          onPause={(bot_id) => sendAction(Action.BOT_PAUSE, { bot_id })}
          onResume={(bot_id) => sendAction(Action.BOT_RESUME, { bot_id })}
        />
      </ErrorBoundary>
    </>
  );
}
