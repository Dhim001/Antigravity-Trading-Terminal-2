import { useCallback, useState } from 'react';
import { toast } from 'sonner';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { useStore } from '../store/useStore';

/**
 * On-demand chart structure vision (1h/4h) — shared by Analyst tab and chart badge.
 */
export function useChartVision(symbol, { visionTf = '4h', barTime = null } = {}) {
  const setActiveSymbol = useStore((s) => s.setActiveSymbol);
  const [loading, setLoading] = useState(false);

  const requestVision = useCallback(() => {
    setLoading(true);
    const handler = (e) => {
      window.removeEventListener('chart-capture-ready', handler);
      const { image, bar_time } = e.detail || {};
      if (!image) {
        setLoading(false);
        toast.error('Chart capture failed');
        return;
      }
      sendAction(Action.CHART_VISION, {
        symbol,
        timeframe: visionTf,
        image_base64: image.replace(/^data:image\/png;base64,/, ''),
        bar_time: bar_time || barTime || Math.floor(Date.now() / 1000),
      }).finally(() => setLoading(false));
    };
    window.addEventListener('chart-capture-ready', handler);
    setActiveSymbol(symbol);
    window.dispatchEvent(new CustomEvent('chart-capture-request', {
      detail: { symbol, bar_time: barTime, timeframe: visionTf },
    }));
    setTimeout(() => {
      window.removeEventListener('chart-capture-ready', handler);
      setLoading(false);
    }, 5000);
  }, [symbol, visionTf, barTime, setActiveSymbol]);

  return { requestVision, loading };
}
