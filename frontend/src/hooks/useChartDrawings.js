/**
 * useChartDrawings — per-symbol chart drawing state with backend persistence.
 *
 * Drawings live in the Zustand store keyed by symbol and are loaded/saved via
 * the WebSocket action protocol (CHART_DRAWINGS_GET / CHART_DRAWINGS_SET). Local
 * mutations are applied optimistically to the store and then persisted.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { isValidDrawing } from '../lib/chart/drawings';

export function useChartDrawings(symbol) {
  const drawingsBySymbol = useStore((s) => s.chartDrawings);
  const setChartDrawings = useStore((s) => s.setChartDrawings);

  const [activeTool, setActiveTool] = useState(null); // null | trendline | hline | rectangle | fib
  const [selectedId, setSelectedId] = useState(null);

  const drawings = useMemo(
    () => drawingsBySymbol[symbol] || [],
    [drawingsBySymbol, symbol],
  );

  // Load persisted drawings whenever the symbol changes (once per symbol).
  useEffect(() => {
    if (!symbol) return;
    if (drawingsBySymbol[symbol] === undefined) {
      sendAction(Action.CHART_DRAWINGS_GET, { symbol });
    }
    setSelectedId(null);
    setActiveTool(null);
  }, [symbol]); // eslint-disable-line react-hooks/exhaustive-deps

  const persist = useCallback((next) => {
    setChartDrawings(symbol, next);
    sendAction(Action.CHART_DRAWINGS_SET, { symbol, drawings: next });
  }, [symbol, setChartDrawings]);

  const addDrawing = useCallback((drawing) => {
    if (!isValidDrawing(drawing)) return;
    const next = [...drawings, drawing];
    persist(next);
  }, [drawings, persist]);

  const updateDrawing = useCallback((id, patch) => {
    const next = drawings.map((d) => (d.id === id ? { ...d, ...patch } : d));
    persist(next);
  }, [drawings, persist]);

  const removeDrawing = useCallback((id) => {
    const next = drawings.filter((d) => d.id !== id);
    persist(next);
    setSelectedId((cur) => (cur === id ? null : cur));
  }, [drawings, persist]);

  const clearDrawings = useCallback(() => {
    persist([]);
    setSelectedId(null);
  }, [persist]);

  return {
    drawings,
    activeTool,
    setActiveTool,
    selectedId,
    setSelectedId,
    addDrawing,
    updateDrawing,
    removeDrawing,
    clearDrawings,
  };
}
