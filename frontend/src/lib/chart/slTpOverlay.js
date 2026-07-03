/**
 * Draggable stop-loss / take-profit chart overlay (Phase 6).
 */

export const SL_TP_LINE_HIT_PX = 8;
export const SL_TP_HANDLE_W = 14;
export const SL_TP_HANDLE_H = 10;

const SL_COLOR = '#ef4444';
const TP_COLOR = '#10b981';
const SL_DRAFT = 'rgba(239,68,68,0.55)';
const TP_DRAFT = 'rgba(16,185,129,0.55)';

/**
 * @param {'BUY'|'SELL'} side
 * @param {number} refPrice entry / last price
 * @param {'sl'|'tp'} kind
 */
export function clampSlTpPrice(price, side, refPrice, kind) {
  const p = Number(price);
  const ref = Number(refPrice);
  if (!Number.isFinite(p) || !Number.isFinite(ref) || ref <= 0) return null;
  const isBuy = (side || 'BUY').toUpperCase() === 'BUY';
  if (kind === 'sl') {
    if (isBuy && p >= ref) return ref * 0.999;
    if (!isBuy && p <= ref) return ref * 1.001;
  } else {
    if (isBuy && p <= ref) return ref * 1.001;
    if (!isBuy && p >= ref) return ref * 0.999;
  }
  return p;
}

/**
 * @returns {'sl'|'tp'|'draft-sl'|'draft-tp'|null}
 */
export function hitTestSlTp(mouseY, lines, hitPx = SL_TP_LINE_HIT_PX) {
  if (!Number.isFinite(mouseY) || !lines?.length) return null;
  let best = null;
  let bestDist = hitPx + 1;
  for (const line of lines) {
    if (line.y == null || !Number.isFinite(line.y)) continue;
    const d = Math.abs(mouseY - line.y);
    if (d <= hitPx && d < bestDist) {
      bestDist = d;
      best = line.id;
    }
  }
  return best;
}

function lineGraphic(id, y, left, right, color, dashed, label) {
  return {
    type: 'line',
    id: `${id}-line`,
    shape: { x1: left, y1: y, x2: right, y2: y },
    style: {
      stroke: color,
      lineWidth: dashed ? 1.5 : 2,
      lineDash: dashed ? [6, 4] : undefined,
    },
    silent: true,
    z: 90,
  };
}

function handleGraphic(id, y, right, color, label) {
  return {
    type: 'rect',
    id: `${id}-handle`,
    shape: {
      x: right - SL_TP_HANDLE_W - 4,
      y: y - SL_TP_HANDLE_H / 2,
      width: SL_TP_HANDLE_W,
      height: SL_TP_HANDLE_H,
      r: 2,
    },
    style: { fill: color, stroke: '#fff', lineWidth: 1 },
    z: 100,
    cursor: 'ns-resize',
  };
}

function labelGraphic(id, y, right, text, color) {
  return {
    type: 'text',
    id: `${id}-label`,
    style: {
      x: right - SL_TP_HANDLE_W - 8,
      y: y - 14,
      text,
      fill: color,
      fontSize: 10,
      fontWeight: 'bold',
      textAlign: 'right',
    },
    silent: true,
    z: 95,
  };
}

/**
 * Build flat ECharts graphic elements for SL/TP levels.
 * Returns { elements, hitLines } where hitLines is used for drag hit-test.
 */
export function buildSlTpGraphic({
  priceToY,
  plotLeft,
  plotRight,
  dec = 2,
  live = {},
  draft = {},
}) {
  const elements = [];
  const hitLines = [];

  const addLevel = (id, price, { color, dashed, label, fmt }) => {
    if (price == null || !(price > 0)) return;
    const y = priceToY(price);
    if (y == null || !Number.isFinite(y)) return;
    hitLines.push({ id, y, price });
    elements.push(lineGraphic(id, y, plotLeft, plotRight, color, dashed, label));
    elements.push(handleGraphic(id, y, plotRight, color, label));
    elements.push(labelGraphic(id, y, plotRight, `${label} ${fmt(price)}`, color));
  };

  addLevel('sl', live.stop_loss_price, {
    color: SL_COLOR,
    dashed: false,
    label: 'SL',
    fmt: (p) => p.toFixed(dec),
  });
  addLevel('tp', live.take_profit_price, {
    color: TP_COLOR,
    dashed: false,
    label: 'TP',
    fmt: (p) => p.toFixed(dec),
  });
  addLevel('draft-sl', draft.stop_loss_price, {
    color: SL_DRAFT,
    dashed: true,
    label: 'SL draft',
    fmt: (p) => p.toFixed(dec),
  });
  addLevel('draft-tp', draft.take_profit_price, {
    color: TP_DRAFT,
    dashed: true,
    label: 'TP draft',
    fmt: (p) => p.toFixed(dec),
  });

  return { elements, hitLines };
}

export function isDraftTarget(target) {
  return target === 'draft-sl' || target === 'draft-tp';
}

export function kindFromTarget(target) {
  if (target === 'sl' || target === 'draft-sl') return 'sl';
  if (target === 'tp' || target === 'draft-tp') return 'tp';
  return null;
}
