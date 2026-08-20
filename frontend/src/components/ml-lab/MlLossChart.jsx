import { useCallback, useId, useMemo, useRef, useState } from 'react';

export function normalizeCurveHistory(history, trainHistory, metrics) {
  const trainRows = Array.isArray(trainHistory) ? trainHistory : [];
  if (trainRows.some((h) => h && h.return_pct != null)) {
    return {
      mode: 'returns',
      title: 'Episode returns',
      rows: trainRows
        .filter((h) => h && h.return_pct != null)
        .map((h, i) => ({
          i: h.episode ?? i + 1,
          primary: Number(h.return_pct),
        })),
      primaryLabel: 'return',
      secondaryLabel: null,
    };
  }

  const lossRows = Array.isArray(history)
    ? history.filter((h) => h && (h.val_loss != null || h.train_loss != null || h.return_pct != null))
    : [];
  if (lossRows.some((h) => h.return_pct != null) && !lossRows.some((h) => h.train_loss != null)) {
    return {
      mode: 'returns',
      title: 'Episode returns',
      rows: lossRows.map((h, i) => ({
        i: h.episode ?? h.epoch ?? i + 1,
        primary: Number(h.return_pct),
      })),
      primaryLabel: 'return',
      secondaryLabel: null,
    };
  }
  if (lossRows.length >= 2) {
    return {
      mode: 'loss',
      title: 'Training curve',
      rows: lossRows.map((h, i) => ({
        i: h.epoch ?? i + 1,
        primary: h.train_loss != null ? Number(h.train_loss) : null,
        secondary: h.val_loss != null ? Number(h.val_loss) : null,
      })),
      primaryLabel: 'train',
      secondaryLabel: 'val',
    };
  }

  const last10 = Array.isArray(metrics?.last_10_returns) ? metrics.last_10_returns : [];
  if (last10.length >= 2) {
    return {
      mode: 'returns',
      title: 'Recent episode returns',
      rows: last10.map((v, i) => ({ i: i + 1, primary: Number(v) })),
      primaryLabel: 'return',
      secondaryLabel: null,
    };
  }
  return null;
}

export function formatSignedPct(n, digits = 2) {
  if (!Number.isFinite(n)) return '—';
  const abs = Math.abs(n).toFixed(digits);
  if (n > 0) return `+${abs}%`;
  if (n < 0) return `−${abs}%`;
  return `${Number(0).toFixed(digits)}%`;
}

export function rollingMean(values, window = 5) {
  const out = [];
  let sum = 0;
  const q = [];
  for (const v of values) {
    if (!Number.isFinite(v)) {
      out.push(null);
      continue;
    }
    q.push(v);
    sum += v;
    if (q.length > window) sum -= q.shift();
    out.push(sum / q.length);
  }
  return out;
}

export function summarizeReturnCurve(rows, { recentWindow = 10 } = {}) {
  const vals = (rows || [])
    .map((r) => Number(r?.primary))
    .filter((n) => Number.isFinite(n));
  if (!vals.length) return null;
  const n = vals.length;
  const last = vals[n - 1];
  const mean = vals.reduce((a, b) => a + b, 0) / n;
  const recentN = Math.min(recentWindow, n);
  const recent = vals.slice(-recentN);
  const recentMean = recent.reduce((a, b) => a + b, 0) / recent.length;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const wins = vals.filter((v) => v > 0).length;
  const third = Math.max(1, Math.floor(n / 3));
  const early = vals.slice(0, third).reduce((a, b) => a + b, 0) / third;
  const late = vals.slice(-third).reduce((a, b) => a + b, 0) / third;
  const delta = late - early;
  let trend = 'flat';
  if (delta > 0.15) trend = 'improving';
  else if (delta < -0.15) trend = 'worsening';
  if (Math.abs(recentMean) < 0.05 && Math.abs(max - min) > 0.5 && Math.abs(late) < 0.15) {
    trend = 'flat';
  }
  return {
    n,
    last,
    mean,
    recentMean,
    recentN,
    min,
    max,
    wins,
    winRate: wins / n,
    trend,
  };
}

export function hoverIndexFromX(x, left, right, count) {
  if (count <= 1) return 0;
  const t = (x - left) / Math.max(right - left, 1e-9);
  return Math.min(count - 1, Math.max(0, Math.round(t * (count - 1))));
}

/** Ignore pointer jitter that Windows/Electron fires during a wheel tick. */
export const WHEEL_HOVER_LOCK_MS = 160;

function signClass(n) {
  if (!Number.isFinite(n) || n === 0) return 'ml-training__stat--flat';
  return n > 0 ? 'ml-training__stat--up' : 'ml-training__stat--down';
}

function StatChip({ label, value, tone }) {
  return (
    <span className={`ml-training__stat ${tone || ''}`}>
      <span className="ml-training__stat-label">{label}</span>
      <span className="ml-training__stat-value num-mono">{value}</span>
    </span>
  );
}

export function LossHistoryChart({ history, trainHistory, metrics }) {
  const curve = normalizeCurveHistory(history, trainHistory, metrics);
  const clipId = useId().replace(/:/g, '');
  const [hover, setHover] = useState(null);
  const svgRef = useRef(null);
  const wheelLockUntilRef = useRef(0);

  const layout = useMemo(() => {
    if (!curve || curve.rows.length < 2) return null;
    const vals = curve.rows.flatMap((r) => [r.primary, r.secondary].filter((n) => Number.isFinite(n)));
    if (!vals.length) return null;
    let min = Math.min(...vals);
    let max = Math.max(...vals);
    const pad = Math.max(Math.abs(max - min) * 0.08, 0.05);
    if (curve.mode === 'returns') {
      if (min > 0) min = 0;
      if (max < 0) max = 0;
    }
    const yMin = min - pad;
    const yMax = max + pad;
    const span = Math.max(yMax - yMin, 1e-9);
    const isReturns = curve.mode === 'returns';
    const w = 360;
    const h = isReturns ? 96 : 72;
    const left = 2;
    const right = w - 2;
    const top = 6;
    const bottom = h - 8;
    const innerH = bottom - top;
    const xAt = (i) => left + (i / Math.max(curve.rows.length - 1, 1)) * (right - left);
    const yAt = (v) => bottom - ((v - yMin) / span) * innerH;
    return {
      w, h, left, right, top, bottom, yMin, yMax, span, xAt, yAt, isReturns,
    };
  }, [curve]);

  const stats = useMemo(
    () => (curve?.mode === 'returns' ? summarizeReturnCurve(curve.rows) : null),
    [curve],
  );

  const sma = useMemo(() => {
    if (!curve || curve.mode !== 'returns') return [];
    return rollingMean(curve.rows.map((r) => r.primary), 5);
  }, [curve]);

  const hoverFromClientX = useCallback((clientX) => {
    const svg = svgRef.current;
    if (!svg || !layout || !curve?.rows?.length) return null;
    const box = svg.getBoundingClientRect();
    if (!box.width) return null;
    const x = ((clientX - box.left) / box.width) * layout.w;
    const idx = hoverIndexFromX(x, layout.left, layout.right, curve.rows.length);
    const row = curve.rows[idx];
    if (!row || !Number.isFinite(row.primary)) return null;
    return {
      i: idx,
      episode: row.i,
      value: row.primary,
      sma: sma[idx],
      x: layout.xAt(idx),
      y: layout.yAt(row.primary),
    };
  }, [curve, layout, sma]);

  if (!curve || curve.rows.length < 2 || !layout) {
    return (
      <div className="ml-training__loss ml-training__loss--empty">
        <p className="ml-training__subsection-label">Training curve</p>
        <p className="text-[0.65rem] text-muted-foreground">
          No epoch / episode history yet. Run Trigger retrain to populate the curve.
        </p>
      </div>
    );
  }

  const { w, h, left, right, yMin, yMax, xAt, yAt, isReturns } = layout;
  const zeroInRange = isReturns && yMin < 0 && yMax > 0;
  const yZero = yAt(0);

  const toPath = (values) => {
    const pts = values
      .map((v, i) => (Number.isFinite(v) ? `${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}` : null))
      .filter(Boolean);
    if (pts.length < 2) return null;
    return pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p}`).join(' ');
  };

  const primaryVals = curve.rows.map((r) => r.primary);
  const primaryD = toPath(primaryVals);
  const secondaryD = curve.secondaryLabel ? toPath(curve.rows.map((r) => r.secondary)) : null;
  const smaD = isReturns ? toPath(sma) : null;

  const areaToZero = (() => {
    if (!isReturns || !primaryD || !zeroInRange) return null;
    const pts = curve.rows
      .map((r, i) => (Number.isFinite(r.primary) ? [xAt(i), yAt(r.primary)] : null))
      .filter(Boolean);
    if (pts.length < 2) return null;
    const firstX = pts[0][0].toFixed(1);
    const lastX = pts[pts.length - 1][0].toFixed(1);
    const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
    return `${line} L${lastX},${yZero.toFixed(1)} L${firstX},${yZero.toFixed(1)} Z`;
  })();

  const last = curve.rows[curve.rows.length - 1];
  const fmtY = (n) => (isReturns ? formatSignedPct(n) : Number(n).toFixed(4));

  const onPointerMove = (evt) => {
    if (evt.pointerType === 'touch') return;
    if (performance.now() < wheelLockUntilRef.current) return;
    setHover(hoverFromClientX(evt.clientX));
  };

  const onWheel = () => {
    // Vertical wheel/trackpad must scroll the ML Lab panel, not scrub episodes.
    // Windows also emits mousemove during wheel — lock hover so the crosshair
    // does not stay glued to the pointer while the panel moves.
    wheelLockUntilRef.current = performance.now() + WHEEL_HOVER_LOCK_MS;
    setHover(null);
  };

  return (
    <div className={`ml-training__loss${isReturns ? ' ml-training__loss--returns' : ''}`}>
      <div className="ml-training__loss-head">
        <p className="ml-training__subsection-label">{curve.title}</p>
        {isReturns && stats ? (
          <span className={`ml-training__trend ml-training__trend--${stats.trend}`}>
            {stats.trend}
          </span>
        ) : (
          <span className="text-[0.5rem] text-muted-foreground">
            {curve.primaryLabel && (
              <span className="ml-training__loss-legend ml-training__loss-legend--train">
                {curve.primaryLabel}
              </span>
            )}
            {curve.secondaryLabel && (
              <>
                {' · '}
                <span className="ml-training__loss-legend ml-training__loss-legend--val">
                  {curve.secondaryLabel}
                </span>
              </>
            )}
          </span>
        )}
      </div>

      {isReturns && stats && (
        <div className="ml-training__loss-stats" aria-label="Episode return summary">
          <StatChip label="last" value={formatSignedPct(stats.last)} tone={signClass(stats.last)} />
          <StatChip label="mean" value={formatSignedPct(stats.mean)} tone={signClass(stats.mean)} />
          <StatChip
            label={`last ${stats.recentN}`}
            value={formatSignedPct(stats.recentMean)}
            tone={signClass(stats.recentMean)}
          />
          <StatChip label="best" value={formatSignedPct(stats.max)} tone={signClass(stats.max)} />
          <StatChip label="worst" value={formatSignedPct(stats.min)} tone={signClass(stats.min)} />
          <StatChip
            label="win"
            value={`${Math.round(stats.winRate * 100)}%`}
            tone={stats.winRate >= 0.5 ? 'ml-training__stat--up' : 'ml-training__stat--down'}
          />
        </div>
      )}

      <div className="ml-training__loss-plot" onWheel={onWheel}>
        <div className="ml-training__loss-ylabels num-mono" aria-hidden>
          <span>{fmtY(yMax)}</span>
          <span>{fmtY(yMin)}</span>
        </div>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${w} ${h}`}
          className="ml-training__loss-svg"
          aria-label={curve.title}
          onPointerMove={onPointerMove}
          onPointerLeave={() => setHover(null)}
        >
          {zeroInRange && (
            <>
              <defs>
                <clipPath id={`${clipId}-up`}>
                  <rect x={left} y={0} width={right - left} height={Math.max(yZero, 0)} />
                </clipPath>
                <clipPath id={`${clipId}-dn`}>
                  <rect x={left} y={yZero} width={right - left} height={Math.max(h - yZero, 0)} />
                </clipPath>
              </defs>
              <line
                x1={left}
                y1={yZero}
                x2={right}
                y2={yZero}
                className="ml-training__loss-zero"
              />
              <text
                x={right - 2}
                y={yZero - 3}
                textAnchor="end"
                className="ml-training__loss-zero-label"
              >
                0%
              </text>
              {areaToZero && (
                <>
                  <path d={areaToZero} className="ml-training__loss-fill ml-training__loss-fill--up" clipPath={`url(#${clipId}-up)`} />
                  <path d={areaToZero} className="ml-training__loss-fill ml-training__loss-fill--down" clipPath={`url(#${clipId}-dn)`} />
                </>
              )}
            </>
          )}
          {!zeroInRange && (
            <line x1={left} y1={h / 2} x2={right} y2={h / 2} className="ml-training__loss-grid" />
          )}
          {smaD && (
            <path d={smaD} className="ml-training__loss-path ml-training__loss-path--sma" fill="none" />
          )}
          {primaryD && (
            <path
              d={primaryD}
              className={`ml-training__loss-path ${isReturns ? 'ml-training__loss-path--return' : 'ml-training__loss-path--train'}`}
              fill="none"
            />
          )}
          {secondaryD && (
            <path d={secondaryD} className="ml-training__loss-path ml-training__loss-path--val" fill="none" />
          )}
          {isReturns && Number.isFinite(last?.primary) && (
            <circle
              cx={xAt(curve.rows.length - 1)}
              cy={yAt(last.primary)}
              r={3}
              className={`ml-training__loss-dot ${last.primary >= 0 ? 'ml-training__loss-dot--up' : 'ml-training__loss-dot--down'}`}
            />
          )}
          {hover && (
            <>
              <line
                x1={hover.x}
                y1={layout.top}
                x2={hover.x}
                y2={layout.bottom}
                className="ml-training__loss-cross"
              />
              <circle
                cx={hover.x}
                cy={hover.y}
                r={3.5}
                className={`ml-training__loss-dot ${hover.value >= 0 ? 'ml-training__loss-dot--up' : 'ml-training__loss-dot--down'}`}
              />
            </>
          )}
        </svg>
      </div>

      <div className="ml-training__loss-footer">
        <span className="num-mono">
          {curve.rows[0]?.i ?? 1}
        </span>
        <span className="ml-training__loss-footer-mid num-mono">
          {hover
            ? `ep ${hover.episode}  ·  ${formatSignedPct(hover.value)}${Number.isFinite(hover.sma) ? `  ·  sma ${formatSignedPct(hover.sma)}` : ''}`
            : `${curve.rows.length} ${isReturns ? 'episodes' : 'epochs'}${Number.isFinite(last?.primary) ? `  ·  last ${fmtY(last.primary)}` : ''}${Number.isFinite(last?.secondary) ? `  ·  val ${fmtY(last.secondary)}` : ''}`}
        </span>
        <span className="num-mono">
          {curve.rows[curve.rows.length - 1]?.i ?? curve.rows.length}
        </span>
      </div>
      {isReturns && (
        <p className="ml-training__loss-hint">
          Green = profit · red = loss · dashed = 5-ep average. Hover a point for that episode.
        </p>
      )}
    </div>
  );
}
