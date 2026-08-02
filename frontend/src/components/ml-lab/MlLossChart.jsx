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

export function LossHistoryChart({ history, trainHistory, metrics }) {
  const curve = normalizeCurveHistory(history, trainHistory, metrics);
  if (!curve || curve.rows.length < 2) {
    return (
      <div className="ml-training__loss ml-training__loss--empty">
        <p className="ml-training__subsection-label">Training curve</p>
        <p className="text-[0.65rem] text-muted-foreground">
          No epoch / episode history yet. Run Trigger retrain to populate the curve.
        </p>
      </div>
    );
  }

  const vals = curve.rows.flatMap((r) => [r.primary, r.secondary].filter((n) => Number.isFinite(n)));
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const pad = Math.max(Math.abs(max - min) * 0.08, 1e-6);
  const yMin = min - pad;
  const yMax = max + pad;
  const span = Math.max(yMax - yMin, 1e-9);
  const w = 360;
  const h = 72;
  const left = 2;
  const right = w - 2;

  const toPath = (key) => {
    const pts = curve.rows
      .map((r, i) => {
        const v = Number(r[key]);
        if (!Number.isFinite(v)) return null;
        const x = left + (i / Math.max(curve.rows.length - 1, 1)) * (right - left);
        const y = h - ((v - yMin) / span) * (h - 10) - 5;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .filter(Boolean);
    if (pts.length < 2) return null;
    return pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p}`).join(' ');
  };

  const primaryD = toPath('primary');
  const secondaryD = curve.secondaryLabel ? toPath('secondary') : null;
  const last = curve.rows[curve.rows.length - 1];
  const fmtY = (n) => (curve.mode === 'returns'
    ? `${Number(n).toFixed(2)}%`
    : Number(n).toFixed(4));

  return (
    <div className="ml-training__loss">
      <div className="ml-training__loss-head">
        <p className="ml-training__subsection-label">{curve.title}</p>
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
      </div>
      <div className="ml-training__loss-plot">
        <div className="ml-training__loss-ylabels num-mono" aria-hidden>
          <span>{fmtY(yMax)}</span>
          <span>{fmtY(yMin)}</span>
        </div>
        <svg viewBox={`0 0 ${w} ${h}`} className="ml-training__loss-svg" aria-label={curve.title}>
          <line x1={left} y1={h / 2} x2={right} y2={h / 2} className="ml-training__loss-grid" />
          {primaryD && (
            <path d={primaryD} className="ml-training__loss-path ml-training__loss-path--train" fill="none" />
          )}
          {secondaryD && (
            <path d={secondaryD} className="ml-training__loss-path ml-training__loss-path--val" fill="none" />
          )}
        </svg>
      </div>
      <p className="ml-training__loss-footer num-mono">
        {curve.rows.length} {curve.mode === 'returns' ? 'episodes' : 'epochs'}
        {Number.isFinite(last?.primary) ? ` · last ${fmtY(last.primary)}` : ''}
        {Number.isFinite(last?.secondary) ? ` · val ${fmtY(last.secondary)}` : ''}
      </p>
    </div>
  );
}
