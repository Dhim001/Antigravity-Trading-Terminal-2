import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useSettingsStore } from '../store/useSettingsStore';

const STEPS = [
  {
    title: 'Welcome to Antigravity',
    body: 'A sim/live trading terminal with chart analyst, algo bots, and a full order ticket. This quick tour shows where the main workflows live.',
  },
  {
    title: 'Pick a symbol',
    body: 'Use the watchlist on the left or press ⌘K to search symbols. The market strip and chart update with your selection.',
    action: 'watchlist',
  },
  {
    title: 'Read the chart',
    body: 'The Chart Analyst badge on the toolbar shows the server signal (BUY/SELL/NEUTRAL) with expandable trend, momentum, and risk sub-reports.',
    action: 'chart',
  },
  {
    title: 'Place an order',
    body: 'The order ticket on the right includes a pre-trade preview — qty, notional, SL/TP, and blocked reasons before you submit.',
    action: 'order',
  },
  {
    title: 'Bottom dock',
    body: 'Positions, Scanner (rank watchlist), Analyst history, and Algo bots live in the resizable bottom dock. Try ⌘B for bots and ⌘I for analyst.',
    action: 'dock',
  },
  {
    title: 'You are set',
    body: 'Press ? anytime for keyboard shortcuts. Open Preferences (⌘,) for themes, workspaces, and alerts.',
  },
];

function runStepAction(action) {
  if (!action || typeof window === 'undefined') return;
  if (action === 'dock') {
    window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'positions' }));
  }
}

export default function OnboardingTour() {
  const completed = useSettingsStore((s) => s.settings.onboardingCompleted);
  const setOnboardingCompleted = useSettingsStore((s) => s.setOnboardingCompleted);
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (!completed) {
      const t = setTimeout(() => setOpen(true), 800);
      return () => clearTimeout(t);
    }
  }, [completed]);

  const finish = useCallback(() => {
    setOnboardingCompleted(true);
    setOpen(false);
  }, [setOnboardingCompleted]);

  const current = STEPS[step];
  const isLast = step >= STEPS.length - 1;

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) finish(); else setOpen(v); }}>
      <DialogContent className="sm:max-w-md" data-tour="onboarding">
        <DialogHeader>
          <DialogTitle>{current.title}</DialogTitle>
          <DialogDescription className="text-sm leading-relaxed">
            {current.body}
          </DialogDescription>
        </DialogHeader>
        <div className="flex gap-1 py-1">
          {STEPS.map((_, i) => (
            <div
              key={i}
              className={`h-1 flex-1 rounded-full ${i <= step ? 'bg-primary' : 'bg-muted'}`}
            />
          ))}
        </div>
        <DialogFooter className="gap-2 sm:justify-between">
          <Button variant="ghost" size="sm" onClick={finish}>
            Skip tour
          </Button>
          <div className="flex gap-2">
            {step > 0 && (
              <Button variant="outline" size="sm" onClick={() => setStep((s) => s - 1)}>
                Back
              </Button>
            )}
            <Button
              size="sm"
              onClick={() => {
                if (isLast) {
                  finish();
                } else {
                  runStepAction(STEPS[step + 1]?.action);
                  setStep((s) => s + 1);
                }
              }}
            >
              {isLast ? 'Get started' : 'Next'}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
