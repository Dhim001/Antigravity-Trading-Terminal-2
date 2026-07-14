/**
 * ChartWidget.jsx — Professional Trading Chart using Apache ECharts
 */
import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import * as echarts from 'echarts';
import { useStore } from '../store/useStore';
import { useResearchStore } from '../store/useResearchStore';
import {
  useLiveCandleRevision,
  useHistoryCandleRevision,
  subscribeLiveRevisions,
} from '../services/candleRevisions';
import { useSettingsStore } from '../store/useSettingsStore';
import { getChartEchartsTheme, hexToRgba } from '../settings/applySettings';
import {
  getIndicatorTheme,
  getIndicatorToolbarMeta,
  volumeBarEntry,
  macdHistogramColor,
  rsiMarkLine,
  macdZeroMarkLine,
  emaLineStyle,
} from '../settings/indicatorThemes';
import { CHART_LAYOUT_RESET_EVENT, DEFAULT_TERMINAL_SETTINGS } from '../settings/defaults';
import ChartHeaderPrice from './chart/ChartHeaderPrice';
import ChartSymbolSwitcher from './chart/ChartSymbolSwitcher';
import ChartToolbar from './chart/ChartToolbar';
import { useChartSlTpDrag } from '../hooks/useChartSlTpDrag';
import {
  TF_CONFIGS, CHART_DISPLAY_BARS, CHART_DISPLAY_MAX, ARCHIVE_LOAD_CHUNK, ARCHIVE_1M_RETENTION_SEC,
  FUTURE_PADDING, CHART_HISTORY_MIN_BARS, CHART_HISTORY_CACHED_BARS, MASSIVE_CHART_MIN_BARS,
  CHART_HISTORY_GATE_MS, LOAD_OLDER_MIN_INTERVAL_MS, CONFIGURE_DEBOUNCE_MS, CHART_VISIBLE_BARS,
  SERIES_ANIM_OFF, sliceRawForTimeframe, chartStructureKey, isChartHistoryReady, withSeriesAnimOff,
  formatTimeLabel, buildCategoryAxisData, indexOfCategoryKey, lastRealCategoryIndex,
  defaultDataZoomPercent, dataZoomEndIndex, isDataZoomAtLiveEdge, liveEdgeDataZoomForBars,
  buildDataZoomOption, preserveDataZoomPercent, hasValidDataZoom, markerYForTrade, categoryAxisLabelFormatter,
  categoryXAxisOpts, buildMainSeriesData, buildVolumeSeriesData, normalizeEchartsList,
  aggregateBucket, barMatches, bucketCandles, aggregateCandlesForSymbol,
  updateLiveSeriesCache, buildLightLiveSeriesPatchesFromCache, buildNewBarSeriesPatches,
  buildIndicatorSeriesPatches, buildAgentMarkLines, getPriceDecimals, buildMarkLineData,
  buildBotTradeMarkers, buildBacktestTradeMarkers, mapBacktestEquityLine, buildTradeMarkers,
  isChartDisposed, formatVol,
  mapEmaSeries, mapRsiSeries, mapMacdSeries, mapAtrSeries, mapBbSeries, mapVwapSeries,
} from '../lib/chart/chartHelpers';

import ChartAnalystBadge from './ChartAnalystBadge';
import {
  AreaChart, TrendingUp, Activity, Maximize2, Minimize2, CandlestickChart, Grid3x3,
  Spline, Minus, AlignJustify, Square, BarChart2, Trash2,
  History, Play, Pause, SkipForward, SkipBack, RotateCcw, X,
} from 'lucide-react';
import { WidgetShell, WidgetToolbar, WidgetToolbarDivider } from './WidgetShell';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Button } from '@/components/ui/button';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { GitCompareArrows } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { BACKTEST_OVERLAY_EVENT, symbolsMatch } from '@/lib/backtestDisplay';
import { getCandles, getOldestBarTime, toUnixSeconds, candleBufferKey, patchHtFormingBar, patchHtFormingBarFromPrice, applyLivePrice, chartTimeframeSecs, isHigherTimeframe, hasChartReadyHistory, hasCandleHistory, setCandleHistory, setComparePinnedCandleSymbol, CHART_SNAPSHOT_BARS, CHART_READY_MIN_BARS } from '../services/candleBuffer';
import { fetchCandles } from '../api/endpoints';
import { getStoreActions } from '../api/dispatch';
import { isLiveMassiveMode } from '../lib/massiveMarket';
import { onLivePrice } from '../services/livePriceChannel';
import { selectAgentInsight } from '../lib/agentInsights';
import { fetchOlderCandles } from '../api/endpoints';
import { Action } from '../api/protocol';
import {
  CHART_DISPLAY_BARS_DEFAULT,
  CHART_DISPLAY_MAX_BARS,
} from '../services/memoryBudget';
import { applyCandleTransform, estimateRenkoBrickSize } from '../lib/chart/candleTransforms';
import { computeVolumeProfile, volumeProfileGraphic } from '../lib/chart/volumeProfile';
import { alignComparisonSeries } from '../lib/chart/comparison';
import {
  createDrawing, drawingsToGraphic, hitTestDrawings, timeToFractionalIndex,
} from '../lib/chart/drawings';
import {
  buildSlTpGraphic,
  hitTestSlTp,
  clampSlTpPrice,
  kindFromTarget,
  isDraftTarget,
} from '../lib/chart/slTpOverlay';
import { sendAction } from '../api/transport';
import { useChartDrawings } from '../hooks/useChartDrawings';
// ─── Main Component ──────────────────────────────────────────────────
export default function ChartWidget() {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const candlesRef = useRef([]);
  const displayBarsRef = useRef([]);
  const chartLayoutRef = useRef({ xAxisCount: 1, showVolume: true });
  const liveRafRef = useRef(null);
  const liveLastPaintMs = useRef(0);
  const LIVE_MIN_INTERVAL_MS = 250;
  const DATAZOOM_HANDLER_MIN_MS = 400;
  const configureChartRef = useRef(() => {});
  const applyOverlayPatchRef = useRef(() => {});
  const chartReadyRef = useRef(false);
  const chartConfiguringRef = useRef(false);
  const configureDebounceRef = useRef(null);
  const prevStructureKeyRef = useRef('');
  const suppressDataZoomEventsRef = useRef(0);
  const loadOlderLastMsRef = useRef(0);
  const loadingOlderRef = useRef(false);
  const olderExhaustedRef = useRef({});
  const loadOlderRef = useRef(null);
  const pinnedToLiveRef = useRef(true);
  const liveSeriesCacheRef = useRef({ main: null, volume: null, barCount: 0, chartType: null });
  const renkoBrickSizeRef = useRef(0);
  const dataZoomHandlerLastMsRef = useRef(0);
  const lastConfigureRevisionRef = useRef('');
  const chartHistoryReadyRef = useRef(false);
  const htFetchRef = useRef(null);

  const [chartMountKey, setChartMountKey] = useState(0);
  const [displayBarLimit, setDisplayBarLimit] = useState(CHART_DISPLAY_BARS);
  const [historyGateForced, setHistoryGateForced] = useState(false);
  const settings = useSettingsStore(state => state.settings);
  const resolvedTheme = useSettingsStore(state => state.resolvedTheme);
  const [timeframe, setTimeframe] = useState(() => settings.chartLayout?.timeframe || '1m');

  const activeSymbol = useStore(state => state.activeSymbol);
  const terminalMode = useStore(state => state.terminalMode);
  const useNativeHt = isLiveMassiveMode(terminalMode) && isHigherTimeframe(timeframe);
  const chartBufKey = useNativeHt ? candleBufferKey(activeSymbol, timeframe) : activeSymbol;
  const historyRev = useHistoryCandleRevision(chartBufKey);
  const candleRev = useLiveCandleRevision(chartBufKey);
  const oneMinRev = useLiveCandleRevision(activeSymbol);
  const lastCandleTime = useMemo(() => {
    const intervalSecs = chartTimeframeSecs(timeframe);
    const candles = getCandles(activeSymbol, useNativeHt ? timeframe : '1m', intervalSecs);
    return candles.length > 0 ? candles[candles.length - 1].time : 0;
  }, [activeSymbol, candleRev, oneMinRev, timeframe, useNativeHt]);
  const symbolPosition = useStore(state => state.positions[activeSymbol]);
  const chartSlTpDraft = useStore(state => state.chartSlTpDraft);
  const setChartSlTpDraft = useStore(state => state.setChartSlTpDraft);
  const clearChartSlTpDraft = useStore(state => state.clearChartSlTpDraft);
  const positionOverlayKey = useStore(state => {
    const p = state.positions[activeSymbol];
    if (!p || p.size === 0) return '';
    return `${p.size}|${p.avg_price}|${p.stop_loss_price}|${p.take_profit_price}`;
  });
  const tradeOverlayKey = useStore(state => {
    let key = '';
    for (const t of state.tradeHistory) {
      if (t.symbol === activeSymbol && t.status === 'FILLED') {
        key += `${t.timestamp}:${t.side}:${t.filled_quantity ?? t.quantity}:${t.average_fill_price ?? t.price};`;
      }
    }
    return key;
  });
  const selectedBotId = useStore(state => state.selectedBotId);
  const botDetail = useStore(state => state.botDetail);
  const agentInsights = useResearchStore(state => state.agentInsights);
  const agentInsight = useMemo(
    () => selectAgentInsight(agentInsights, activeSymbol, timeframe),
    [agentInsights, activeSymbol, timeframe],
  );
  const setBotStrategy = useStore(state => state.setBotStrategy);
  const setBotExecutionMode = useStore(state => state.setBotExecutionMode);
  const setBotTimeframe = useStore(state => state.setBotTimeframe);
  const updateBotConfig = useStore(state => state.updateBotConfig);
  const agentOverlayKey = useMemo(() => {
    if (!agentInsight) return '';
    const lv = agentInsight.levels || {};
    return `${agentInsight.bar_time}|${agentInsight.signal}|${lv.stop_loss_distance}|${lv.take_profit_price}`;
  }, [agentInsight]);
  const handleDeployChartAgent = useCallback(() => {
    setBotStrategy('CHART_AGENT');
    setBotExecutionMode('BAR_CLOSE');
    setBotTimeframe(timeframe);
    updateBotConfig({
      min_confidence: agentInsight?.confidence ?? 0.55,
      use_llm: false,
      allocation: 2000,
      trailing_stop_percent: 2,
      take_profit_percent: 3,
      tp_mode: 'percent',
    });
  }, [agentInsight, setBotStrategy, setBotExecutionMode, setBotTimeframe, timeframe, updateBotConfig]);
  const botOverlayKey = useStore(state => {
    if (!state.selectedBotId || !state.botDetail?.trades) return '';
    return state.botDetail.trades.map(
      (t) => `${t.id}:${t.signal_bar_time ?? ''}:${t.signal_id ?? ''}:${t.side}`,
    ).join(';');
  });
  const backtestOverlay = useResearchStore(state => state.backtestOverlay);
  const backtestOverlayKey = useResearchStore(state => {
    const o = state.backtestOverlay;
    if (!o) return '';
    return `${o.visible ? 1 : 0}:${o.runId ?? ''}:${o.trades?.length ?? 0}:${o.symbol ?? ''}:${o.equityCurve?.length ?? 0}`;
  });
  const chartInteractionMode = useStore(state => state.chartInteractionMode);
  const setChartInteractionMode = useStore(state => state.setChartInteractionMode);

  const zenMode = useSettingsStore(state => state.settings.workspace?.zenMode ?? false);
  const updateChartLayout = useSettingsStore(state => state.updateChartLayout);
  const chartTheme = useMemo(
    () => getChartEchartsTheme(settings, resolvedTheme),
    [settings, resolvedTheme],
  );
  const indicatorTheme = useMemo(
    () => getIndicatorTheme(resolvedTheme),
    [resolvedTheme],
  );
  const indicatorToolbar = useMemo(
    () => getIndicatorToolbarMeta(indicatorTheme),
    [indicatorTheme],
  );

  const slTpHitLinesRef = useRef([]);
  const slTpDragRef = useRef(null);
  const slTpDragPricesRef = useRef({});
  const chartSlTpDraftRef = useRef(chartSlTpDraft);
  const symbolPositionRef = useRef(symbolPosition);
  chartSlTpDraftRef.current = chartSlTpDraft;
  symbolPositionRef.current = symbolPosition;

  const [slTpOverlayTick, setSlTpOverlayTick] = useState(0);
  const [slTpDragging, setSlTpDragging] = useState(false);
  const setSlTpDraggingRef = useRef(setSlTpDragging);
  setSlTpDraggingRef.current = setSlTpDragging;
  const activeSymbolRef = useRef(activeSymbol);
  activeSymbolRef.current = activeSymbol;
  const prevConfigRef = useRef({ symbol: activeSymbol, timeframe: timeframe });
  const layoutPushSkipRef = useRef(false);

  const chartLayoutFromSettings = settings.chartLayout;

  useEffect(() => {
    setHistoryGateForced(false);
    pinnedToLiveRef.current = true;
    prevStructureKeyRef.current = '';
    lastConfigureRevisionRef.current = '';
    const t = setTimeout(() => setHistoryGateForced(true), CHART_HISTORY_GATE_MS);
    return () => clearTimeout(t);
  }, [activeSymbol]);

  useEffect(() => {
    pinnedToLiveRef.current = true;
    prevStructureKeyRef.current = '';
    lastConfigureRevisionRef.current = '';
  }, [timeframe]);

  useEffect(() => {
    setDisplayBarLimit(CHART_DISPLAY_BARS);
    olderExhaustedRef.current[activeSymbol] = false;
  }, [activeSymbol, timeframe]);
  const [chartType, setChartType] = useState(() => settings.chartLayout?.chartType || 'candle');
  const [active, setActive] = useState(() => ({
    ...DEFAULT_TERMINAL_SETTINGS.chartLayout.activeIndicators,
    ...(settings.chartLayout?.activeIndicators || {}),
  }));

  // Live mirrors of local chart state — let the settings→chart sync compare
  // against the latest values without depending on them (which would revert
  // header-toggle changes before they propagate to settings).
  const timeframeRef = useRef(timeframe);
  const chartTypeRef = useRef(chartType);
  const activeRef = useRef(active);
  timeframeRef.current = timeframe;
  chartTypeRef.current = chartType;
  activeRef.current = active;

  // ── Volume Profile (VPVR) + drawing tools ──
  const [showVolumeProfile, setShowVolumeProfile] = useState(
    () => Boolean(settings.chartLayout?.volumeProfile),
  );
  const {
    drawings,
    activeTool,
    setActiveTool,
    selectedId: selectedDrawingId,
    setSelectedId: setSelectedDrawingId,
    addDrawing,
    removeDrawing,
    clearDrawings,
  } = useChartDrawings(activeSymbol);

  // ── Comparison mode (overlay a second symbol, rebased %) ──
  const [compareSymbol, setCompareSymbol] = useState(null);
  const compareSymbolRef = useRef(compareSymbol);
  compareSymbolRef.current = compareSymbol;
  // Reactive to the *set* of available symbols (not per-tick price changes).
  const compareSymbolsKey = useStore(
    (s) => Object.keys(s.tickerData || {}).sort().join(','),
  );
  const compareOptions = useMemo(
    () => (compareSymbolsKey ? compareSymbolsKey.split(',') : [])
      .filter((s) => s && s !== activeSymbol),
    [compareSymbolsKey, activeSymbol],
  );

  // ── Replay mode (bar-by-bar historical playback) ──
  const [replayActive, setReplayActive] = useState(false);
  const [replayIndex, setReplayIndex] = useState(0);
  const [replayPlaying, setReplayPlaying] = useState(false);
  const [replaySpeed, setReplaySpeed] = useState(1);
  const replayActiveRef = useRef(replayActive);
  replayActiveRef.current = replayActive;

  const showVolumeProfileRef = useRef(showVolumeProfile);
  const drawingsRef = useRef(drawings);
  const activeToolRef = useRef(activeTool);
  const selectedDrawingIdRef = useRef(selectedDrawingId);
  const addDrawingRef = useRef(addDrawing);
  const pendingDrawPointRef = useRef(null);
  const overlayIdsRef = useRef(new Set());
  showVolumeProfileRef.current = showVolumeProfile;
  drawingsRef.current = drawings;
  activeToolRef.current = activeTool;
  selectedDrawingIdRef.current = selectedDrawingId;
  addDrawingRef.current = addDrawing;

  useEffect(() => {
    lastConfigureRevisionRef.current = '';
  }, [activeSymbol, timeframe, active]);


  useEffect(() => { try { localStorage.setItem('terminal_tf', timeframe); } catch {} }, [timeframe]);

  // LIVE_MASSIVE: lazy-fetch native HT history from Massive REST on TF switch
  useEffect(() => {
    if (!useNativeHt || !activeSymbol) return;
    if (hasChartReadyHistory(activeSymbol, CHART_READY_MIN_BARS, timeframe)) return;

    const fetchKey = `${activeSymbol}|${timeframe}`;
    if (htFetchRef.current === fetchKey) return;
    htFetchRef.current = fetchKey;

    fetchCandles(activeSymbol, getStoreActions(), {
      limit: CHART_SNAPSHOT_BARS,
      interval: timeframe,
      timeoutMs: 25000,
    }).catch((err) => {
      console.warn('[ChartWidget] HT candle fetch failed:', err?.message || err);
    }).finally(() => {
      if (htFetchRef.current === fetchKey) htFetchRef.current = null;
    });

    if (!hasChartReadyHistory(activeSymbol, CHART_READY_MIN_BARS, '1m')) {
      fetchCandles(activeSymbol, getStoreActions(), {
        limit: 120,
        interval: '1m',
        timeoutMs: 15000,
      }).catch(() => {});
    }
  }, [activeSymbol, timeframe, useNativeHt, historyRev]);

  useEffect(() => { try { localStorage.setItem('terminal_chart_type', chartType); } catch {} }, [chartType]);
  useEffect(() => { try { localStorage.setItem('terminal_chart_indicators_active', JSON.stringify(active)); } catch {} }, [active]);

  useEffect(() => {
    const cl = chartLayoutFromSettings;
    if (!cl) return;

    let pulled = false;
    if (cl.timeframe && cl.timeframe !== timeframeRef.current) {
      setTimeframe(cl.timeframe);
      pulled = true;
    }
    if (cl.chartType && cl.chartType !== chartTypeRef.current) {
      setChartType(cl.chartType);
      pulled = true;
    }
    if (cl.activeIndicators) {
      const keys = Object.keys(indicatorToolbar);
      const differs = keys.some((k) => Boolean(cl.activeIndicators[k]) !== Boolean(activeRef.current[k]));
      if (differs) {
        setActive((prev) => ({ ...prev, ...cl.activeIndicators }));
        pulled = true;
      }
    }
    if (pulled) layoutPushSkipRef.current = true;
  }, [
    chartLayoutFromSettings?.timeframe,
    chartLayoutFromSettings?.chartType,
    chartLayoutFromSettings?.activeIndicators,
    indicatorToolbar,
  ]);

  useEffect(() => {
    if (layoutPushSkipRef.current) {
      layoutPushSkipRef.current = false;
      return;
    }
    updateChartLayout({ timeframe, chartType, activeIndicators: active });
  }, [timeframe, chartType, active, updateChartLayout]);

  const safeChartResize = useCallback(() => {
    const chart = chartRef.current;
    if (isChartDisposed(chart) || !chartReadyRef.current || chartConfiguringRef.current) return false;
    if (!hasValidDataZoom(chart)) return false;
    try {
      chart.resize();
      return true;
    } catch (err) {
      console.warn('[ChartWidget] resize failed:', err);
      return false;
    }
  }, []);

  const safeChartResizeRef = useRef(safeChartResize);
  safeChartResizeRef.current = safeChartResize;

  useEffect(() => {
    if (!chartReadyRef.current) return undefined;
    const id = requestAnimationFrame(() => {
      requestAnimationFrame(() => safeChartResizeRef.current());
    });
    return () => cancelAnimationFrame(id);
  }, [zenMode]);

  useEffect(() => {
    const onReset = (e) => {
      const cl = e.detail?.chartLayout ?? DEFAULT_TERMINAL_SETTINGS.chartLayout;
      layoutPushSkipRef.current = true;
      setTimeframe(cl.timeframe);
      setChartType(cl.chartType);
      setActive({ ...cl.activeIndicators });
      chartReadyRef.current = false;
      try { chartRef.current?.clear(); } catch (_) {}
    };
    window.addEventListener(CHART_LAYOUT_RESET_EVENT, onReset);
    return () => window.removeEventListener(CHART_LAYOUT_RESET_EVENT, onReset);
  }, []);

  useEffect(() => {
    const onCaptureRequest = (e) => {
      const sym = e.detail?.symbol;
      if (sym && sym !== activeSymbol) return;
      const captureTfRaw = (e.detail?.timeframe || '').toLowerCase();
      const captureChartTf = captureTfRaw === '1h' ? '1H' : captureTfRaw === '4h' ? '4H' : null;

      const emitCapture = () => {
        const chart = chartRef.current;
        if (!chart) return;
        try {
          const image = chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#0a0a0a' });
          window.dispatchEvent(new CustomEvent('chart-capture-ready', {
            detail: { symbol: activeSymbol, image, bar_time: e.detail?.bar_time },
          }));
        } catch (_) {}
      };

      if (captureChartTf && captureChartTf !== timeframeRef.current) {
        const prevTf = timeframeRef.current;
        setTimeframe(captureChartTf);
        window.setTimeout(() => {
          emitCapture();
          setTimeframe(prevTf);
        }, 500);
        return;
      }
      emitCapture();
    };
    window.addEventListener('chart-capture-request', onCaptureRequest);
    return () => window.removeEventListener('chart-capture-request', onCaptureRequest);
  }, [activeSymbol]);

  const activeIndicatorKeys = useMemo(
    () => Object.entries(active).filter(([, on]) => on).map(([k]) => k),
    [active]
  );

  const handleIndicatorsChange = useCallback((vals) => {
    setActive(prev => {
      const next = { ...prev };
      for (const k of Object.keys(indicatorToolbar)) next[k] = vals.includes(k);
      return next;
    });
  }, [indicatorToolbar]);

  // Aggregate candles based on timeframe; chart renders a rolling window only
  const aggregatedCandles = useMemo(() => {
    const cfg = TF_CONFIGS.find(t => t.label === timeframe) || TF_CONFIGS[0];
    const limit = displayBarLimit;

    if (useNativeHt) {
      const native = getCandles(activeSymbol, timeframe, cfg.secs);
      if (native.length > 0) {
        return native.length > limit ? native.slice(-limit) : native;
      }
      // Stale-while-revalidate: show bucketed 1m until native HT REST returns
      const raw = getCandles(activeSymbol, '1m', 60);
      if (!raw.length) return [];
      const rawSlice = sliceRawForTimeframe(raw, cfg.secs, limit);
      const fallback = bucketCandles(rawSlice, cfg.secs);
      return fallback.length > limit ? fallback.slice(-limit) : fallback;
    }

    const raw = getCandles(activeSymbol, '1m', 60);
    if (!raw.length) return [];

    const rawSlice = sliceRawForTimeframe(raw, cfg.secs, limit);
    const series = bucketCandles(rawSlice, cfg.secs);
    return series.length > limit ? series.slice(-limit) : series;
  }, [timeframe, activeSymbol, historyRev, displayBarLimit, useNativeHt]);

  // Comparison-symbol candle revision (the active-symbol historyRev won't fire
  // when only the comparison symbol's data arrives).
  const compareHistRev = useHistoryCandleRevision(compareSymbol || '');
  const compareLiveRev = useLiveCandleRevision(compareSymbol || '');
  const compareRev = compareHistRev + compareLiveRev;

  // Comparison-symbol candles aggregated to the active timeframe.
  // Bumped after comparison history is (re)loaded so the memo recomputes even
  // when neither the active-symbol nor compare-symbol live revisions change.
  const [compareLoadRev, setCompareLoadRev] = useState(0);

  const compareCandles = useMemo(() => {
    if (!compareSymbol) return [];
    const cfg = TF_CONFIGS.find(t => t.label === timeframe) || TF_CONFIGS[0];
    return aggregateCandlesForSymbol(compareSymbol, cfg, displayBarLimit, useNativeHt);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compareSymbol, timeframe, historyRev, compareRev, compareLoadRev, displayBarLimit, useNativeHt]);

  // Load comparison-symbol history when selected. The generic history merge
  // rejects an archived snapshot older than a pre-existing live bar (which the
  // compare symbol usually already has from the ticker feed), so replace the
  // buffer directly. Pin it so the LRU cache doesn't evict it (only the active
  // chart symbol is pinned by default).
  useEffect(() => {
    setComparePinnedCandleSymbol(compareSymbol);
    if (!compareSymbol) return undefined;
    const interval = useNativeHt ? timeframe : '1m';
    fetchCandles(compareSymbol, getStoreActions(), {
      limit: CHART_SNAPSHOT_BARS,
      interval,
    }).then((body) => {
      const bars = body?.data?.[compareSymbol];
      if (Array.isArray(bars) && bars.length > 1) {
        setCandleHistory(compareSymbol, bars, interval, chartTimeframeSecs(interval));
        setCompareLoadRev((n) => n + 1);
      }
    }).catch(() => {});
    return () => setComparePinnedCandleSymbol(null);
  }, [compareSymbol, timeframe, useNativeHt]);

  // During replay, only reveal bars up to the replay cursor.
  const effectiveCandles = useMemo(() => {
    if (!replayActive) return aggregatedCandles;
    const end = Math.min(replayIndex, aggregatedCandles.length);
    return aggregatedCandles.slice(0, Math.max(1, end));
  }, [replayActive, replayIndex, aggregatedCandles]);

  // Stable Renko brick size per symbol/timeframe so bricks don't rescale on every tick.
  useEffect(() => {
    if (chartType === 'renko') {
      renkoBrickSizeRef.current = estimateRenkoBrickSize(aggregatedCandles);
    } else {
      renkoBrickSizeRef.current = 0;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartType, activeSymbol, timeframe, aggregatedCandles.length > 0]);

  const nativeHtLoaded = useNativeHt && hasCandleHistory(activeSymbol, timeframe);

  const chartHistoryReady = useMemo(
    () => isChartHistoryReady(
      aggregatedCandles.length,
      historyRev,
      historyGateForced,
      terminalMode,
      useNativeHt,
    ),
    [aggregatedCandles.length, historyRev, historyGateForced, terminalMode, useNativeHt],
  );

  chartHistoryReadyRef.current = chartHistoryReady;

  const configureRevision = useMemo(() => [
    activeSymbol,
    timeframe,
    chartType,
    historyRev,
    displayBarLimit,
    chartHistoryReady ? 1 : 0,
    activeIndicatorKeys.join(','),
    backtestOverlayKey,
    resolvedTheme,
    replayActive ? `replay:${replayIndex}` : 'live',
    compareSymbol ? `cmp:${compareSymbol}:${compareCandles.length}` : 'nocmp',
  ].join('|'), [
    activeSymbol, timeframe, chartType, historyRev, displayBarLimit, chartHistoryReady,
    activeIndicatorKeys, backtestOverlayKey, resolvedTheme, replayActive, replayIndex,
    compareSymbol, compareCandles.length,
  ]);

  const displayBarsSyncKey = useMemo(() => {
    const last = aggregatedCandles[aggregatedCandles.length - 1];
    return [
      activeSymbol,
      timeframe,
      historyRev,
      displayBarLimit,
      useNativeHt ? 1 : 0,
      aggregatedCandles.length,
      last?.time ?? 0,
    ].join('|');
  }, [
    activeSymbol,
    timeframe,
    historyRev,
    displayBarLimit,
    useNativeHt,
    aggregatedCandles.length,
    aggregatedCandles[aggregatedCandles.length - 1]?.time,
  ]);

  // Sync display buffer on structural changes only — live OHLC patches use applyLiveCandleUpdate.
  const lastSetBarsMsRef = useRef(0);
  useEffect(() => {
    const now = Date.now();
    
    // Throttle setBars to max 2Hz (500ms) when we are actively scrolling history 
    // and live ticks are coming in rapidly.
    if (!pinnedToLiveRef.current && (now - lastSetBarsMsRef.current < 500)) {
      return;
    }
    lastSetBarsMsRef.current = now;

    const prev = displayBarsRef.current;
    displayBarsRef.current = effectiveCandles.map(c => ({ ...c }));
    candlesRef.current = displayBarsRef.current;
    const next = displayBarsRef.current;
    const barCountChanged = prev.length !== next.length;
    const lastTimeChanged = prev.length > 0 && next.length > 0
      && prev[prev.length - 1].time !== next[next.length - 1].time;
    if (barCountChanged || lastTimeChanged || !liveSeriesCacheRef.current.main) {
      liveSeriesCacheRef.current = { main: null, volume: null, barCount: 0, chartType: null };
    }
  }, [displayBarsSyncKey, effectiveCandles, replayIndex]);

  // Direct DOM Legend update
  const updateLegendDOM = useCallback((bar) => {
    const openEl = document.getElementById('chart-legend-o');
    const highEl = document.getElementById('chart-legend-h');
    const lowEl  = document.getElementById('chart-legend-l');
    const closeEl = document.getElementById('chart-legend-c');
    const volEl   = document.getElementById('chart-legend-v');
    const pctEl   = document.getElementById('chart-legend-pct');
    if (!openEl || !bar) return;

    const isBull = bar.close >= bar.open;
    const color = isBull ? 'var(--color-up)' : 'var(--color-down)';
    const dec = getPriceDecimals(bar.close);

    openEl.textContent = bar.open.toFixed(dec);
    openEl.style.color = color;
    highEl.textContent = bar.high.toFixed(dec);
    highEl.style.color = color;
    lowEl.textContent  = bar.low.toFixed(dec);
    lowEl.style.color  = color;
    closeEl.textContent = bar.close.toFixed(dec);
    closeEl.style.color = color;
    volEl.textContent   = formatVol(bar.volume);
    
    if (bar.open > 0) {
      const pct = ((bar.close - bar.open) / bar.open) * 100;
      pctEl.textContent = `${isBull ? '+' : ''}${pct.toFixed(2)}%`;
      pctEl.style.color = color;
    } else {
      pctEl.textContent = '';
    }
  }, []);

  // Set up chart options
  const configureChart = useCallback(() => {
    if (!chartRef.current || aggregatedCandles.length === 0 || !chartHistoryReady) {
      chartReadyRef.current = false;
      return;
    }

    chartConfiguringRef.current = true;
    chartReadyRef.current = false;
    if (liveRafRef.current != null) {
      cancelAnimationFrame(liveRafRef.current);
      liveRafRef.current = null;
    }

    const candles = displayBarsRef.current.length > 0
      ? displayBarsRef.current
      : aggregatedCandles;
    const dec = getPriceDecimals(candles[candles.length - 1]?.close);

    const categoryData = buildCategoryAxisData(candles);

    const layoutChanged = prevConfigRef.current.symbol !== activeSymbol
      || prevConfigRef.current.timeframe !== timeframe;

    // Preserve zoom (% window) — skip getOption when symbol/timeframe changed (expensive + stale)
    let zoomStart = null;
    let zoomEnd = null;

    if (!layoutChanged && chartRef.current) {
      try {
        const currentOption = chartRef.current.getOption();
        const dataZoomList = normalizeEchartsList(currentOption?.dataZoom);
        const xAxisList = normalizeEchartsList(currentOption?.xAxis);
        if (dataZoomList[0] && xAxisList[0]?.data) {
          const preserved = preserveDataZoomPercent(
            xAxisList[0].data,
            categoryData,
            dataZoomList[0],
            candles.length,
            candles.length,
          );
          if (preserved) {
            zoomStart = preserved.start;
            zoomEnd = preserved.end;
          }
        }
      } catch (err) {
        console.warn('[ChartWidget] zoom preservation failed:', err);
      }
    }

    if (zoomStart == null || zoomEnd == null) {
      ({ start: zoomStart, end: zoomEnd } = liveEdgeDataZoomForBars(candles.length, categoryData));
      pinnedToLiveRef.current = true;
    } else {
      pinnedToLiveRef.current = isDataZoomAtLiveEdge(
        { start: zoomStart, end: zoomEnd },
        categoryData,
      );
    }
    if (!Number.isFinite(zoomStart) || !Number.isFinite(zoomEnd) || zoomEnd <= zoomStart) {
      ({ start: zoomStart, end: zoomEnd } = liveEdgeDataZoomForBars(candles.length, categoryData));
      pinnedToLiveRef.current = true;
    }
    zoomStart = Math.max(0, Math.min(100, zoomStart));
    zoomEnd = Math.max(0, Math.min(100, zoomEnd));
    prevConfigRef.current = { symbol: activeSymbol, timeframe: timeframe };

    // Heikin-Ashi / Renko render with transformed OHLC on the same category axis;
    // volume and indicators continue to use the raw candles below.
    const mainCandles = applyCandleTransform(candles, chartType, {
      renkoBrickSize: renkoBrickSizeRef.current,
    });
    const candlestickData = mainCandles.map(c => [c.open, c.close, c.low, c.high]);
    for (let i = 0; i < FUTURE_PADDING; i++) {
      candlestickData.push('-');
    }

    // ── Grid Configurations ──
    const showVol = active.volume;
    const showRsi = active.rsi;
    const showMacd = active.macd;
    const showAtr = active.atr;

    const subPanes = [];
    if (showVol) subPanes.push('volume');
    if (showRsi) subPanes.push('rsi');
    if (showMacd) subPanes.push('macd');
    if (showAtr) subPanes.push('atr');

    const totalSubPanes = subPanes.length;
    const subPaneHeightPct = 9;
    const gapPct = 3;
    const mainHeightPct = 83 - (totalSubPanes * (subPaneHeightPct + gapPct));

    const grids = [{
      left: '3%', right: '5%', top: '5%',
      height: `${mainHeightPct}%`
    }];

    let currentTop = 5 + mainHeightPct + gapPct;
    const paneGridMap = {};
    subPanes.forEach(pane => {
      grids.push({
        left: '3%', right: '5%',
        top: `${currentTop}%`,
        height: `${subPaneHeightPct}%`
      });
      paneGridMap[pane] = grids.length - 1;
      currentTop += subPaneHeightPct + gapPct;
    });

    // Axes
    const xAxes = [];
    const yAxes = [];
    
    // Main grid axis
    xAxes.push({
      id: 'x-0',
      ...categoryXAxisOpts(categoryData, 0, { showLabels: grids.length === 1, chartTheme }),
    });

    yAxes.push({
      id: 'price',
      scale: true,
      gridIndex: 0,
      position: 'right',
      splitLine: { show: true, lineStyle: { color: chartTheme.gridColor } },
      axisLine: { lineStyle: { color: chartTheme.axisLineColor } },
      axisLabel: { color: chartTheme.axisLabelColor, formatter: val => val.toFixed(dec) }
    });

    // Sub grids axes — track y-axis index separately from grid index (overlay axes append later).
    const gridCount = grids.length;
    const paneYAxisIndex = {};
    subPanes.forEach((pane) => {
      const gIdx = paneGridMap[pane];
      const isLowest = gIdx === gridCount - 1;

      xAxes.push({
        id: `x-${xAxes.length}`,
        ...categoryXAxisOpts(categoryData, gIdx, { showLabels: isLowest, chartTheme }),
        axisTick: { show: isLowest },
      });

      let yAxisOpt = {
        id: `y-${pane}`,
        scale: true,
        gridIndex: gIdx,
        position: 'right',
        splitLine: { show: true, lineStyle: { color: chartTheme.gridColor } },
        axisLine: { lineStyle: { color: chartTheme.axisLineColor } },
        axisLabel: { color: chartTheme.axisLabelColor, fontSize: 9 },
      };

      if (pane === 'volume') {
        yAxisOpt.axisLabel.formatter = val => formatVol(val);
      } else if (pane === 'rsi') {
        yAxisOpt.min = 0;
        yAxisOpt.max = 100;
        yAxisOpt.interval = 30;
      }
      paneYAxisIndex[pane] = yAxes.length;
      yAxes.push(yAxisOpt);
    });

    const showBacktestEquity = backtestOverlay?.visible
      && symbolsMatch(backtestOverlay.symbol, activeSymbol)
      && backtestOverlay.equityCurve?.length;

    const showCompare = Boolean(compareSymbol) && compareCandles.length > 1;

    const structureKey = `${chartStructureKey(chartType, subPanes, Boolean(showBacktestEquity))}|cmp:${showCompare ? 1 : 0}`;
    const fullReplace = structureKey !== prevStructureKeyRef.current;
    prevStructureKeyRef.current = structureKey;

    let backtestEquityYIdx = -1;
    if (showBacktestEquity) {
      backtestEquityYIdx = yAxes.length;
      yAxes.push({
        id: 'backtest-equity-axis',
        scale: true,
        gridIndex: 0,
        position: 'left',
        splitLine: { show: false },
        axisLine: { show: false },
        axisLabel: {
          color: '#60a5fa',
          fontSize: 9,
          formatter: (val) => (val >= 1000 ? `$${(val / 1000).toFixed(1)}k` : `$${Number(val).toFixed(0)}`),
        },
      });
    }

    // Comparison overlay axis (percent change, left side).
    let compareAxisIndex = -1;
    if (showCompare) {
      compareAxisIndex = yAxes.length;
      yAxes.push({
        id: 'compare-axis',
        scale: true,
        gridIndex: 0,
        position: 'left',
        offset: showBacktestEquity ? 38 : 0,
        splitLine: { show: false },
        axisLine: { show: true, lineStyle: { color: '#f472b6' } },
        axisLabel: {
          color: '#f472b6',
          fontSize: 9,
          formatter: (v) => `${v >= 0 ? '+' : ''}${Number(v).toFixed(1)}%`,
        },
      });
    }

    // Series
    const series = [];

    // Main Candlestick / Line Series
    if (chartType === 'line') {
      const lineData = mainCandles.map(c => c.close);
      for (let i = 0; i < FUTURE_PADDING; i++) lineData.push('-');
      series.push(withSeriesAnimOff({
        id: 'main',
        name: activeSymbol,
        type: 'line',
        data: lineData,
        xAxisIndex: 0,
        yAxisIndex: 0,
        showSymbol: false,
        lineStyle: { color: chartTheme.crosshairLabelBg, width: 2 },
      }));
    } else {
      series.push(withSeriesAnimOff({
        id: 'main',
        name: activeSymbol,
        type: 'candlestick',
        data: candlestickData,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: {
          color: chartTheme.bullishColor,
          color0: chartTheme.bearishColor,
          borderColor: chartTheme.bullishColor,
          borderColor0: chartTheme.bearishColor,
        },
      }));
    }

    // Comparison overlay — second symbol rebased to percent change.
    if (showCompare) {
      const pct = alignComparisonSeries(candles, compareCandles);
      const compareData = [...pct];
      for (let i = 0; i < FUTURE_PADDING; i++) compareData.push(null);
      series.push(withSeriesAnimOff({
        id: 'compare',
        name: `${compareSymbol} %`,
        type: 'line',
        data: compareData,
        xAxisIndex: 0,
        yAxisIndex: compareAxisIndex,
        showSymbol: false,
        connectNulls: true,
        lineStyle: { color: '#f472b6', width: 1.5, type: 'dashed' },
        z: 3,
      }));
    }

    // Signal markers — scatter layer shares the category x-axis (stable under zoom/pan)
    series.push(withSeriesAnimOff({
      id: 'signal-markers',
      name: 'Signals',
      type: 'scatter',
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: [],
      clip: true,
      z: 6,
      animation: false,
      tooltip: { show: false },
    }));

    const equityOverlayData = showBacktestEquity
      ? mapBacktestEquityLine(backtestOverlay.equityCurve, candles)
      : [];

    if (showBacktestEquity) {
      series.push(withSeriesAnimOff({
        id: 'backtest-equity',
        name: 'BT Equity',
        type: 'line',
        data: equityOverlayData,
        xAxisIndex: 0,
        yAxisIndex: backtestEquityYIdx,
        showSymbol: false,
        silent: true,
        z: 2,
        lineStyle: { color: '#60a5fa', width: 1.5, type: 'dashed', opacity: 0.85 },
      }));
    }

    // Overlay indicators
    if (active.ema9) {
      series.push(withSeriesAnimOff({
        id: 'ema9',
        name: 'EMA 9', type: 'line', data: mapEmaSeries(candles, 9), xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: emaLineStyle(9, indicatorTheme),
      }));
    }
    if (active.ema21) {
      series.push(withSeriesAnimOff({
        id: 'ema21',
        name: 'EMA 21', type: 'line', data: mapEmaSeries(candles, 21), xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: emaLineStyle(21, indicatorTheme),
      }));
    }
    if (active.ema50) {
      series.push(withSeriesAnimOff({
        id: 'ema50',
        name: 'EMA 50', type: 'line', data: mapEmaSeries(candles, 50), xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: emaLineStyle(50, indicatorTheme),
      }));
    }
    if (active.bb) {
      const bb = mapBbSeries(candles);
      const { bb: bbTheme } = indicatorTheme;
      series.push(
        withSeriesAnimOff({
          id: 'bb-upper', name: 'BB Upper', type: 'line', data: bb.upper, xAxisIndex: 0, yAxisIndex: 0,
          showSymbol: false,
          lineStyle: { color: bbTheme.outer, width: 1, type: 'dashed', opacity: bbTheme.outerOpacity },
        }),
        withSeriesAnimOff({
          id: 'bb-mid', name: 'BB Mid', type: 'line', data: bb.middle, xAxisIndex: 0, yAxisIndex: 0,
          showSymbol: false,
          lineStyle: { color: bbTheme.basis, width: 1, opacity: bbTheme.basisOpacity },
        }),
        withSeriesAnimOff({
          id: 'bb-lower', name: 'BB Lower', type: 'line', data: bb.lower, xAxisIndex: 0, yAxisIndex: 0,
          showSymbol: false,
          lineStyle: { color: bbTheme.outer, width: 1, type: 'dashed', opacity: bbTheme.outerOpacity },
        }),
      );
    }
    if (active.vwap) {
      series.push(withSeriesAnimOff({
        id: 'vwap',
        name: 'VWAP', type: 'line', data: mapVwapSeries(candles), xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false,
        connectNulls: false,
        lineStyle: {
          color: indicatorTheme.vwap.line,
          width: indicatorTheme.vwap.width,
          opacity: indicatorTheme.vwap.opacity,
        },
      }));
    }

    // Sub grids series
    if (showVol) {
      const gIdx = paneGridMap.volume;
      series.push(withSeriesAnimOff({
        id: 'volume',
        name: 'Volume',
        type: 'bar',
        xAxisIndex: gIdx,
        yAxisIndex: paneYAxisIndex.volume,
        barCategoryGap: '30%',
        data: buildVolumeSeriesData(candles, indicatorTheme),
      }));
    }

    if (showRsi) {
      const gIdx = paneGridMap.rsi;
      series.push(withSeriesAnimOff({
        id: 'rsi',
        name: 'RSI', type: 'line', data: mapRsiSeries(candles), xAxisIndex: gIdx, yAxisIndex: paneYAxisIndex.rsi,
        showSymbol: false,
        lineStyle: {
          color: indicatorTheme.rsi.line,
          width: indicatorTheme.rsi.width,
        },
        markLine: rsiMarkLine(indicatorTheme),
      }));
    }

    if (showMacd) {
      const gIdx = paneGridMap.macd;
      const macd = mapMacdSeries(candles, indicatorTheme);
      const { macd: macdTheme } = indicatorTheme;
      const macdYIdx = paneYAxisIndex.macd;
      series.push(
        withSeriesAnimOff({
          id: 'macd', name: 'MACD', type: 'line', data: macd.macd, xAxisIndex: gIdx, yAxisIndex: macdYIdx,
          showSymbol: false,
          lineStyle: { color: macdTheme.line, width: macdTheme.lineWidth },
          markLine: macdZeroMarkLine(indicatorTheme),
        }),
        withSeriesAnimOff({
          id: 'macd-signal', name: 'Signal', type: 'line', data: macd.signal, xAxisIndex: gIdx, yAxisIndex: macdYIdx,
          showSymbol: false,
          lineStyle: { color: macdTheme.signal, width: macdTheme.lineWidth },
        }),
        withSeriesAnimOff({
          id: 'macd-hist',
          name: 'Hist',
          type: 'bar',
          xAxisIndex: gIdx,
          yAxisIndex: macdYIdx,
          data: macd.hist,
        }),
      );
    }

    if (showAtr) {
      const gIdx = paneGridMap.atr;
      series.push(withSeriesAnimOff({
        id: 'atr',
        name: 'ATR', type: 'line', data: mapAtrSeries(candles), xAxisIndex: gIdx, yAxisIndex: paneYAxisIndex.atr,
        showSymbol: false,
        lineStyle: {
          color: indicatorTheme.atr.line,
          width: indicatorTheme.atr.width,
          opacity: indicatorTheme.atr.opacity,
        },
      }));
    }

    // Zoom and pan links
    const zoomXIndices = grids.map((_, i) => i);

    const option = {
      backgroundColor: chartTheme.backgroundColor,
      animation: false,
      animationDuration: 0,
      animationDurationUpdate: 0,
      axisPointer: {
        link: [{ xAxisIndex: 'all' }],
        label: { backgroundColor: chartTheme.crosshairLabelBg }
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        show: false // we use our own legend instead
      },
      grid: grids,
      xAxis: xAxes,
      yAxis: yAxes,
      dataZoom: [
        { id: 'dz-inside', type: 'inside', xAxisIndex: zoomXIndices, start: zoomStart, end: zoomEnd },
        { id: 'dz-slider', type: 'slider', xAxisIndex: zoomXIndices, start: zoomStart, end: zoomEnd, bottom: '3%', height: 18, borderColor: 'transparent', fillerColor: chartTheme.dataZoomFiller, textStyle: { color: chartTheme.axisLabelColor } },
      ],
      series: series
    };

    suppressDataZoomEventsRef.current += 1;
    let setOk = false;
    const chart = chartRef.current;
    if (fullReplace || layoutChanged) {
      overlayIdsRef.current = new Set();
      try { chart.clear(); } catch (_) {}
    }
    try {
      chart.setOption(option, { notMerge: true, lazyUpdate: false });
      setOk = true;
    } catch (err) {
      console.warn('[ChartWidget] configureChart setOption failed:', err);
      try {
        chart.clear();
        chart.setOption(option, { notMerge: true, lazyUpdate: false });
        setOk = true;
      } catch (retryErr) {
        console.warn('[ChartWidget] configureChart notMerge retry failed:', retryErr);
        chartReadyRef.current = false;
        setChartMountKey((k) => k + 1);
      }
    } finally {
      requestAnimationFrame(() => {
        suppressDataZoomEventsRef.current = Math.max(0, suppressDataZoomEventsRef.current - 1);
      });
      chartConfiguringRef.current = false;
    }

    if (!setOk) {
      chartReadyRef.current = false;
      return;
    }

    chartLayoutRef.current = { xAxisCount: xAxes.length, showVolume: showVol };
    chartReadyRef.current = true;
    updateLiveSeriesCache(
      liveSeriesCacheRef.current,
      candles,
      chartType,
      active,
      indicatorTheme,
      { forceRebuild: true },
    );

    // Initial legend display
    const lastBar = candles[candles.length - 1];
    updateLegendDOM(lastBar);

    requestAnimationFrame(() => {
      if (!chartReadyRef.current || isChartDisposed(chartRef.current)) return;
      try {
        safeChartResizeRef.current();
        applyOverlayPatchRef.current?.();
        renderChartGraphicsRef.current?.();
      } catch (err) {
        console.warn('[ChartWidget] post-configure overlay failed:', err);
      }
    });
  }, [aggregatedCandles, activeSymbol, timeframe, active, chartType, updateLegendDOM, chartTheme, indicatorTheme, backtestOverlay, backtestOverlayKey, chartHistoryReady, compareSymbol, compareCandles]);

  // Lightweight overlay patch — SL/TP lines and trade markers only
  const applyOverlayPatch = useCallback(() => {
    const chart = chartRef.current;
    const bars = candlesRef.current;
    if (isChartDisposed(chart) || !bars.length || !chartReadyRef.current || chartConfiguringRef.current) return;

    const cfg = TF_CONFIGS.find((t) => t.label === timeframe) || TF_CONFIGS[0];
    const bucketSecs = cfg.secs;
    const dec = getPriceDecimals(bars[bars.length - 1]?.close);
    const overlays = settings.chartLayout?.overlays ?? DEFAULT_TERMINAL_SETTINGS.chartLayout.overlays;
    const markLineData = [
      ...(overlays.positions !== false ? buildMarkLineData(symbolPosition, dec) : []),
      ...(overlays.agentLevels !== false
        ? buildAgentMarkLines(agentInsight, bars[bars.length - 1]?.close, dec)
        : []),
    ];
    const showBotMarkers = overlays.botMarkers !== false
      && selectedBotId
      && botDetail?.bot?.symbol === activeSymbol
      && botDetail?.trades?.length;
    const tradeHistory = useStore.getState().tradeHistory;
    const tradeMarkers = overlays.trades !== false
      ? buildTradeMarkers(
        tradeHistory,
        activeSymbol,
        bars,
        bucketSecs,
        { excludeBotId: showBotMarkers ? selectedBotId : null },
      )
      : [];
    const botMarkers = showBotMarkers
      ? buildBotTradeMarkers(botDetail.trades, bars, bucketSecs)
      : [];
    const showBacktestMarkers = backtestOverlay?.visible
      && symbolsMatch(backtestOverlay.symbol, activeSymbol)
      && backtestOverlay.trades?.length;
    const backtestMarkers = showBacktestMarkers
      ? buildBacktestTradeMarkers(backtestOverlay.trades, bars, bucketSecs)
      : [];
    const scatterData = [...tradeMarkers, ...botMarkers, ...backtestMarkers];

    try {
      chart.setOption({
        series: [
          {
            id: 'main',
            markLine: { symbol: ['none', 'none'], animation: false, data: markLineData },
            markPoint: { data: [] },
          },
          {
            id: 'signal-markers',
            data: scatterData,
          },
        ],
      }, { lazyUpdate: false });
    } catch (err) {
      console.warn('[ChartWidget] overlay patch failed:', err);
    }
  }, [activeSymbol, timeframe, symbolPosition, tradeOverlayKey, selectedBotId, botDetail, botOverlayKey, backtestOverlay, backtestOverlayKey, agentInsight, agentOverlayKey, settings.chartLayout?.overlays]);

  // Render the graphic overlay layer (Volume Profile + drawings) in pixel space.
  // Recomputed on configure, zoom/pan, resize, and when drawings/VPVR change.
  const renderChartGraphics = useCallback(() => {
    const chart = chartRef.current;
    if (isChartDisposed(chart) || !chartReadyRef.current || chartConfiguringRef.current) return;
    const bars = candlesRef.current;

    // Overlay elements are rendered as a FLAT list of ECharts `graphic` elements
    // (not a nested group): ECharts v6 throws internally (updateLeaveTo) when a
    // graphic *group* with children is diffed/removed, which silently breaks the
    // overlay. Each element carries a stable, type-consistent id so stale ones can
    // be removed by id when the set shrinks.
    const setGraphic = (children) => {
      if (isChartDisposed(chart)) return;
      try {
        const nextIds = new Set();
        for (const el of children) if (el && el.id != null) nextIds.add(el.id);
        const removals = [];
        for (const prevId of overlayIdsRef.current) {
          if (!nextIds.has(prevId)) removals.push({ id: prevId, $action: 'remove' });
        }
        chart.setOption({ graphic: { elements: [...children, ...removals] } });
        overlayIdsRef.current = nextIds;
      } catch (err) {
        console.warn('[ChartWidget] graphic overlay failed:', err);
      }
    };

    if (!bars || !bars.length) {
      setGraphic([]);
      return;
    }

    // Category axes interpret numeric convertToPixel input as the ordinal index,
    // so translate stored {time} → fractional bar index before conversion.
    const priceToY = (price) => {
      const px = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [0, price]);
      return px && Number.isFinite(px[1]) ? px[1] : null;
    };
    const convert = (pt) => {
      if (!pt) return null;
      const idx = timeToFractionalIndex(bars, toUnixSeconds(pt.time));
      if (idx == null) return null;
      const px = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [idx, pt.price]);
      return px && Number.isFinite(px[0]) && Number.isFinite(px[1]) ? px : null;
    };

    const width = chart.getWidth();
    const plotLeft = width * 0.03;
    const plotRight = width * 0.95;

    const children = [];

    if (showVolumeProfileRef.current) {
      const profile = computeVolumeProfile(bars, { bins: 24 });
      if (profile.bins.length >= 2) {
        const y0 = priceToY(profile.bins[0].mid);
        const y1 = priceToY(profile.bins[1].mid);
        const binPx = y0 != null && y1 != null ? Math.abs(y0 - y1) : 6;
        children.push(...volumeProfileGraphic(profile, {
          plotRight,
          maxWidthPx: width * 0.16,
          priceToY,
          binPx,
        }));
      }
    }

    children.push(...drawingsToGraphic(drawingsRef.current, convert, {
      left: plotLeft,
      right: plotRight,
      width,
      priceToY,
      selectedId: selectedDrawingIdRef.current,
    }));

    const pos = symbolPositionRef.current;
    const draft = chartSlTpDraftRef.current?.symbol === activeSymbol ? chartSlTpDraftRef.current : null;
    const dragPrices = slTpDragPricesRef.current;
    const barsDec = getPriceDecimals(bars[bars.length - 1]?.close);
    const hasLive = pos && Math.abs(pos.size) > 0;
    const live = hasLive ? {
      stop_loss_price: dragPrices.sl ?? pos.stop_loss_price,
      take_profit_price: dragPrices.tp ?? pos.take_profit_price,
    } : {};
    const showDraft = draft && (
      (draft.stop_loss_price != null && draft.stop_loss_price > 0)
      || (draft.take_profit_price != null && draft.take_profit_price > 0)
    );
    const draftLevels = showDraft ? {
      stop_loss_price: dragPrices.draftSl ?? draft.stop_loss_price,
      take_profit_price: dragPrices.draftTp ?? draft.take_profit_price,
    } : {};
    const { elements: slTpEls, hitLines } = buildSlTpGraphic({
      priceToY,
      plotLeft,
      plotRight,
      dec: barsDec,
      live,
      draft: draftLevels,
    });
    slTpHitLinesRef.current = hitLines;
    children.push(...slTpEls);

    setGraphic(children);
  }, [activeSymbol]);

  const renderChartGraphicsRef = useRef(renderChartGraphics);
  renderChartGraphicsRef.current = renderChartGraphics;

  configureChartRef.current = configureChart;
  applyOverlayPatchRef.current = applyOverlayPatch;

  const loadOlderHistory = useCallback(async () => {
    if (loadingOlderRef.current || olderExhaustedRef.current[activeSymbol]) return;

    const cfg = TF_CONFIGS.find((t) => t.label === timeframe) || TF_CONFIGS[0];
    const barSecs = cfg.secs <= 60 ? 60 : cfg.secs;
    const oldest = getOldestBarTime(activeSymbol, useNativeHt ? timeframe : '1m', barSecs);
    if (oldest == null) return;

    const nowSec = Math.floor(Date.now() / 1000);
    let interval = cfg.secs >= 3600 ? '1h' : '1m';
    if (oldest < nowSec - ARCHIVE_1M_RETENTION_SEC) {
      interval = 'auto';
    }
    const chunk = interval === 'auto' || interval === '1h' ? ARCHIVE_LOAD_CHUNK : Math.min(ARCHIVE_LOAD_CHUNK, 500);
    const from = oldest - chunk * barSecs;
    const to = oldest - barSecs;

    loadingOlderRef.current = true;
    try {
      const added = await fetchOlderCandles(activeSymbol, from, to, interval);
      if (added <= 0) {
        olderExhaustedRef.current[activeSymbol] = true;
      } else {
        setDisplayBarLimit((prev) => Math.min(prev + added, CHART_DISPLAY_MAX));
      }
    } catch (err) {
      console.warn('[ChartWidget] load older history failed:', err);
    } finally {
      loadingOlderRef.current = false;
    }
  }, [activeSymbol, timeframe, useNativeHt]);

  loadOlderRef.current = loadOlderHistory;

  // Init ECharts once the container has non-zero layout (avoids zero-size init warning).
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    let chart = null;
    let disposed = false;

    const mountChart = () => {
      if (disposed || chart) return false;
      const { clientWidth, clientHeight } = el;
      if (clientWidth < 2 || clientHeight < 2) return false;

      chart = echarts.init(el, chartTheme.echartsTheme || undefined);
      chartRef.current = chart;
      chartReadyRef.current = false;

      chart.on('updateAxisPointer', (event) => {
        const axesInfo = event.axesInfo;
        const candles = candlesRef.current;
        if (axesInfo && axesInfo[0] && candles?.length) {
          const info = axesInfo[0];
          let idx = info.value;
          if (typeof idx !== 'number' || idx >= candles.length || idx < 0) {
            idx = candles.findIndex((c) => toUnixSeconds(c.time) === toUnixSeconds(info.value));
          }
          if (idx >= 0 && idx < candles.length && candles[idx]) {
            updateLegendDOM(candles[idx]);
            return;
          }
        }
        if (candles?.length) {
          updateLegendDOM(candles[candles.length - 1]);
        }
      });

      chart.on('datazoom', (ev) => {
        if (!chartReadyRef.current || suppressDataZoomEventsRef.current > 0) return;

        const batch = ev.batch?.[0] ?? ev;
        const bars = displayBarsRef.current;
        if (bars.length) {
          const categoryLen = bars.length + FUTURE_PADDING;
          if (typeof batch.end === 'number') {
            const endIdx = Math.round((batch.end / 100) * categoryLen);
            pinnedToLiveRef.current = endIdx >= bars.length - 1;
          }
        }

        // Reposition the graphic overlay (VPVR + drawings) on zoom/pan.
        renderChartGraphicsRef.current?.();

        const now = Date.now();
        if (now - dataZoomHandlerLastMsRef.current < DATAZOOM_HANDLER_MIN_MS) return;
        dataZoomHandlerLastMsRef.current = now;

        if (typeof batch.start === 'number' && batch.start <= 2) {
          if (loadingOlderRef.current) return;
          if (now - loadOlderLastMsRef.current < LOAD_OLDER_MIN_INTERVAL_MS) return;
          loadOlderLastMsRef.current = now;
          loadOlderRef.current?.();
        }
      });

      const handleChartClick = (params) => {
        const pointInPixel = [params.offsetX, params.offsetY];
        const inGrid = chart.containPixel({ gridIndex: 0 }, pointInPixel);

        // 1) SL/TP edit mode (existing behavior).
        const mode = useStore.getState().chartInteractionMode;
        if (mode !== 'normal') {
          if (inGrid) {
            const pointInValue = chart.convertFromPixel({ gridIndex: 0 }, pointInPixel);
            const price = pointInValue[1];
            if (price !== null && price > 0) {
              window.dispatchEvent(new CustomEvent('chart-click', { detail: price }));
            }
          }
          return;
        }

        const tool = activeToolRef.current;
        const bars = candlesRef.current;
        if (!inGrid) return;

        // 2) Drawing creation.
        if (tool) {
          const val = chart.convertFromPixel({ xAxisIndex: 0, yAxisIndex: 0 }, pointInPixel);
          const price = val?.[1];
          if (price == null || !Number.isFinite(price)) return;
          const idx = Math.round(val[0]);
          const bar = bars[idx] ?? bars[bars.length - 1];
          const time = bar ? toUnixSeconds(bar.time) : null;
          if (time == null) return;

          if (tool === 'hline') {
            addDrawingRef.current(createDrawing('hline', [{ price }]));
            setActiveTool(null);
            return;
          }
          if (!pendingDrawPointRef.current) {
            pendingDrawPointRef.current = { time, price };
          } else {
            const p1 = pendingDrawPointRef.current;
            pendingDrawPointRef.current = null;
            addDrawingRef.current(createDrawing(tool, [p1, { time, price }]));
            setActiveTool(null);
          }
          return;
        }

        // 3) Selection (when no tool active): hit-test existing drawings.
        const priceToY = (p) => {
          const px = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [0, p]);
          return px && Number.isFinite(px[1]) ? px[1] : null;
        };
        const convert = (pt) => {
          const idx = timeToFractionalIndex(bars, toUnixSeconds(pt.time));
          if (idx == null) return null;
          const px = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [idx, pt.price]);
          return px && Number.isFinite(px[0]) ? px : null;
        };
        const hit = hitTestDrawings(
          params.offsetX, params.offsetY, drawingsRef.current, convert, { priceToY },
        );
        setSelectedDrawingId(hit);
      };

      // zrender's synthetic 'click' is suppressed on a live-updating chart (the
      // hovered element changes between mousedown and mouseup), so detect clicks
      // ourselves from a mousedown/mouseup pair with negligible movement. This
      // also distinguishes a click from a pan-drag (dataZoom inside).
      let downPt = null;
      chart.getZr().on('mousedown', (p) => {
        downPt = { x: p.offsetX, y: p.offsetY, t: Date.now() };
        const modeNow = useStore.getState().chartInteractionMode;
        const toolNow = activeToolRef.current;
        if (!toolNow && modeNow === 'normal' && chart.containPixel({ gridIndex: 0 }, [p.offsetX, p.offsetY])) {
          const slTpHit = hitTestSlTp(p.offsetY, slTpHitLinesRef.current);
          if (slTpHit) {
            slTpDragRef.current = { target: slTpHit, startY: p.offsetY };
            slTpDragPricesRef.current = {};
            setSlTpDraggingRef.current(true);
            p.stop?.();
          }
        }
      });
      chart.getZr().on('mouseup', (p) => {
        const wasSlTpDrag = !!slTpDragRef.current;
        const start = downPt;
        downPt = null;
        if (wasSlTpDrag) return;
        if (!start) return;
        const moved = Math.hypot(p.offsetX - start.x, p.offsetY - start.y);
        if (moved <= 5 && Date.now() - start.t < 700) handleChartClick(p);
      });

      if (import.meta.env.DEV) {
        el.__chartInstance = chart;
      }

      requestAnimationFrame(() => {
        if (chartHistoryReadyRef.current) {
          lastConfigureRevisionRef.current = '';
          configureChartRef.current();
        }
      });
      return true;
    };

    const ro = new ResizeObserver(() => {
      if (chart) {
        if (safeChartResizeRef.current()) {
          renderChartGraphicsRef.current?.();
        }
        return;
      }
      mountChart();
    });
    ro.observe(el);
    mountChart();

    return () => {
      disposed = true;
      ro.disconnect();
      if (configureDebounceRef.current) {
        clearTimeout(configureDebounceRef.current);
        configureDebounceRef.current = null;
      }
      chartConfiguringRef.current = false;
      lastConfigureRevisionRef.current = '';
      chart?.dispose();
      chartRef.current = null;
      chartReadyRef.current = false;
      if (el.__chartInstance) delete el.__chartInstance;
    };
  }, [updateLegendDOM, chartTheme.echartsTheme, resolvedTheme, chartMountKey]);

  // Full rebuild when structure/history/indicators change (debounced — coalesces timeframe toggles)
  useEffect(() => {
    if (configureDebounceRef.current) clearTimeout(configureDebounceRef.current);
    configureDebounceRef.current = setTimeout(() => {
      configureDebounceRef.current = null;
      if (configureRevision === lastConfigureRevisionRef.current) return;
      lastConfigureRevisionRef.current = configureRevision;
      configureChartRef.current();
    }, CONFIGURE_DEBOUNCE_MS);
    return () => {
      if (configureDebounceRef.current) {
        clearTimeout(configureDebounceRef.current);
        configureDebounceRef.current = null;
      }
    };
  }, [configureRevision]);

  // Lightweight overlay patch — trades, positions, and after full rebuild
  useEffect(() => {
    if (!chartReadyRef.current) return;
    applyOverlayPatchRef.current?.();
  }, [positionOverlayKey, tradeOverlayKey, botOverlayKey, backtestOverlayKey]);

  // Re-render the graphic overlay (Volume Profile + drawings) on state changes.
  useEffect(() => {
    if (!chartReadyRef.current) return;
    renderChartGraphicsRef.current?.();
  }, [drawings, selectedDrawingId, showVolumeProfile, chartSlTpDraft, symbolPosition, slTpOverlayTick, activeSymbol]);

  // Persist the Volume Profile toggle to settings.
  useEffect(() => {
    updateChartLayout({ volumeProfile: showVolumeProfile });
  }, [showVolumeProfile, updateChartLayout]);

  // Replay playback timer — advances the cursor one bar at a time.
  useEffect(() => {
    if (!replayActive || !replayPlaying) return undefined;
    const total = aggregatedCandles.length;
    const interval = Math.max(80, 700 / replaySpeed);
    const id = setInterval(() => {
      setReplayIndex((i) => {
        const ni = i + 1;
        if (ni >= total) {
          setReplayPlaying(false);
          return total;
        }
        return ni;
      });
    }, interval);
    return () => clearInterval(id);
  }, [replayActive, replayPlaying, replaySpeed, aggregatedCandles.length]);

  const enterReplay = useCallback(() => {
    const total = aggregatedCandles.length;
    if (total < 5) return;
    setReplayActive(true);
    setReplayPlaying(false);
    setReplayIndex(Math.max(2, Math.floor(total / 2)));
  }, [aggregatedCandles.length]);

  const exitReplay = useCallback(() => {
    setReplayActive(false);
    setReplayPlaying(false);
  }, []);

  // Leaving the symbol/timeframe abandons any active replay (data changed).
  useEffect(() => {
    setReplayActive(false);
    setReplayPlaying(false);
    setCompareSymbol((cur) => (cur === activeSymbol ? null : cur));
  }, [activeSymbol, timeframe]);

  useEffect(() => {
    const onOverlayChanged = () => applyOverlayPatchRef.current?.();
    window.addEventListener(BACKTEST_OVERLAY_EVENT, onOverlayChanged);
    return () => window.removeEventListener(BACKTEST_OVERLAY_EVENT, onOverlayChanged);
  }, []);

  useEffect(() => {
    const onFocusBar = (e) => {
      const { time, symbol: sym } = e.detail ?? {};
      if (time == null || !symbolsMatch(sym, activeSymbol)) return;
      const chart = chartRef.current;
      if (!chart || !chartReadyRef.current) return;
      const bars = displayBarsRef.current;
      if (!bars.length) return;
      const target = toUnixSeconds(time);
      let idx = bars.findIndex((b) => toUnixSeconds(b.time) === target);
      if (idx < 0) {
        idx = bars.findIndex((b) => Math.abs(toUnixSeconds(b.time) - target) < 120);
      }
      if (idx < 0) return;
      const total = buildCategoryAxisData(bars).length;
      const catIdx = FUTURE_PADDING + idx;
      const half = 25;
      const start = Math.max(0, ((catIdx - half) / total) * 100);
      const end = Math.min(100, ((catIdx + half) / total) * 100);
      try {
        suppressDataZoomEventsRef.current += 1;
        const { xAxisCount } = chartLayoutRef.current;
        const zoomXIndices = Array.from({ length: Math.max(1, xAxisCount) }, (_, i) => i);
        chart.setOption({
          dataZoom: buildDataZoomOption(start, end, zoomXIndices),
        });
        requestAnimationFrame(() => {
          suppressDataZoomEventsRef.current = Math.max(0, suppressDataZoomEventsRef.current - 1);
        });
        pinnedToLiveRef.current = false;
      } catch (err) {
        console.warn('[ChartWidget] backtest focus zoom failed:', err);
      }
    };
    window.addEventListener('backtest-focus-bar', onFocusBar);
    return () => window.removeEventListener('backtest-focus-bar', onFocusBar);
  }, [activeSymbol]);

  const applyLiveCandleUpdate = useCallback(() => {
    const chart = chartRef.current;
    if (isChartDisposed(chart) || !chartReadyRef.current || chartConfiguringRef.current) return;
    // Replay mode freezes live updates so the cursor controls what's visible.
    if (replayActiveRef.current) return;

    const cfg = TF_CONFIGS.find(t => t.label === timeframe) || TF_CONFIGS[0];
    let aggregatedLive;

    if (useNativeHt) {
      if (!patchHtFormingBar(activeSymbol, timeframe, cfg.secs)) {
        const px = useStore.getState().tickerData[activeSymbol]?.price;
        if (px != null) {
          patchHtFormingBarFromPrice(activeSymbol, timeframe, cfg.secs, px);
        }
      }
      const htSeries = getCandles(activeSymbol, timeframe, cfg.secs);
      aggregatedLive = htSeries[htSeries.length - 1];
      if (!aggregatedLive) return;
    } else {
      const raw = getCandles(activeSymbol, '1m', 60);
      if (!raw.length) return;
      aggregatedLive = timeframe === '1m'
        ? raw[raw.length - 1]
        : aggregateBucket(raw, cfg);
      if (!aggregatedLive) return;
    }

    const bars = displayBarsRef.current;
    if (!bars.length) return;

    const last = bars[bars.length - 1];
    let isNewBar = false;
    if (last && last.time === aggregatedLive.time) {
      bars[bars.length - 1] = aggregatedLive;
    } else if (!last || aggregatedLive.time > last.time) {
      isNewBar = true;
      bars.push({ ...aggregatedLive });
      if (bars.length > displayBarLimit) {
        bars.shift();
      }
    } else {
      return;
    }

    candlesRef.current = bars;
    const cache = liveSeriesCacheRef.current;

    // Heikin-Ashi / Renko depend on prior bars, so incremental OHLC patching
    // would desync the forming bar. Rebuild the full option for correctness.
    if (chartTypeRef.current === 'heikin' || chartTypeRef.current === 'renko') {
      configureChartRef.current();
      updateLegendDOM(aggregatedLive);
      return;
    }

    try {
      const patch = {};

      if (isNewBar) {
        const categoryData = buildCategoryAxisData(bars);
        const { xAxisCount } = chartLayoutRef.current;
        patch.xAxis = Array.from({ length: xAxisCount }, (_, i) => ({
          id: `x-${i}`,
          gridIndex: i,
          data: categoryData,
        }));
        patch.series = buildNewBarSeriesPatches(bars, chartType, active, indicatorTheme, cache);
        if (pinnedToLiveRef.current) {
          const { start, end } = liveEdgeDataZoomForBars(bars.length, categoryData);
          const zoomXIndices = Array.from({ length: Math.max(1, xAxisCount) }, (_, i) => i);
          patch.dataZoom = buildDataZoomOption(start, end, zoomXIndices);
          suppressDataZoomEventsRef.current += 1;
        }
      } else {
        updateLiveSeriesCache(cache, bars, chartType, active, indicatorTheme);
        patch.series = buildLightLiveSeriesPatchesFromCache(cache, chartType, active);
      }

      // Merge by series id only — never replaceMerge (drops indicator series not in patch)
      chart.setOption(patch, { lazyUpdate: true });
      if (isNewBar && suppressDataZoomEventsRef.current > 0) {
        requestAnimationFrame(() => {
          suppressDataZoomEventsRef.current = Math.max(0, suppressDataZoomEventsRef.current - 1);
        });
      }
      updateLegendDOM(aggregatedLive);
      if (isNewBar) {
        applyOverlayPatchRef.current?.();
      }
    } catch (err) {
      console.warn('[ChartWidget] live candle update failed:', err);
    }
  }, [activeSymbol, timeframe, chartType, updateLegendDOM, active, displayBarLimit, indicatorTheme, terminalMode, useNativeHt]);

  const pumpLiveCandleUpdate = useCallback(() => {
    const now = performance.now();
    if (now - liveLastPaintMs.current < LIVE_MIN_INTERVAL_MS) {
      if (liveRafRef.current == null) {
        liveRafRef.current = requestAnimationFrame(() => {
          liveRafRef.current = null;
          pumpLiveCandleUpdate();
        });
      }
      return;
    }
    liveLastPaintMs.current = now;
    applyLiveCandleUpdate();
  }, [applyLiveCandleUpdate]);

  // Immediate forming-bar paint on every WS price (Massive) — does not wait for React ticker flush.
  useEffect(() => {
    const symbol = activeSymbol;
    return onLivePrice((sym, _price) => {
      if (sym !== symbol) return;
      if (liveRafRef.current != null) return;
      liveRafRef.current = requestAnimationFrame(() => {
        liveRafRef.current = null;
        pumpLiveCandleUpdate();
      });
    });
  }, [activeSymbol, pumpLiveCandleUpdate]);

  useEffect(() => {
    const symbol = activeSymbol;
    const bufKey = chartBufKey;
    const unsubscribe = subscribeLiveRevisions(symbol, bufKey, () => {
      if (liveRafRef.current != null) return;
      liveRafRef.current = requestAnimationFrame(() => {
        liveRafRef.current = null;
        pumpLiveCandleUpdate();
      });
    });
    return () => {
      unsubscribe();
      if (liveRafRef.current != null) {
        cancelAnimationFrame(liveRafRef.current);
        liveRafRef.current = null;
      }
    };
  }, [activeSymbol, chartBufKey, pumpLiveCandleUpdate]);

  // Ticker state flush (~60 Hz batched) — keeps header/watchlist in sync.
  useEffect(() => {
    const symbol = activeSymbol;
    let lastPrice;
    return useStore.subscribe(
      (state) => state.tickerData[symbol]?.price,
      (price) => {
        if (price == null || price === lastPrice) return;
        lastPrice = price;
        if (liveRafRef.current != null) return;
        liveRafRef.current = requestAnimationFrame(() => {
          liveRafRef.current = null;
          pumpLiveCandleUpdate();
        });
      },
    );
  }, [activeSymbol, pumpLiveCandleUpdate]);

  // Handle ESC key to cancel interaction mode
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape' && chartInteractionMode !== 'normal') {
        setChartInteractionMode('normal');
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [chartInteractionMode, setChartInteractionMode]);

  // Drawing-tool keyboard shortcuts: ESC cancels, Delete/Backspace removes selection.
  useEffect(() => {
    const onKey = (e) => {
      const tag = document.activeElement?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (e.key === 'Escape') {
        if (pendingDrawPointRef.current || activeToolRef.current) {
          pendingDrawPointRef.current = null;
          setActiveTool(null);
        } else if (selectedDrawingIdRef.current) {
          setSelectedDrawingId(null);
        }
      } else if ((e.key === 'Delete' || e.key === 'Backspace') && selectedDrawingIdRef.current) {
        removeDrawing(selectedDrawingIdRef.current);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setActiveTool, setSelectedDrawingId, removeDrawing]);

  // Handle Chart Click for SL/TP
  useEffect(() => {
    const handleChartClick = (e) => {
      if (chartInteractionMode === 'normal') return;
      const price = e.detail;
      
      if (chartInteractionMode === 'edit_sl') {
        import('../api/transport').then(({ sendAction }) => {
          sendAction(Action.UPDATE_POSITION_SL_TP, { symbol: activeSymbol, stop_loss_price: price });
        });
      } else if (chartInteractionMode === 'edit_tp') {
        import('../api/transport').then(({ sendAction }) => {
          sendAction(Action.UPDATE_POSITION_SL_TP, { symbol: activeSymbol, take_profit_price: price });
        });
      }
      
      setChartInteractionMode('normal');
    };
    
    window.addEventListener('chart-click', handleChartClick);
    return () => window.removeEventListener('chart-click', handleChartClick);
  }, [chartInteractionMode, activeSymbol, setChartInteractionMode]);

  useEffect(() => {
    clearChartSlTpDraft();
    slTpDragRef.current = null;
    slTpDragPricesRef.current = {};
    setSlTpDragging(false);
  }, [activeSymbol, clearChartSlTpDraft]);

  useChartSlTpDrag({
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
  });

  const hasSlTpOverlay = useMemo(() => {
    const pos = symbolPosition;
    const draft = chartSlTpDraft?.symbol === activeSymbol ? chartSlTpDraft : null;
    const liveSlTp = pos && Math.abs(pos.size) > 0 && (
      (pos.stop_loss_price != null && pos.stop_loss_price > 0)
      || (pos.take_profit_price != null && pos.take_profit_price > 0)
    );
    const draftSlTp = draft && (
      (draft.stop_loss_price != null && draft.stop_loss_price > 0)
      || (draft.take_profit_price != null && draft.take_profit_price > 0)
    );
    return !!(liveSlTp || draftSlTp);
  }, [symbolPosition, chartSlTpDraft, activeSymbol]);

  const chartToolbar = (
    <ChartToolbar
      timeframe={timeframe}
      onTimeframeChange={setTimeframe}
      chartType={chartType}
      onChartTypeChange={setChartType}
      activeTool={activeTool}
      onActiveToolChange={(v) => { pendingDrawPointRef.current = null; setActiveTool(v); }}
      showVolumeProfile={showVolumeProfile}
      onToggleVolumeProfile={() => setShowVolumeProfile((v) => !v)}
      replayActive={replayActive}
      onToggleReplay={() => (replayActive ? exitReplay() : enterReplay())}
      compareOptions={compareOptions}
      compareSymbol={compareSymbol}
      onCompareSymbolChange={setCompareSymbol}
      drawings={drawings}
      selectedDrawingId={selectedDrawingId}
      onRemoveDrawing={removeDrawing}
      onClearDrawings={clearDrawings}
      chartInteractionMode={chartInteractionMode}
      onCancelInteraction={() => setChartInteractionMode('normal')}
      hasSlTpOverlay={hasSlTpOverlay}
      activeIndicatorKeys={activeIndicatorKeys}
      onIndicatorsChange={handleIndicatorsChange}
      indicatorToolbar={indicatorToolbar}
    />
  );

  return (
    <WidgetShell
      className={cn(chartInteractionMode !== 'normal' && 'chart-interactive-mode relative')}
      data-tour="chart"
      icon={AreaChart}
      title={<ChartSymbolSwitcher />}
      headerRight={
        <div className="relative z-20 flex min-w-0 items-center gap-[var(--icon-gap-loose)]">
          <ChartHeaderPrice symbol={activeSymbol} />
          <ChartAnalystBadge symbol={activeSymbol} timeframe={timeframe} onDeployAgent={handleDeployChartAgent} />
          <Button
            variant={zenMode ? 'secondary' : 'ghost'}
            size="icon-sm"
            className="shrink-0"
            onPointerDown={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              window.dispatchEvent(new CustomEvent('chart-zen-toggle'));
            }}
            title={zenMode ? 'Restore layout (F)' : 'Maximize chart (F)'}
          >
            {zenMode ? <Minimize2 size={14} aria-hidden /> : <Maximize2 size={14} aria-hidden />}
            <span className="sr-only">{zenMode ? 'Restore layout' : 'Maximize chart'}</span>
          </Button>
        </div>
      }
      toolbar={chartToolbar}
      contentClassName="relative flex min-h-0 flex-1 flex-col overflow-hidden p-0"
    >
      {chartInteractionMode !== 'normal' && (
        <Badge className="icon-label pointer-events-none absolute top-2 left-1/2 z-[100] -translate-x-1/2 border-primary/40 bg-primary/90 px-3 py-1 text-[0.68rem] font-bold text-primary-foreground shadow-[0_0_15px_var(--color-accent-bg)]">
          Click chart to set {chartInteractionMode === 'edit_sl' ? 'Stop Loss' : 'Take Profit'}
          <span className="font-normal opacity-80">(ESC to cancel)</span>
        </Badge>
      )}

      {activeTool && (
        <Badge className="icon-label pointer-events-none absolute top-2 left-1/2 z-[100] -translate-x-1/2 border-primary/40 bg-primary/90 px-3 py-1 text-[0.68rem] font-bold text-primary-foreground shadow-[0_0_15px_var(--color-accent-bg)]">
          {activeTool === 'hline'
            ? 'Click to place a horizontal level'
            : `Click ${pendingDrawPointRef.current ? 'end' : 'start'} point for ${activeTool}`}
          <span className="font-normal opacity-80">(ESC to cancel)</span>
        </Badge>
      )}

      {replayActive && (
        <div className="absolute bottom-3 left-1/2 z-[100] flex -translate-x-1/2 items-center gap-1 rounded-md border border-border/60 bg-background/95 px-2 py-1 shadow-lg backdrop-blur">
          <Button variant="ghost" size="icon-sm" title="Restart" onClick={() => { setReplayPlaying(false); setReplayIndex(2); }}>
            <RotateCcw size={13} />
          </Button>
          <Button variant="ghost" size="icon-sm" title="Step back" onClick={() => { setReplayPlaying(false); setReplayIndex((i) => Math.max(2, i - 1)); }}>
            <SkipBack size={13} />
          </Button>
          <Button variant="ghost" size="icon-sm" title={replayPlaying ? 'Pause' : 'Play'} onClick={() => setReplayPlaying((p) => !p)}>
            {replayPlaying ? <Pause size={13} /> : <Play size={13} />}
          </Button>
          <Button variant="ghost" size="icon-sm" title="Step forward" onClick={() => { setReplayPlaying(false); setReplayIndex((i) => Math.min(aggregatedCandles.length, i + 1)); }}>
            <SkipForward size={13} />
          </Button>
          <span className="px-1 font-mono text-[10px] text-muted-foreground tabular-nums">
            {Math.min(replayIndex, aggregatedCandles.length)}/{aggregatedCandles.length}
          </span>
          <ToggleGroup type="single" value={String(replaySpeed)} onValueChange={(v) => v && setReplaySpeed(Number(v))} spacing={0}>
            {[1, 2, 4].map((s) => (
              <ToggleGroupItem key={s} value={String(s)} size="sm" className="px-1.5 text-[0.6rem] font-bold">
                {s}x
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          <Button variant="ghost" size="icon-sm" title="Exit replay" onClick={exitReplay}>
            <X size={13} />
          </Button>
        </div>
      )}

      <div className="relative min-h-0 flex-1 overflow-hidden">
        <div className="pointer-events-none absolute top-1.5 left-2.5 z-10 flex select-none items-center gap-[var(--icon-gap-loose)] font-mono text-[11px]">
          {[
            ['O', 'o'],
            ['H', 'h'],
            ['L', 'l'],
            ['C', 'c'],
          ].map(([label, id]) => (
            <span key={label} className="icon-label-tight">
              <span className="font-normal text-muted-foreground">{label}</span>
              <span id={`chart-legend-${id}`} className="font-bold">—</span>
            </span>
          ))}
          <span className="icon-label-tight">
            <span className="font-normal text-muted-foreground">V</span>
            <span id="chart-legend-v" className="font-bold text-trading-accent">—</span>
          </span>
          <span id="chart-legend-pct" className="text-[10px] font-bold opacity-90">—</span>
        </div>

        <div
          ref={containerRef}
          className={cn('h-full w-full', slTpDragging && 'cursor-ns-resize')}
          data-chart-root="main"
        />
        {!chartHistoryReady && aggregatedCandles.length === 0 && (
          <div className="pointer-events-none absolute inset-0 z-[5] flex items-center justify-center bg-background/40">
            <span className="text-xs text-muted-foreground">
              {terminalMode === 'LIVE_MASSIVE'
                ? 'Loading Massive chart history…'
                : 'Loading chart history…'}
            </span>
          </div>
        )}
        {useNativeHt && !nativeHtLoaded && aggregatedCandles.length > 0 && (
          <div className="pointer-events-none absolute right-2 top-2 z-[5] rounded bg-background/70 px-2 py-0.5 text-[10px] text-muted-foreground">
            Syncing {timeframe}…
          </div>
        )}
      </div>
    </WidgetShell>
  );
}
