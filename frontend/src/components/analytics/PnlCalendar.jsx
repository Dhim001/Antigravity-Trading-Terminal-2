/**
 * PnlCalendar — GitHub-style daily P&L heatmap (ECharts calendar + heatmap).
 */
import { useCallback, useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import { calendarHeatmapData, calendarPnlRange } from '@/lib/analytics/helpers';

export default function PnlCalendar({
  days = [],
  className = '',
  profitColor = '#10b981',
  lossColor = '#ef4444',
  neutralColor = '#1f2937',
}) {
  const chartRef = useRef(null);
  const chartInst = useRef(null);

  const configure = useCallback(() => {
    if (!chartInst.current || !days.length) return;
    const data = calendarHeatmapData(days);
    const range = calendarPnlRange(days);
    const years = [...new Set(days.map((d) => d.date.slice(0, 4)))];
    const year = years[years.length - 1] || new Date().getFullYear().toString();

    chartInst.current.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        formatter: (p) => {
          const v = p.data?.[1];
          if (v == null) return '';
          const sign = v >= 0 ? '+' : '';
          return `${p.data[0]}<br/>P&L: ${sign}$${Number(v).toFixed(2)}`;
        },
      },
      visualMap: {
        min: -range,
        max: range,
        calculable: false,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        inRange: {
          color: [lossColor, neutralColor, profitColor],
        },
        text: ['Profit', 'Loss'],
        textStyle: { color: '#9ca3af', fontSize: 10 },
      },
      calendar: {
        top: 40,
        left: 30,
        right: 10,
        cellSize: ['auto', 14],
        range: year,
        itemStyle: { borderWidth: 2, borderColor: 'rgba(0,0,0,0.3)' },
        dayLabel: { color: '#6b7280', fontSize: 9 },
        monthLabel: { color: '#9ca3af', fontSize: 10 },
        yearLabel: { show: false },
      },
      series: [{
        type: 'heatmap',
        coordinateSystem: 'calendar',
        data,
      }],
    }, { notMerge: true });
  }, [days, profitColor, lossColor, neutralColor]);

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

  useEffect(() => { configure(); }, [configure]);

  if (!days.length) {
    return (
      <div className={`flex min-h-[120px] items-center justify-center text-xs text-muted-foreground ${className}`}>
        No daily P&L data yet
      </div>
    );
  }

  return <div ref={chartRef} className={`min-h-[160px] w-full ${className}`} />;
}
