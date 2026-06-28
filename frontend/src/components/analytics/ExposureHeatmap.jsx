/**
 * ExposureHeatmap — portfolio concentration by asset class, sector, or strategy.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Badge } from '@/components/ui/badge';
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@/components/ui/empty';
import { cn } from '@/lib/utils';
import { fmtUsd } from '@/lib/analytics/helpers';

const DIMENSIONS = [
  { value: 'asset_class', label: 'Asset Class', field: 'by_asset_class' },
  { value: 'sector', label: 'Sector', field: 'by_sector' },
  { value: 'strategy', label: 'Strategy', field: 'by_strategy' },
  { value: 'cross', label: 'Strategy × Sector', field: null },
];

function buildTreemapOption(slices, { profitColor, lossColor }) {
  if (!slices?.length) return null;
  return {
    backgroundColor: 'transparent',
    tooltip: {
      formatter: (p) => {
        const d = p.data;
        return [
          `<strong>${d.name}</strong>`,
          `Notional: ${fmtUsd(d.value)}`,
          `Share: ${Number(d.pct ?? 0).toFixed(1)}%`,
          d.symbols?.length ? `Symbols: ${d.symbols.join(', ')}` : '',
        ].filter(Boolean).join('<br/>');
      },
    },
    series: [{
      type: 'treemap',
      roam: false,
      nodeClick: false,
      breadcrumb: { show: false },
      width: '100%',
      height: '100%',
      label: {
        show: true,
        formatter: (p) => `${p.name}\n${Number(p.data.pct ?? 0).toFixed(1)}%`,
        fontSize: 11,
        color: '#f8fafc',
      },
      upperLabel: { show: false },
      itemStyle: {
        borderColor: 'rgba(15, 23, 42, 0.65)',
        borderWidth: 2,
        gapWidth: 2,
      },
      levels: [{
        itemStyle: {
          borderWidth: 0,
          gapWidth: 3,
        },
      }],
      data: slices.map((s, i) => ({
        name: s.key,
        value: s.notional,
        pct: s.pct,
        symbols: s.symbols,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 1, 1, [
            { offset: 0, color: i % 2 === 0 ? `${profitColor}55` : `${profitColor}33` },
            { offset: 1, color: i % 2 === 0 ? `${lossColor}22` : `${profitColor}18` },
          ]),
        },
      })),
    }],
  };
}

function buildCrossHeatmapOption(cross, profitColor, lossColor) {
  const { rows, cols, matrix } = cross || {};
  if (!rows?.length || !cols?.length || !matrix?.length) return null;

  const flat = matrix.flat();
  const maxVal = Math.max(...flat, 1);

  const cells = matrix.flatMap((row, rowIdx) =>
    row.map((val, colIdx) => [colIdx, rowIdx, val]),
  );

  return {
    backgroundColor: 'transparent',
    tooltip: {
      position: 'top',
      formatter: (p) => {
        const [xIdx, yIdx, val] = p.data;
        return [
          `<strong>${rows[yIdx]}</strong> × <strong>${cols[xIdx]}</strong>`,
          `Notional: ${fmtUsd(val)}`,
        ].join('<br/>');
      },
    },
    grid: {
      left: Math.min(Math.max(...rows.map((r) => r.length), 4) * 7, 120),
      right: 48,
      top: 28,
      bottom: 12,
      containLabel: false,
    },
    xAxis: {
      type: 'category',
      data: cols,
      position: 'top',
      axisLabel: { color: '#9ca3af', fontSize: 10, rotate: cols.length > 5 ? 35 : 0 },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'category',
      data: rows,
      inverse: true,
      axisLabel: { color: '#9ca3af', fontSize: 10 },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    visualMap: {
      min: 0,
      max: maxVal,
      calculable: false,
      orient: 'vertical',
      right: 4,
      top: 'center',
      inRange: { color: [`${lossColor}33`, profitColor] },
      textStyle: { color: '#9ca3af', fontSize: 9 },
      formatter: (v) => fmtUsd(v),
    },
    series: [{
      type: 'heatmap',
      data: cells,
      label: {
        show: rows.length * cols.length <= 24,
        fontSize: 9,
        color: '#f8fafc',
        formatter: (p) => {
          const val = p.data[2];
          return val >= 1000 ? `$${(val / 1000).toFixed(1)}k` : `$${Math.round(val)}`;
        },
      },
      itemStyle: { borderColor: 'rgba(148, 163, 184, 0.15)', borderWidth: 1 },
    }],
  };
}

export default function ExposureHeatmap({
  exposure,
  className = '',
  profitColor = '#10b981',
  lossColor = '#ef4444',
}) {
  const [dimension, setDimension] = useState('asset_class');
  const chartRef = useRef(null);
  const chartInst = useRef(null);

  const activeMeta = DIMENSIONS.find((d) => d.value === dimension) || DIMENSIONS[0];
  const slices = activeMeta.field ? (exposure?.[activeMeta.field] ?? []) : null;

  const option = useMemo(() => {
    if (dimension === 'cross') {
      return buildCrossHeatmapOption(exposure?.cross_strategy_sector, profitColor, lossColor);
    }
    return buildTreemapOption(slices, { profitColor, lossColor });
  }, [dimension, exposure, slices, profitColor, lossColor]);

  const chartHeight = dimension === 'cross'
    ? Math.max(240, (exposure?.cross_strategy_sector?.rows?.length || 2) * 44 + 80)
    : 280;

  useEffect(() => {
    const el = chartRef.current;
    if (!el) return undefined;
    let chart = null;
    let disposed = false;
    const mount = () => {
      if (disposed || chart) return;
      if (el.clientWidth < 2 || el.clientHeight < 2) return;
      chart = echarts.init(el, 'dark');
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

  const hasData = dimension === 'cross'
    ? (exposure?.cross_strategy_sector?.matrix?.length > 0)
    : (slices?.length > 0);

  return (
    <div className={cn('portfolio-dashboard__exposure flex flex-col gap-3', className)}>
      <div className="flex flex-wrap items-center gap-2">
        <ToggleGroup
          type="single"
          value={dimension}
          onValueChange={(v) => { if (v) setDimension(v); }}
          size="sm"
          variant="outline"
          className="flex-wrap"
        >
          {DIMENSIONS.map((d) => (
            <ToggleGroupItem key={d.value} value={d.value} className="text-xs px-2">
              {d.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        {exposure?.position_count ? (
          <Badge variant="secondary" className="text-[0.62rem] font-normal">
            {exposure.position_count} position(s)
          </Badge>
        ) : null}
        {exposure?.total_notional ? (
          <Badge variant="outline" className="text-[0.62rem] font-normal">
            {fmtUsd(exposure.total_notional)} notional
          </Badge>
        ) : null}
      </div>

      {hasData && option ? (
        <div
          ref={chartRef}
          role="img"
          aria-label={`Portfolio exposure by ${activeMeta.label}`}
          className="portfolio-dashboard__exposure-chart w-full min-h-[240px]"
          style={{ height: chartHeight }}
          onPointerDown={(e) => e.stopPropagation()}
        />
      ) : (
        <Empty className="flex-1 border-0 py-10">
          <EmptyHeader>
            <EmptyTitle>No open exposure</EmptyTitle>
            <EmptyDescription>
              Deploy bots or open manual positions to see concentration heatmaps.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}

      {hasData && dimension !== 'cross' && slices?.length ? (
        <div className="flex flex-wrap gap-2">
          {slices.slice(0, 6).map((s) => (
            <Badge key={s.key} variant="outline" className="text-[0.62rem] font-normal">
              {s.key}: {s.pct}%
            </Badge>
          ))}
        </div>
      ) : null}
    </div>
  );
}
