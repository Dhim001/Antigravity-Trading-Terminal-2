import React from 'react';
import {
  AreaChart, TrendingUp, CandlestickChart, Grid3x3, Spline, Minus,
  AlignJustify, Square, BarChart2, History, Trash2,
} from 'lucide-react';
import { GitCompareArrows } from 'lucide-react';
import { WidgetToolbar, WidgetToolbarDivider } from '../WidgetShell';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Button } from '@/components/ui/button';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';
import { TF_CONFIGS } from '../../lib/chart/chartHelpers';

export default function ChartToolbar({
  timeframe,
  onTimeframeChange,
  chartType,
  onChartTypeChange,
  activeTool,
  onActiveToolChange,
  showVolumeProfile,
  onToggleVolumeProfile,
  replayActive,
  onToggleReplay,
  compareOptions,
  compareSymbol,
  onCompareSymbolChange,
  drawings,
  selectedDrawingId,
  onRemoveDrawing,
  onClearDrawings,
  chartInteractionMode,
  onCancelInteraction,
  hasSlTpOverlay,
  activeIndicatorKeys,
  onIndicatorsChange,
  indicatorToolbar,
}) {
  return (
    <div className="chart-toolbar-stack">
      <div className="chart-toolbar-row">
        <div className="scroll-fade-x">
          <WidgetToolbar className="scroll-panel-x no-scrollbar flex-nowrap border-0 py-0">
            <ToggleGroup type="single" value={timeframe} onValueChange={(v) => v && onTimeframeChange(v)} spacing={0}>
              {TF_CONFIGS.map((tf) => (
                <ToggleGroupItem key={tf.label} value={tf.label} size="sm" className="px-2 text-[0.68rem] font-bold">
                  {tf.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
            <WidgetToolbarDivider />
            <ToggleGroup type="single" value={chartType} onValueChange={(v) => v && onChartTypeChange(v)} spacing={0}>
              <ToggleGroupItem value="candle" size="sm" className="px-2 text-[0.68rem] font-bold">
                <AreaChart data-icon="inline-start" />
                Candle
              </ToggleGroupItem>
              <ToggleGroupItem value="heikin" size="sm" className="px-2 text-[0.68rem] font-bold" title="Heikin-Ashi">
                <CandlestickChart data-icon="inline-start" />
                HA
              </ToggleGroupItem>
              <ToggleGroupItem value="renko" size="sm" className="px-2 text-[0.68rem] font-bold" title="Renko (time-aligned)">
                <Grid3x3 data-icon="inline-start" />
                Renko
              </ToggleGroupItem>
              <ToggleGroupItem value="line" size="sm" className="px-2 text-[0.68rem] font-bold">
                <TrendingUp data-icon="inline-start" />
                Line
              </ToggleGroupItem>
            </ToggleGroup>
            <WidgetToolbarDivider />
            <ToggleGroup
              type="single"
              value={activeTool || ''}
              onValueChange={(v) => onActiveToolChange(v || null)}
              spacing={0}
            >
              <ToggleGroupItem value="trendline" size="sm" className="px-1.5" title="Trendline (2 clicks)">
                <Spline size={13} />
              </ToggleGroupItem>
              <ToggleGroupItem value="hline" size="sm" className="px-1.5" title="Horizontal level (1 click)">
                <Minus size={13} />
              </ToggleGroupItem>
              <ToggleGroupItem value="fib" size="sm" className="px-1.5" title="Fibonacci retracement (2 clicks)">
                <AlignJustify size={13} />
              </ToggleGroupItem>
              <ToggleGroupItem value="rectangle" size="sm" className="px-1.5" title="Rectangle (2 clicks)">
                <Square size={13} />
              </ToggleGroupItem>
            </ToggleGroup>
            <Button
              variant={showVolumeProfile ? 'secondary' : 'ghost'}
              size="sm"
              className="px-1.5"
              title="Volume Profile (VPVR)"
              onClick={onToggleVolumeProfile}
            >
              <BarChart2 size={13} />
            </Button>
            <Button
              variant={replayActive ? 'secondary' : 'ghost'}
              size="sm"
              className="px-1.5"
              title="Replay mode (bar-by-bar)"
              onClick={onToggleReplay}
            >
              <History size={13} />
            </Button>
            {compareOptions.length > 0 && (
              <Select
                value={compareSymbol || '__none__'}
                onValueChange={(v) => onCompareSymbolChange(v === '__none__' ? null : v)}
              >
                <SelectTrigger
                  className={cn(
                    'h-6 w-auto gap-1 border-0 px-1.5 text-[0.62rem] font-semibold',
                    compareSymbol && 'text-[#f472b6]',
                  )}
                  aria-label="Compare symbol"
                  title="Compare with another symbol (rebased %)"
                >
                  <GitCompareArrows size={13} />
                  <SelectValue placeholder="Compare" />
                </SelectTrigger>
                <SelectContent position="popper" className="max-h-64">
                  <SelectItem value="__none__" className="text-xs">Compare: off</SelectItem>
                  {compareOptions.map((sym) => (
                    <SelectItem key={sym} value={sym} className="text-xs">{sym}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {(drawings.length > 0 || selectedDrawingId) && (
              <Button
                variant="ghost"
                size="sm"
                className="px-1.5 text-muted-foreground hover:text-destructive"
                title={selectedDrawingId ? 'Delete selected drawing' : 'Clear all drawings'}
                onClick={() => (selectedDrawingId ? onRemoveDrawing(selectedDrawingId) : onClearDrawings())}
              >
                <Trash2 size={13} />
              </Button>
            )}
            {chartInteractionMode !== 'normal' && (
              <Button
                variant="destructive"
                size="sm"
                className="ml-auto h-6 shrink-0 text-[0.62rem]"
                onClick={onCancelInteraction}
              >
                Cancel {chartInteractionMode === 'edit_sl' ? 'SL' : 'TP'} Edit
              </Button>
            )}
            {hasSlTpOverlay && chartInteractionMode === 'normal' && !activeTool && (
              <span className="ml-auto shrink-0 text-[0.62rem] text-muted-foreground">
                Drag SL/TP handles on chart edge
              </span>
            )}
          </WidgetToolbar>
        </div>
      </div>
      <div className="chart-toolbar-row">
        <div className="scroll-fade-x">
          <WidgetToolbar compact className="scroll-panel-x no-scrollbar flex-nowrap border-0">
            <ToggleGroup
              type="multiple"
              value={activeIndicatorKeys}
              onValueChange={onIndicatorsChange}
              className="flex flex-nowrap gap-[var(--icon-gap)]"
              spacing={1}
            >
              {Object.entries(indicatorToolbar).map(([key, ind]) => (
                <ToggleGroupItem
                  key={key}
                  value={key}
                  size="sm"
                  className="gap-[var(--icon-gap)] text-[0.62rem] font-semibold data-[state=on]:border-[var(--ind-c)] data-[state=on]:bg-[color-mix(in_srgb,var(--ind-c)_14%,transparent)] data-[state=on]:text-[var(--ind-c)]"
                  style={{ '--ind-c': ind.color }}
                >
                  <span className="size-1.5 shrink-0 rounded-full bg-[var(--ind-c)] opacity-70" />
                  {ind.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </WidgetToolbar>
        </div>
      </div>
    </div>
  );
}
