import { useCallback, useEffect, useRef, useState } from 'react';
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
  const timeoutRef = useRef(null);
  const handlerRef = useRef(null);

  useEffect(() => () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    if (handlerRef.current) {
      window.removeEventListener('chart-capture-ready', handlerRef.current);
      handlerRef.current = null;
    }
  }, []);

  const requestVision = useCallback(() => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    if (handlerRef.current) {
      window.removeEventListener('chart-capture-ready', handlerRef.current);
      handlerRef.current = null;
    }

    setLoading(true);
    const handler = (e) => {
      window.removeEventListener('chart-capture-ready', handler);
      if (handlerRef.current === handler) handlerRef.current = null;
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
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
    handlerRef.current = handler;
    window.addEventListener('chart-capture-ready', handler);
    setActiveSymbol(symbol);
    window.dispatchEvent(new CustomEvent('chart-capture-request', {
      detail: { symbol, bar_time: barTime, timeframe: visionTf },
    }));
    timeoutRef.current = setTimeout(() => {
      timeoutRef.current = null;
      window.removeEventListener('chart-capture-ready', handler);
      if (handlerRef.current === handler) handlerRef.current = null;
      setLoading(false);
    }, 5000);
  }, [symbol, visionTf, barTime, setActiveSymbol]);

  return { requestVision, loading };
}
