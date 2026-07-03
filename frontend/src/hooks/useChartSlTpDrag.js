import { useEffect } from 'react';
import { Action } from '../api/protocol';
import { sendAction } from '../api/transport';
import { useStore } from '../store/useStore';
import {
  kindFromTarget,
  isDraftTarget,
  clampSlTpPrice,
} from '../lib/chart/slTpOverlay';

/**
 * Global mousemove/mouseup handlers for draggable SL/TP chart lines.
 */
export function useChartSlTpDrag({
  chartRef,
  chartReadyRef,
  slTpDragRef,
  slTpDragPricesRef,
  symbolPositionRef,
  chartSlTpDraftRef,
  activeSymbolRef,
  renderChartGraphicsRef,
  setChartSlTpDraft,
  setSlTpDragging,
  setSlTpOverlayTick,
}) {
  useEffect(() => {
    const onMove = (ev) => {
      const drag = slTpDragRef.current;
      const chart = chartRef.current;
      if (!drag || !chart || !chartReadyRef.current) return;
      const rect = chart.getDom().getBoundingClientRect();
      const offsetY = ev.clientY - rect.top;
      const pointInValue = chart.convertFromPixel({ gridIndex: 0 }, [ev.clientX - rect.left, offsetY]);
      const rawPrice = pointInValue?.[1];
      if (rawPrice == null || !(rawPrice > 0)) return;

      const target = drag.target;
      const kind = kindFromTarget(target);
      if (!kind) return;

      const pos = symbolPositionRef.current;
      const draft = chartSlTpDraftRef.current;
      const sym = activeSymbolRef.current;
      const liveTicker = useStore.getState().tickerData[sym]?.price;
      const side = isDraftTarget(target)
        ? (draft?.side || 'BUY')
        : (pos?.size > 0 ? 'BUY' : 'SELL');
      const refPrice = isDraftTarget(target)
        ? (liveTicker ?? draft?.ref_price ?? rawPrice)
        : (pos?.avg_price ?? liveTicker ?? rawPrice);
      const clamped = clampSlTpPrice(rawPrice, side, refPrice, kind);
      if (clamped == null) return;

      const key = isDraftTarget(target)
        ? (kind === 'sl' ? 'draftSl' : 'draftTp')
        : (kind === 'sl' ? 'sl' : 'tp');
      slTpDragPricesRef.current = { ...slTpDragPricesRef.current, [key]: clamped };
      renderChartGraphicsRef.current?.();
    };

    const onUp = () => {
      const drag = slTpDragRef.current;
      if (!drag) return;
      slTpDragRef.current = null;
      const prices = slTpDragPricesRef.current;
      slTpDragPricesRef.current = {};
      setSlTpDragging(false);
      const target = drag.target;

      if (isDraftTarget(target)) {
        const draft = chartSlTpDraftRef.current || { symbol: activeSymbolRef.current, side: 'BUY' };
        const sym = activeSymbolRef.current;
        const next = { ...draft, symbol: sym, source: 'chart' };
        if (prices.draftSl != null) next.stop_loss_price = prices.draftSl;
        if (prices.draftTp != null) next.take_profit_price = prices.draftTp;
        setChartSlTpDraft(next);
      } else {
        const sym = activeSymbolRef.current;
        const payload = { symbol: sym };
        if (prices.sl != null) payload.stop_loss_price = prices.sl;
        if (prices.tp != null) payload.take_profit_price = prices.tp;
        if (payload.stop_loss_price != null || payload.take_profit_price != null) {
          sendAction(Action.UPDATE_POSITION_SL_TP, payload);
        }
      }
      setSlTpOverlayTick((t) => t + 1);
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [
    activeSymbolRef,
    chartReadyRef,
    chartRef,
    chartSlTpDraftRef,
    renderChartGraphicsRef,
    setChartSlTpDraft,
    setSlTpDragging,
    setSlTpOverlayTick,
    slTpDragPricesRef,
    slTpDragRef,
    symbolPositionRef,
  ]);
}
