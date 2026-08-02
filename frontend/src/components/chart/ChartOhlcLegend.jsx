import React from 'react';

export default function ChartOhlcLegend() {
  return (
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
  );
}
