import { useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { useResearchStore } from '../store/useResearchStore';
import { useSettingsStore } from '../store/useSettingsStore';

/**
 * Evaluate persisted alert rules against live ticker + analyst signals.
 * Uses store.subscribe to avoid React re-renders on every market tick.
 */
export function useAlertMonitor() {
  const alerts = useSettingsStore((s) => s.settings.alerts ?? []);
  const alertsRef = useRef(alerts);
  alertsRef.current = alerts;

  const prevRef = useRef({ prices: {}, signals: {} });

  useEffect(() => {
    const evaluate = () => {
      const state = useStore.getState();
      const research = useResearchStore.getState();
      const rules = alertsRef.current;
      if (!rules.length) return;

      const enabled = rules.filter((a) => a.enabled !== false);
      const prev = prevRef.current;

      for (const rule of enabled) {
        const sym = rule.symbol?.toUpperCase();
        if (!sym) continue;

        if (rule.type === 'price_above' && rule.threshold != null) {
          const px = state.tickerData[sym]?.price;
          if (px != null && px >= rule.threshold && (prev.prices[sym] ?? 0) < rule.threshold) {
            toast.info(`${sym} crossed above ${rule.threshold}`, { id: `alert-${rule.id}` });
          }
          if (px != null) prev.prices[sym] = px;
        }

        if (rule.type === 'price_below' && rule.threshold != null) {
          const px = state.tickerData[sym]?.price;
          if (px != null && px <= rule.threshold && (prev.prices[sym] ?? Infinity) > rule.threshold) {
            toast.warning(`${sym} crossed below ${rule.threshold}`, { id: `alert-${rule.id}` });
          }
          if (px != null) prev.prices[sym] = px;
        }

        if (rule.type === 'signal_change') {
          const sig = research.agentInsights[sym]?.signal;
          const want = rule.signal || 'BUY';
          const was = prev.signals[sym];
          if (sig && sig !== was && sig === want) {
            toast.success(`${sym} analyst signal → ${sig}`, { id: `alert-sig-${rule.id}` });
          }
          if (sig) prev.signals[sym] = sig;
        }
      }
    };

    evaluate();
    const unsubMarket = useStore.subscribe(evaluate);
    const unsubResearch = useResearchStore.subscribe(evaluate);
    return () => {
      unsubMarket();
      unsubResearch();
    };
  }, []);
}
