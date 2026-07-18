/**
 * RL episode replay — full scrubber with speed, keyboard, and sparkline seek.
 *
 * Expects results.rl_data.episode_steps (and optional position_trajectory /
 * reward_accumulation sparklines).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import {
  actionLabel,
  actionTone,
  findNextTradeAction,
  sparkIndexForStep,
} from '@/lib/rlEpisodeReplay';
import { ChevronLeft, ChevronRight, Pause, Play, SkipForward } from 'lucide-react';

const SPEED_MS = {
  '0.5x': 240,
  '1x': 120,
  '2x': 60,
  '4x': 30,
};

function Sparkline({ values, className, tone = 'accent', markerIndex = null, onSeek }) {
  const pts = (values || []).map(Number).filter((n) => Number.isFinite(n));
  if (pts.length < 2) return null;
  const w = 200;
  const h = 48;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = Math.max(max - min, 1e-9);
  const d = pts
    .map((v, i) => {
      const x = (i / (pts.length - 1)) * w;
      const y = h - ((v - min) / span) * (h - 4) - 2;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  let marker = null;
  if (markerIndex != null && pts.length > 1) {
    const mi = Math.max(0, Math.min(pts.length - 1, markerIndex));
    const mx = (mi / (pts.length - 1)) * w;
    const my = h - ((pts[mi] - min) / span) * (h - 4) - 2;
    marker = { mx, my };
  }

  const handleClick = (e) => {
    if (!onSeek || pts.length < 2) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    onSeek(Math.round(ratio * (pts.length - 1)));
  };

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      className={cn('rl-replay__spark', onSeek && 'rl-replay__spark--seek', className)}
      aria-hidden
      onClick={handleClick}
      role={onSeek ? 'slider' : undefined}
    >
      <path d={d} className={cn('rl-replay__spark-path', `rl-replay__spark-path--${tone}`)} fill="none" />
      {marker && (
        <>
          <line
            x1={marker.mx}
            y1={0}
            x2={marker.mx}
            y2={h}
            className="rl-replay__spark-marker-line"
          />
          <circle cx={marker.mx} cy={marker.my} r={2.5} className="rl-replay__spark-marker" />
        </>
      )}
    </svg>
  );
}

function ObsPreview({ observation, maxDims = 16 }) {
  const obs = Array.isArray(observation) ? observation : [];
  if (!obs.length) {
    return <p className="text-xs text-muted-foreground">No observation vector for this step.</p>;
  }
  const slice = obs.slice(0, maxDims);
  const maxAbs = Math.max(...slice.map((v) => Math.abs(Number(v) || 0)), 1e-9);
  return (
    <div className="rl-replay__obs" aria-label="Observation vector">
      {slice.map((v, i) => {
        const n = Number(v) || 0;
        const pct = Math.min(100, (Math.abs(n) / maxAbs) * 100);
        return (
          <div key={i} className="rl-replay__obs-row">
            <span className="rl-replay__obs-idx num-mono">{i}</span>
            <div className="rl-replay__obs-track">
              <div
                className={cn('rl-replay__obs-bar', n >= 0 ? 'rl-replay__obs-bar--pos' : 'rl-replay__obs-bar--neg')}
                style={{ width: `${Math.max(2, pct)}%` }}
              />
            </div>
            <span className="rl-replay__obs-val num-mono">{n.toFixed(3)}</span>
          </div>
        );
      })}
      {obs.length > maxDims && (
        <p className="text-[0.55rem] text-muted-foreground mt-1">+{obs.length - maxDims} dims</p>
      )}
    </div>
  );
}

export default function RlEpisodeReplay({
  rlData,
  compact = false,
  className,
}) {
  const steps = useMemo(
    () => (Array.isArray(rlData?.episode_steps) ? rlData.episode_steps : []),
    [rlData],
  );
  const rootRef = useRef(null);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState('1x');

  useEffect(() => {
    setIndex(0);
    setPlaying(false);
  }, [steps]);

  useEffect(() => {
    if (!playing || steps.length < 2) return undefined;
    const ms = SPEED_MS[speed] ?? 120;
    const id = window.setInterval(() => {
      setIndex((i) => (i + 1 >= steps.length ? 0 : i + 1));
    }, ms);
    return () => window.clearInterval(id);
  }, [playing, steps.length, speed]);

  const maxIdx = Math.max(steps.length - 1, 0);
  const seek = useCallback((i) => {
    setPlaying(false);
    setIndex(Math.max(0, Math.min(maxIdx, i)));
  }, [maxIdx]);

  const jumpTrade = useCallback(() => {
    const next = findNextTradeAction(steps, index);
    if (next >= 0) seek(next);
  }, [steps, index, seek]);

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return undefined;
    const onKey = (e) => {
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        seek(index - 1);
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        seek(index + 1);
      } else if (e.key === ' ') {
        e.preventDefault();
        setPlaying((p) => !p);
      } else if (e.key === 'n' || e.key === 'N') {
        e.preventDefault();
        jumpTrade();
      }
    };
    el.addEventListener('keydown', onKey);
    return () => el.removeEventListener('keydown', onKey);
  }, [index, seek, jumpTrade]);

  const posSparkIdx = useMemo(
    () => sparkIndexForStep(index, steps.length, rlData?.position_trajectory?.length || 0),
    [rlData?.position_trajectory, steps.length, index],
  );

  const rewSparkIdx = useMemo(
    () => sparkIndexForStep(index, steps.length, rlData?.reward_accumulation?.length || 0),
    [rlData?.reward_accumulation, steps.length, index],
  );

  if (!steps.length && !rlData?.position_trajectory?.length) {
    return (
      <section className={cn('rl-replay rl-replay--empty', className)}>
        <p className="algo-backtest-table-scroll__caption mb-1">Episode replay</p>
        <p className="text-xs text-muted-foreground">
          Per-step RL observations appear here when the backtest includes{' '}
          <code className="mx-0.5">rl_data.episode_steps</code>.
        </p>
      </section>
    );
  }

  const step = steps[index] || {};
  const label = actionLabel(step.action);
  const tone = actionTone(step.action);

  const seekFromSpark = (sparkLen) => (sparkIdx) => {
    if (!steps.length || sparkLen < 2) return;
    const ratio = sparkIdx / (sparkLen - 1);
    seek(Math.round(ratio * maxIdx));
  };

  return (
    <section
      ref={rootRef}
      tabIndex={0}
      className={cn('rl-replay', compact && 'rl-replay--compact', className)}
      aria-label="RL episode replay"
    >
      <div className="rl-replay__header">
        <p className="algo-backtest-table-scroll__caption mb-0">Episode replay</p>
        <span className="text-[0.5rem] text-muted-foreground hidden sm:inline">
          ← → scrub · Space play · N next trade
        </span>
      </div>

      {(rlData?.position_trajectory?.length > 1 || rlData?.reward_accumulation?.length > 1) && (
        <div className="rl-replay__sparks mb-2">
          {rlData?.position_trajectory?.length > 1 && (
            <div>
              <span className="text-[0.5rem] uppercase text-muted-foreground">Position</span>
              <Sparkline
                values={rlData.position_trajectory}
                tone="accent"
                markerIndex={posSparkIdx}
                onSeek={seekFromSpark(rlData.position_trajectory.length)}
              />
            </div>
          )}
          {rlData?.reward_accumulation?.length > 1 && (
            <div>
              <span className="text-[0.5rem] uppercase text-muted-foreground">Reward Σ</span>
              <Sparkline
                values={rlData.reward_accumulation}
                tone="up"
                markerIndex={rewSparkIdx}
                onSeek={seekFromSpark(rlData.reward_accumulation.length)}
              />
            </div>
          )}
        </div>
      )}

      {steps.length > 0 && (
        <>
          <div className="rl-replay__controls">
            <Button
              type="button"
              variant="ghost"
              size="xs"
              className="h-6 w-6 p-0"
              onClick={() => seek(index - 1)}
              disabled={index <= 0}
              aria-label="Previous step"
            >
              <ChevronLeft size={14} />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="xs"
              className="h-6 w-6 p-0"
              onClick={() => setPlaying((p) => !p)}
              aria-label={playing ? 'Pause' : 'Play'}
            >
              {playing ? <Pause size={14} /> : <Play size={14} />}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="xs"
              className="h-6 w-6 p-0"
              onClick={() => seek(index + 1)}
              disabled={index >= maxIdx}
              aria-label="Next step"
            >
              <ChevronRight size={14} />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="xs"
              className="h-6 px-1.5 text-[0.55rem] gap-0.5"
              onClick={jumpTrade}
              aria-label="Next trade action"
              title="Jump to next BUY/SELL"
            >
              <SkipForward size={12} />
              {!compact && 'Trade'}
            </Button>
            <div className="rl-replay__speeds" role="group" aria-label="Playback speed">
              {Object.keys(SPEED_MS).map((s) => (
                <button
                  key={s}
                  type="button"
                  className={cn('rl-replay__speed', speed === s && 'rl-replay__speed--active')}
                  onClick={() => setSpeed(s)}
                >
                  {s}
                </button>
              ))}
            </div>
            <input
              type="range"
              className="rl-replay__slider"
              min={0}
              max={maxIdx}
              value={index}
              onChange={(e) => seek(Number(e.target.value))}
              aria-label="Episode scrubber"
            />
            <span className="num-mono text-[0.6rem] text-muted-foreground whitespace-nowrap">
              {index + 1}/{steps.length}
            </span>
          </div>

          <div className="rl-replay__step-meta num-mono text-[0.65rem] mb-2">
            <span>bar {step.bar_index ?? '—'}</span>
            <span className={cn('rl-replay__action-badge', `rl-replay__action-badge--${tone}`)}>
              {label}
            </span>
            <span>reward {step.reward != null ? Number(step.reward).toFixed(4) : '—'}</span>
            <span>pos {step.position != null ? Number(step.position).toFixed(2) : '—'}</span>
            {step.info?.confidence != null && (
              <span>conf {Number(step.info.confidence).toFixed(2)}</span>
            )}
          </div>

          {!compact && <ObsPreview observation={step.observation} />}
        </>
      )}
    </section>
  );
}
