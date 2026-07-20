/**
 * Backtest period candlestick chart with entry/exit trade markers (matches PDF export).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts';
import { initEcharts } from '@/lib/echartsInit';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  BACKTEST_MARKER_COLORS,
  buildTradeMarkerPoints,
  resolveBacktestPdfCandles,
} from '@/lib/backtestPdfChart';
import { toUnixSeconds } from '../services/candleBuffer';

function BacktestChartLegend({ className }) {
  return (
    <div className={cn('backtest-chart-legend', className)} aria-hidden>
      <span className="backtest-chart-legend__item">
        <i className="backtest-chart-legend__swatch backtest-chart-legend__swatch--entry" />
        Entry
      </span>
      <span className="backtest-chart-legend__item">
        <i className="backtest-chart-legend__swatch backtest-chart-legend__swatch--exit-win" />
        Exit (win)
      </span>
      <span className="backtest-chart-legend__item">
        <i className="backtest-chart-legend__swatch backtest-chart-legend__swatch--exit-loss" />
        Exit (loss)
      </span>
    </div>
  );
}

export default function BacktestPriceChart({
  symbol,
  meta,
  timeframe = '1m',
  trades = [],
  tradesLoading = false,
  tradesTotal = null,
  highlightBarTime = null,
  className,
  title,
}) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const [candles, setCandles] = useState([]);
  const [bucketSecs, setBucketSecs] = useState(60);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);

  useEffect(() => {
    if (!symbol || !meta?.oldest) {
      setCandles([]);
      setLoadError(null);
      return undefined;
    }

    let cancelled = false;
    setLoading(true);
    setLoadError(null);

    (async () => {
      try {
        const resolved = await resolveBacktestPdfCandles(symbol, meta, timeframe);
        if (cancelled) return;
        setCandles(resolved.candles);
        setBucketSecs(resolved.bucketSecs);
        if (!resolved.candles.length) {
          setLoadError('No candle history for this backtest window');
        }
      } catch (err) {
        if (!cancelled) {
          setCandles([]);
          setLoadError(err?.message || 'Could not load chart candles');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [symbol, meta?.oldest, meta?.newest, meta?.count, timeframe]);

  const markersPending = tradesLoading
    && tradesTotal != null
    && tradesTotal > (trades?.length ?? 0);

  const markerTrades = markersPending ? [] : trades;

  const markerPoints = useMemo(
    () => buildTradeMarkerPoints(candles, markerTrades, bucketSecs),
    [candles, markerTrades, bucketSecs],
  );

  const markerCounts = useMemo(() => {
    let entries = 0;
    let exits = 0;
    for (const m of markerPoints) {
      if (m.isExit) exits += 1;
      else entries += 1;
    }
    return { entries, exits };
  }, [markerPoints]);

  const highlightIndex = useMemo(() => {
    if (!highlightBarTime || !candles.length) return -1;
    const target = Number(highlightBarTime);
    let best = -1;
    let bestDiff = Infinity;
    candles.forEach((c, idx) => {
      const sec = toUnixSeconds(c.time);
      if (sec == null) return;
      const diff = Math.abs(sec - target);
      if (diff < bestDiff) {
        bestDiff = diff;
        best = idx;
      }
    });
    return best;
  }, [candles, highlightBarTime]);

  const scatterSeries = useMemo(() => {
    const entries = [];
    const exitWins = [];
    const exitLosses = [];

    for (const m of markerPoints) {
      const point = [m.idx, m.yPrice];
      if (m.isExit) {
        if ((m.pnl ?? 0) >= 0) exitWins.push(point);
        else exitLosses.push(point);
      } else {
        entries.push({ point, side: m.side });
      }
    }

    const entrySeries = entries.length ? [{
      name: 'Entry',
      type: 'scatter',
      data: entries.map((e) => ({
        value: e.point,
        symbolRotate: e.side === 'SELL' ? 180 : 0,
      })),
      symbol: 'triangle',
      symbolSize: 9,
      itemStyle: { color: BACKTEST_MARKER_COLORS.entry },
      z: 4,
    }] : [];

    const exitWinSeries = exitWins.length ? [{
      name: 'Exit (win)',
      type: 'scatter',
      data: exitWins,
      symbol: 'pin',
      symbolSize: 11,
      itemStyle: { color: BACKTEST_MARKER_COLORS.exitWin },
      z: 4,
    }] : [];

    const exitLossSeries = exitLosses.length ? [{
      name: 'Exit (loss)',
      type: 'scatter',
      data: exitLosses,
      symbol: 'pin',
      symbolSize: 11,
      itemStyle: { color: BACKTEST_MARKER_COLORS.exitLoss },
      z: 4,
    }] : [];

    return [...entrySeries, ...exitWinSeries, ...exitLossSeries];
  }, [markerPoints]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;

    let chart = null;
    let disposed = false;

    const mountChart = () => {
      if (disposed || chart) return false;
      const { clientWidth, clientHeight } = el;
      if (clientWidth < 2 || clientHeight < 2) return false;
      chart = initEcharts(el, 'dark');
      chartRef.current = chart;
      return true;
    };

    const ro = new ResizeObserver(() => {
      if (chart) chart.resize();
      else mountChart();
    });
    ro.observe(el);
    mountChart();

    return () => {
      disposed = true;
      ro.disconnect();
      chart?.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    if (!candles.length) {
      chart.clear();
      return;
    }

    const labels = candles.map((c) => {
      const sec = toUnixSeconds(c.time);
      if (sec == null) return '';
      const d = new Date(sec * 1000);
      return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    });

    const ohlc = candles.map((c) => [c.open, c.close, c.low, c.high]);

    chart.setOption({
      backgroundColor: 'transparent',
      animation: false,
      grid: { left: 48, right: 12, top: 16, bottom: 22 },
      xAxis: {
        type: 'category',
        data: labels,
        boundaryGap: true,
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.08)' } },
        axisLabel: {
          fontSize: 9,
          color: '#9ca3af',
          interval: Math.max(0, Math.floor(labels.length / 6) - 1),
        },
      },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
        axisLabel: { fontSize: 9, color: '#9ca3af' },
      },
      series: [
        {
          type: 'candlestick',
          data: ohlc,
          itemStyle: {
            color: '#16a34a',
            color0: '#dc2626',
            borderColor: '#16a34a',
            borderColor0: '#dc2626',
          },
          markLine: highlightIndex >= 0 ? {
            symbol: 'none',
            silent: true,
            lineStyle: { color: '#f59e0b', width: 1, type: 'dashed' },
            data: [{ xAxis: highlightIndex }],
          } : undefined,
        },
        ...scatterSeries,
      ],
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        textStyle: { fontSize: 11 },
      },
      dataZoom: highlightIndex >= 0 ? [{
        type: 'inside',
        startValue: Math.max(0, highlightIndex - 30),
        endValue: Math.min(labels.length - 1, highlightIndex + 30),
      }] : undefined,
    }, { notMerge: true });
  }, [candles, scatterSeries, highlightIndex]);

  if (!symbol || !meta?.oldest) return null;

  return (
    <div className={cn('backtest-price-chart-wrap', className)}>
      <div className="backtest-price-chart-wrap__head">
        <span className="backtest-price-chart-wrap__title">
          {title ?? `Price & trades · ${timeframe}`}
        </span>
        <BacktestChartLegend />
        {markersPending && (
          <span className="backtest-price-chart-wrap__status text-[0.58rem] text-muted-foreground">
            <Loader2 className="inline size-3 animate-spin" aria-hidden />
            Loading trade markers…
          </span>
        )}
        {!markersPending && markerPoints.length > 0 && (
          <span className="backtest-price-chart-wrap__status text-[0.58rem] text-muted-foreground">
            {markerCounts.entries} entries · {markerCounts.exits} exits
          </span>
        )}
      </div>
      {loading && (
        <div className="backtest-price-chart__state" aria-live="polite">
          <Loader2 className="animate-spin text-muted-foreground" aria-hidden />
          <span className="text-[0.62rem] text-muted-foreground">Loading candles…</span>
        </div>
      )}
      {!loading && loadError && (
        <p className="backtest-price-chart__state text-[0.62rem] text-muted-foreground">{loadError}</p>
      )}
      <div
        ref={containerRef}
        className={cn('backtest-price-chart', (loading || loadError) && 'backtest-price-chart--hidden')}
        aria-label="Backtest price chart with trade markers"
      />
    </div>
  );
}
