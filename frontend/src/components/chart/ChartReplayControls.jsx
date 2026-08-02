import React from 'react';
import { Button } from '@/components/ui/button';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { RotateCcw, SkipBack, Play, Pause, SkipForward, X } from 'lucide-react';

export default function ChartReplayControls({
  replayActive,
  replayPlaying,
  replayIndex,
  totalCandles,
  replaySpeed,
  setReplayPlaying,
  setReplayIndex,
  setReplaySpeed,
  exitReplay,
}) {
  if (!replayActive) return null;

  return (
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
      <Button variant="ghost" size="icon-sm" title="Step forward" onClick={() => { setReplayPlaying(false); setReplayIndex((i) => Math.min(totalCandles, i + 1)); }}>
        <SkipForward size={13} />
      </Button>
      <span className="px-1 font-mono text-[10px] text-muted-foreground tabular-nums">
        {Math.min(replayIndex, totalCandles)}/{totalCandles}
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
  );
}
