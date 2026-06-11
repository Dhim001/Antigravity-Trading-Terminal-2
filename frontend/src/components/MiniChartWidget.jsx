/**
 * MiniChartWidget.jsx — Compact ECharts panel for multi-chart grid.
 */
import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import * as echarts from 'echarts';
import { useStore } from '../store/useStore';
import { calcEMA } from '../utils/indicators';
import { cn } from '@/lib/utils';
import { Maximize2, Minimize2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';

const SYMBOL_COLORS = {
  BTCUSDT: '#f59e0b',
  ETHUSDT: '#8b5cf6',
  AAPL: '#34d399',
  TSLA: '#f87171',
  MSFT: '#06b6d4',
};

const pad = (n) => String(n).padStart(2, '0');

function MiniChartHeaderPrice({ symbol }) {
  const ticker = useStore(state => state.tickerData[symbol]);
  const direction = useStore(state => state.priceDirections[symbol]);

  if (!ticker) {
    return <span className="text-[0.7rem] text-muted-foreground">Loading…</span>;
  }

  const dec = (
    symbol.includes('XRP') || symbol.includes('ADA') || symbol.includes('DOGE') || ticker.price < 2.0
  ) ? 4 : 2;

  const priceClass =
    direction === 'up' ? 'text-trading-up'
      : direction === 'down' ? 'text-trading-down'
        : 'text-foreground';

  return (
    <div className="flex min-w-0 items-center gap-1.5 overflow-hidden text-ellipsis whitespace-nowrap text-xs">
      <span className={cn('num-mono text-[0.8rem] font-bold transition-colors', priceClass)}>
        {ticker.price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
      </span>
      <span className={cn(
        'num-mono text-[0.7rem]',
        ticker.change_24h >= 0 ? 'text-trading-up' : 'text-trading-down',
      )}>
        {ticker.change_24h >= 0 ? '+' : ''}{ticker.change_24h}%
      </span>
    </div>
  );
}

const EMPTY_ARRAY = [];

export default function MiniChartWidget({
  defaultSymbol = 'BTCUSDT',
  isFocused = false,
  onFocus,
  isMaximized = false,
  onToggleMaximize,
  className,
}) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);

  const [symbol, setSymbol] = useState(defaultSymbol);

  const lastCandleTime = useStore(state => {
    const candles = state.candleData[symbol];
    return candles && candles.length > 0 ? candles[candles.length - 1].time : 0;
  });
  const setActiveSymbol = useStore(state => state.setActiveSymbol);
  const symbolsList = useStore(state => state.symbolsList);

  const accentCol = SYMBOL_COLORS[symbol] || '#6366f1';

  const symbolCandles = useMemo(() => {
    return useStore.getState().candleData[symbol] || EMPTY_ARRAY;
  }, [lastCandleTime, symbol]);

  const configureChart = useCallback(() => {
    if (!chartRef.current || symbolCandles.length === 0) return;

    const candles = symbolCandles;
    const dec = (
      symbol.includes('XRP') || symbol.includes('ADA') || symbol.includes('DOGE') ||
      candles[candles.length - 1]?.close < 2.0
    ) ? 4 : 2;

    const categoryData = candles.map(c => {
      const d = new Date(c.time * 1000);
      return `${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
    });

    const candlestickData = candles.map(c => [c.open, c.close, c.low, c.high]);
    const ema9 = calcEMA(candles, 9);
    const ema9Data = candles.map((c, i) => i >= 8 ? ema9[i - 8]?.value : null);
    const ema21 = calcEMA(candles, 21);
    const ema21Data = candles.map((c, i) => i >= 20 ? ema21[i - 20]?.value : null);

    const option = {
      backgroundColor: '#0b0f19',
      grid: { left: '2%', right: '8%', top: '6%', bottom: '18%' },
      tooltip: { show: false },
      xAxis: {
        type: 'category',
        data: categoryData,
        scale: true,
        boundaryGap: false,
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
        splitLine: { show: true, lineStyle: { color: 'rgba(255,255,255,0.02)' } },
        axisLabel: { color: '#9ca3af', fontSize: 9 },
      },
      yAxis: {
        scale: true,
        position: 'right',
        splitLine: { show: true, lineStyle: { color: 'rgba(255,255,255,0.02)' } },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
        axisLabel: { color: '#9ca3af', fontSize: 9, formatter: val => val.toFixed(dec) },
      },
      dataZoom: [{ type: 'inside', start: 75, end: 100 }],
      series: [
        {
          name: symbol,
          type: 'candlestick',
          data: candlestickData,
          itemStyle: {
            color: '#10b981',
            color0: '#ef4444',
            borderColor: '#10b981',
            borderColor0: '#ef4444',
          },
        },
        {
          name: 'EMA 9',
          type: 'line',
          data: ema9Data,
          showSymbol: false,
          lineStyle: { color: '#f59e0b', width: 1, opacity: 0.8 },
        },
        {
          name: 'EMA 21',
          type: 'line',
          data: ema21Data,
          showSymbol: false,
          lineStyle: { color: '#8b5cf6', width: 1, opacity: 0.8 },
        },
      ],
    };

    chartRef.current.setOption(option, { notMerge: true });
  }, [symbolCandles, symbol]);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = echarts.init(containerRef.current, 'dark');
    chartRef.current = chart;

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    configureChart();
  }, [configureChart]);

  useEffect(() => {
    const unsub = useStore.subscribe(
      state => state.candleData[symbol],
      (candles) => {
        if (!candles || candles.length === 0 || !chartRef.current) return;
        const last = candles[candles.length - 1];
        const d = new Date(last.time * 1000);
        const timeLabel = `${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;

        try {
          const option = chartRef.current.getOption();
          const candlestickSeries = option.series[0];

          if (candlestickSeries) {
            const entry = [last.open, last.close, last.low, last.high];

            if (option.xAxis[0].data[option.xAxis[0].data.length - 1] === timeLabel) {
              candlestickSeries.data[candlestickSeries.data.length - 1] = entry;
            } else {
              candlestickSeries.data.push(entry);
              option.xAxis[0].data.push(timeLabel);
            }

            chartRef.current.setOption({ xAxis: option.xAxis, series: option.series });
          }
        } catch (_) {}
      },
    );
    return unsub;
  }, [symbol]);

  useEffect(() => {
    setSymbol(defaultSymbol);
  }, [defaultSymbol]);

  const handleFocusClick = () => {
    setActiveSymbol(symbol);
    if (onFocus) onFocus(symbol);
  };

  const handleSymbolChange = (next) => {
    setSymbol(next);
    setActiveSymbol(next);
    if (onFocus) onFocus(next);
  };

  return (
    <div
      className={cn(
        'flex cursor-pointer flex-col overflow-hidden rounded-md bg-card transition-all duration-200',
        isFocused ? 'border-[1.5px] shadow-[0_0_12px]' : 'border border-border',
        isMaximized && 'absolute top-1 left-1 z-50 h-[calc(100%-8px)] w-[calc(100%-8px)] shadow-2xl',
        !isMaximized && 'relative h-full w-full',
        className,
      )}
      style={{
        borderColor: isFocused ? accentCol : undefined,
        boxShadow: isFocused && !isMaximized ? `0 0 12px ${accentCol}30` : undefined,
      }}
      onClick={handleFocusClick}
    >
      <div
        className="flex shrink-0 select-none items-center justify-between gap-2 border-b border-border bg-muted/30 px-2.5 py-1.5"
        onClick={e => e.stopPropagation()}
        onDoubleClick={(e) => {
          e.stopPropagation();
          if (onToggleMaximize) onToggleMaximize();
        }}
      >
        <Select value={symbol} onValueChange={handleSymbolChange}>
          <SelectTrigger
            size="sm"
            className="h-7 w-auto gap-1.5 border-0 bg-transparent px-1 shadow-none focus-visible:ring-0"
            style={{ color: accentCol }}
          >
            <span
              className="size-2 shrink-0 rounded-full"
              style={{ background: accentCol, boxShadow: `0 0 6px ${accentCol}` }}
            />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {symbolsList.map(s => (
              <SelectItem key={s} value={s} className="text-xs">
                <span className="flex items-center gap-2">
                  <span
                    className="size-1.5 shrink-0 rounded-full"
                    style={{ background: SYMBOL_COLORS[s] || '#6366f1' }}
                  />
                  {s}
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <MiniChartHeaderPrice symbol={symbol} />

        <Button
          variant="ghost"
          size="icon-xs"
          className="shrink-0 text-muted-foreground hover:text-foreground"
          onClick={(e) => {
            e.stopPropagation();
            if (onToggleMaximize) onToggleMaximize();
            else handleFocusClick();
          }}
          title={isMaximized ? 'Restore grid layout' : 'Maximize chart'}
        >
          {isMaximized ? <Minimize2 /> : <Maximize2 />}
        </Button>
      </div>

      <div ref={containerRef} className="min-h-0 flex-1" />
    </div>
  );
}
