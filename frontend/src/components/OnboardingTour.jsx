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
    body: 'A sim/live trading terminal with chart analyst, algo bots, and a full order ticket. This tour highlights the main workflows.',
    target: null,
  },
  {
    title: 'Pick a symbol',
    body: 'Use the watchlist on the left or press ⌘K to search. The chart and order ticket follow your selection.',
    target: 'watchlist',
    action: 'watchlist',
  },
  {
    title: 'Read the chart',
    body: 'The Chart Analyst badge shows server signals (BUY/SELL) with trend, momentum, and risk sub-reports. Switch timeframes on the toolbar.',
    target: 'chart',
    action: 'chart',
  },
  {
    title: 'Place an order',
    body: 'The order ticket includes a pre-trade preview — qty, notional, SL/TP, and blocked reasons before you submit.',
    target: 'order-panel',
    action: 'order',
  },
  {
    title: 'Backtest & deploy bots',
    body: 'Open Automation Studio (Algo tab) to pick a strategy template, set trailing stop loss, run a backtest, and deploy.',
    target: 'algo-deploy',
    action: 'algo',
  },
  {
    title: 'Backtest Lab & Optimizer',
    body: 'After a backtest, open Backtest Lab → Optimizer to sweep parameters, run walk-forward validation, and deploy the winning config.',
    target: 'algo-deploy',
    action: 'algo',
  },
  {
    title: 'Chart Analyst',
    body: 'Press ⌘I for Insights Hub — scanner ranks symbols; Analyst tab shows insight history with sub-reports.',
    target: 'insights-hub',
    action: 'insights',
  },
  {
    title: 'Deep reasoning & vision',
    body: 'Expand an Analyst row for deep reasoning (LLM summary + risk notes — signal unchanged). Chart vision describes structure on 1H/4H. Enable AGENT_LLM_ENABLED in backend .env.',
    target: null,
  },
  {
    title: 'Ambiguous orders',
    body: 'Live mode: unknown broker outcomes appear in Reconcile tab only. Confirm filled or dismiss — never resend automatically.',
    target: null,
  },
  {
    title: 'Bottom dock',
    body: 'Positions, orders, balances, and active bots live in the bottom dock. Use ⌘B to jump to Algo.',
    target: 'bottom-dock',
    action: 'dock',
  },
  {
    title: 'You are set',
    body: 'Press ? for keyboard shortcuts. Open Preferences (⌘,) for themes, workspaces, and alerts.',
    target: null,
  },
];

function runStepAction(action) {
  if (!action || typeof window === 'undefined') return;
  switch (action) {
    case 'watchlist':
      window.dispatchEvent(new CustomEvent('sidebar-expand'));
      break;
    case 'chart':
      window.dispatchEvent(new CustomEvent('chart-focus'));
      break;
    case 'order':
      window.dispatchEvent(new CustomEvent('trading-panel-expand'));
      break;
    case 'algo':
      window.dispatchEvent(new CustomEvent('automation-studio-open'));
      window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'algo' }));
      break;
    case 'insights':
      window.dispatchEvent(new CustomEvent('insights-hub-open'));
      break;
    case 'dock':
      window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'positions' }));
      break;
    default:
      break;
  }
}

function TourSpotlight({ target }) {
  const [rect, setRect] = useState(null);

  useEffect(() => {
    if (!target) {
      setRect(null);
      return undefined;
    }
    const update = () => {
      const el = document.querySelector(`[data-tour="${target}"]`);
      if (!el) {
        setRect(null);
        return;
      }
      const r = el.getBoundingClientRect();
      setRect({
        top: r.top - 6,
        left: r.left - 6,
        width: r.width + 12,
        height: r.height + 12,
      });
    };
    update();
    const interval = setInterval(update, 250);
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      clearInterval(interval);
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [target]);

  if (!rect) return null;

  return (
    <div
      className="tour-spotlight-ring"
      style={{
        top: rect.top,
        left: rect.left,
        width: rect.width,
        height: rect.height,
      }}
      aria-hidden
    />
  );
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
    <>
      {open && <TourSpotlight target={current.target} />}
      <Dialog modal={false} open={open} onOpenChange={(v) => { if (!v) finish(); else setOpen(v); }}>
        <DialogContent
          className="onboarding-tour-dialog pointer-events-auto bottom-4 left-auto right-4 top-auto max-w-sm translate-x-0 translate-y-0 sm:max-w-md"
          overlayClassName="pointer-events-none bg-transparent supports-backdrop-filter:backdrop-blur-none"
          data-tour="onboarding"
        >
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
                    const next = STEPS[step + 1];
                    if (next?.action) runStepAction(next.action);
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
    </>
  );
}
