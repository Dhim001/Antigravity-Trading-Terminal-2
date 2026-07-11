import React, { useEffect, useState, useMemo, useRef } from 'react';
import * as echarts from 'echarts';
import { fetchFootprint } from '../../api/endpoints';

export default function FootprintChartWidget({ symbol, fromTs, toTs, priceStep = 10, timeBucketMs = 60000 }) {
  const [data, setData] = useState([]);
  const [rangeNote, setRangeNote] = useState(null);
  const [loading, setLoading] = useState(false);
  const containerRef = useRef(null);
  const chartInstanceRef = useRef(null);

  useEffect(() => {
    let active = true;
    if (!symbol || !fromTs || !toTs) return;

    setLoading(true);
    fetchFootprint(symbol, fromTs, toTs, priceStep, timeBucketMs).then((res) => {
      if (!active) return;
      const cells = Array.isArray(res?.footprint) ? res.footprint : [];
      setData(cells);
      const note = res?.message || res?.meta?.range_note || null;
      setRangeNote(note);
      setLoading(false);
    }).catch(err => {
      console.error("Footprint fetch error:", err);
      if (active) setLoading(false);
    });

    return () => { active = false; };
  }, [symbol, fromTs, toTs, priceStep, timeBucketMs]);

  const option = useMemo(() => {
    if (!data.length) return {};

    const times = Array.from(new Set(data.map(d => d.time))).sort((a, b) => a - b);
    const prices = Array.from(new Set(data.map(d => d.price))).sort((a, b) => a - b);
    
    let maxVolume = 0;
    const heatmapData = data.map(d => {
      if (d.volume > maxVolume) maxVolume = d.volume;
      return [
        times.indexOf(d.time),
        prices.indexOf(d.price),
        d.volume
      ];
    });

    function renderItem(params, api) {
      const xValue = api.value(0);
      const yValue = api.value(1);
      const volume = api.value(2);
      
      const pointLeftBottom = api.coord([xValue - 0.45, yValue - 0.45]);
      const pointRightTop = api.coord([xValue + 0.45, yValue + 0.45]);
      
      const width = pointRightTop[0] - pointLeftBottom[0];
      const height = pointLeftBottom[1] - pointRightTop[1];
      
      const intensity = Math.min(1, volume / (maxVolume || 1));
      const r = Math.round(59 + (40 - 59) * intensity);
      const g = Math.round(130 + (255 - 130) * intensity);
      const b = Math.round(246 + (40 - 246) * intensity);
      
      return {
        type: 'rect',
        shape: {
          x: pointLeftBottom[0],
          y: pointRightTop[1],
          width: width,
          height: height
        },
        style: api.style({
          fill: `rgba(${r}, ${g}, ${b}, ${0.3 + 0.7 * intensity})`,
          text: volume.toFixed(1),
          textFill: intensity > 0.5 ? '#000' : '#fff',
          fontSize: Math.max(9, Math.min(12, height * 0.4))
        })
      };
    }

    return {
      tooltip: {
        position: 'top',
        formatter: (params) => {
          const time = times[params.value[0]];
          const price = prices[params.value[1]];
          const vol = params.value[2];
          return `${new Date(time).toLocaleTimeString()}<br/>Price: ${price}<br/>Volume: ${vol.toFixed(3)}`;
        }
      },
      grid: {
        top: 10,
        bottom: 40,
        left: 60,
        right: 20
      },
      xAxis: {
        type: 'category',
        data: times.map(t => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })),
        splitLine: { show: true, lineStyle: { color: '#333' } },
        axisLabel: { color: '#999' }
      },
      yAxis: {
        type: 'category',
        data: prices,
        splitLine: { show: true, lineStyle: { color: '#333' } },
        axisLabel: { color: '#999' }
      },
      dataZoom: [
        { type: 'inside', xAxisIndex: 0 },
        { type: 'inside', yAxisIndex: 0 }
      ],
      series: [{
        type: 'custom',
        renderItem: renderItem,
        data: heatmapData,
        encode: {
          x: 0,
          y: 1,
          tooltip: [0, 1, 2]
        }
      }]
    };
  }, [data]);

  useEffect(() => {
    if (!containerRef.current) return;
    
    if (!chartInstanceRef.current) {
      chartInstanceRef.current = echarts.init(containerRef.current);
    }
    
    if (Object.keys(option).length > 0) {
      chartInstanceRef.current.setOption(option, true);
    } else {
      chartInstanceRef.current.clear();
    }
    
    const handleResize = () => chartInstanceRef.current?.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, [option]);
  
  useEffect(() => {
    return () => {
      if (chartInstanceRef.current) {
        chartInstanceRef.current.dispose();
        chartInstanceRef.current = null;
      }
    };
  }, []);

  if (loading) {
    return (
      <div className="w-full h-full flex items-center justify-center text-zinc-500">
        <div className="animate-spin rounded-full h-6 w-6 border-t-2 border-b-2 border-indigo-500 mr-3"></div>
        Loading order flow...
      </div>
    );
  }

  if (!data.length) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center text-zinc-500 text-sm p-4 text-center bg-[#0d0e12]">
        <svg xmlns="http://www.w3.org/2000/svg" className="h-10 w-10 mb-2 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4" />
        </svg>
        <p>No tick data available for this timeframe.</p>
        <p className="text-xs mt-1 text-zinc-600">Ensure tick archiving is enabled and trades have occurred.</p>
      </div>
    );
  }

  return (
    <div className="w-full h-full relative bg-[#0d0e12]" style={{ minHeight: '300px' }}>
      {rangeNote ? (
        <div className="absolute top-1 left-2 right-2 z-10 text-[10px] text-amber-400/90 truncate pointer-events-none">
          {rangeNote}
        </div>
      ) : null}
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
    </div>
  );
}
