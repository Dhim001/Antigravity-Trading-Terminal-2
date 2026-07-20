/**
 * CorrelationMatrix — Pearson correlation heatmap with theme-aware diverging scale.
 */
import { useCallback, useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts';
import { initEcharts } from '@/lib/echartsInit';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import {
  buildCorrelationHeatmapCells,
  correlationAxisFontSize,
  correlationCellSize,
  correlationStrengthLabel,
} from '@/lib/analytics/helpers';

const AXIS_MUTED = '#9ca3af';
const GRID_LINE = 'rgba(148, 163, 184, 0.12)';

function buildChartOption({
  symbols,
  matrix,
  meta,
  profitColor,
  lossColor,
  neutralColor,
}) {
  const n = symbols.length;
  const cellSize = correlationCellSize(n);
  const plotSize = n * cellSize;
  const labelSize = correlationAxisFontSize(n);
  const showCellLabels = n <= 8;
  const longest = Math.max(...symbols.map((s) => s.length), 4);
  const leftPad = Math.min(Math.max(longest * 6.5, 52), 88);

  const cells = buildCorrelationHeatmapCells(matrix, { lowerTriangleOnly: true });

  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: {
      trigger: 'item',
      confine: true,
      backgroundColor: 'rgba(15, 23, 42, 0.94)',
      borderColor: 'rgba(148, 163, 184, 0.25)',
      textStyle: { color: '#e2e8f0', fontSize: 12, lineHeight: 18 },
      formatter: (params) => {
        const [xIdx, yIdx, value] = params.data;
        const rowSym = symbols[yIdx];
        const colSym = symbols[xIdx];
        if (xIdx > yIdx) return '';
        const strength = correlationStrengthLabel(value);
        const lines = [
          `<strong>${rowSym}</strong> × <strong>${colSym}</strong>`,
          `ρ = ${Number(value).toFixed(3)} · ${strength}`,
        ];
        if (meta?.commonDays != null) {
          lines.push(`${meta.commonDays} day sample (max overlap)`);
        }
        if (meta?.returnType) {
          lines.push(meta.returnType);
        }
        return lines.join('<br/>');
      },
    },
    grid: {
      left: leftPad,
      top: 8,
      right: 56,
      bottom: 8,
      width: plotSize,
      height: plotSize,
      containLabel: false,
    },
    xAxis: {
      type: 'category',
      data: symbols,
      position: 'top',
      splitArea: { show: true, areaStyle: { color: ['transparent', 'rgba(148,163,184,0.04)'] } },
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: AXIS_MUTED,
        fontSize: labelSize,
        rotate: n > 6 ? 40 : 0,
        interval: 0,
        margin: 10,
        formatter: (value) => (value.length > 10 ? `${value.slice(0, 9)}…` : value),
      },
    },
    yAxis: {
      type: 'category',
      data: symbols,
      inverse: true,
      splitArea: { show: true, areaStyle: { color: ['transparent', 'rgba(148,163,184,0.04)'] } },
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: AXIS_MUTED,
        fontSize: labelSize,
        interval: 0,
        margin: 10,
        formatter: (value) => (value.length > 12 ? `${value.slice(0, 11)}…` : value),
      },
    },
    visualMap: {
      min: -1,
      max: 1,
      calculable: false,
      orient: 'vertical',
      right: 4,
      top: 'center',
      itemWidth: 10,
      itemHeight: Math.min(plotSize, 160),
      inRange: { color: [lossColor, neutralColor, profitColor] },
      text: ['+1', '0', '−1'],
      textStyle: { color: AXIS_MUTED, fontSize: 10 },
      formatter: (v) => Number(v).toFixed(1),
    },
    series: [{
      type: 'heatmap',
      data: cells,
      emphasis: {
        itemStyle: {
          shadowBlur: 8,
          shadowColor: 'rgba(0, 0, 0, 0.35)',
          borderColor: 'rgba(226, 232, 240, 0.45)',
          borderWidth: 1,
        },
      },
      itemStyle: {
        borderColor: GRID_LINE,
        borderWidth: 1,
      },
      label: {
        show: showCellLabels,
        fontSize: Math.max(labelSize - 1, 8),
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        color: 'rgba(248, 250, 252, 0.92)',
        formatter: (p) => {
          const [xIdx, yIdx, value] = p.data;
          if (xIdx > yIdx) return '';
          return Number(value).toFixed(2);
        },
      },
    }],
  };
}

export default function CorrelationMatrix({
  correlation,
  className = '',
  profitColor = '#10b981',
  lossColor = '#ef4444',
  neutralColor = '#374151',
}) {
  const chartRef = useRef(null);
  const chartInst = useRef(null);

  const symbols = correlation?.symbols ?? [];
  const matrix = correlation?.matrix ?? [];
  const hasData = symbols.length >= 2 && matrix.length >= 2;

  const meta = useMemo(() => ({
    mode: correlation?.mode,
    returnType: correlation?.return_type,
    period: correlation?.period,
    source: correlation?.source,
    commonDays: correlation?.common_days,
    pairwise: correlation?.pairwise,
  }), [correlation]);

  const chartSize = useMemo(() => {
    const n = symbols.length;
    const cell = correlationCellSize(n);
    const plot = n * cell;
    const longest = Math.max(...symbols.map((s) => s.length), 4);
    const leftPad = Math.min(Math.max(longest * 6.5, 52), 88);
    return {
      width: plot + leftPad + 72,
      height: plot + (n > 6 ? 36 : 24),
    };
  }, [symbols]);

  const option = useMemo(() => {
    if (!hasData) return null;
    return buildChartOption({
      symbols,
      matrix,
      meta,
      profitColor,
      lossColor,
      neutralColor,
    });
  }, [hasData, symbols, matrix, meta, profitColor, lossColor, neutralColor]);

  const summaryLabel = useMemo(() => {
    if (!hasData) return 'Correlation matrix unavailable';
    const modeLabel = meta.mode === 'price'
      ? 'price log-returns'
      : meta.mode === 'trade_pnl'
        ? 'trade PnL returns'
        : 'auto correlation';
    return `${symbols.length} symbols, ${modeLabel}, scale −1 to +1`;
  }, [hasData, symbols.length, meta.mode]);

  useEffect(() => {
    const el = chartRef.current;
    if (!el) return undefined;
    let chart = null;
    let disposed = false;
    const mount = () => {
      if (disposed || chart) return;
      if (el.clientWidth < 2 || el.clientHeight < 2) return;
      chart = initEcharts(el, 'dark');
      chartInst.current = chart;
    };
    const ro = new ResizeObserver(() => {
      if (chart) chart.resize();
      else mount();
    });
    ro.observe(el);
    mount();
    return () => {
      disposed = true;
      ro.disconnect();
      chart?.dispose();
      chartInst.current = null;
    };
  }, []);

  const applyOption = useCallback(() => {
    if (!chartInst.current || !option) return;
    chartInst.current.setOption(option, { notMerge: true });
  }, [option]);

  useEffect(() => { applyOption(); }, [applyOption]);

  if (!hasData) return null;

  const scrollable = symbols.length > 6;

  return (
    <div className={cn('portfolio-dashboard__correlation flex min-h-0 flex-col gap-3', className)}>
      <div className="portfolio-dashboard__correlation-meta flex flex-wrap items-center gap-2">
        <Badge variant="secondary" className="text-[0.62rem] font-normal">
          {symbols.length} symbols
        </Badge>
        {meta.commonDays != null ? (
          <Badge variant="outline" className="text-[0.62rem] font-normal">
            {meta.commonDays}d overlap
          </Badge>
        ) : null}
        {meta.period ? (
          <Badge variant="outline" className="text-[0.62rem] font-normal">
            {meta.period}
          </Badge>
        ) : null}
        {meta.pairwise ? (
          <Badge variant="outline" className="text-[0.62rem] font-normal">
            Pairwise
          </Badge>
        ) : null}
      </div>

      <ScrollArea
        className={cn(
          'portfolio-dashboard__correlation-scroll',
          scrollable && 'portfolio-dashboard__correlation-scroll--bounded',
        )}
      >
        <div
          className="portfolio-dashboard__correlation-chart-wrap"
          style={{
            width: scrollable ? chartSize.width : '100%',
            height: chartSize.height,
          }}
        >
          <div
            ref={chartRef}
            role="img"
            aria-label={summaryLabel}
            className="portfolio-dashboard__correlation-chart"
            style={{ width: '100%', height: chartSize.height }}
            onPointerDown={(e) => e.stopPropagation()}
          />
        </div>
        {scrollable ? <ScrollBar orientation="horizontal" /> : null}
      </ScrollArea>

      <p className="text-[0.625rem] leading-relaxed text-muted-foreground">
        Lower triangle only · Red = negative · Green = positive · Hover cells for pair detail
      </p>
    </div>
  );
}
