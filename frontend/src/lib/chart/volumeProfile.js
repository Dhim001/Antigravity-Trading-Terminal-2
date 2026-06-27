/**
 * Volume Profile (VPVR) computation.
 *
 * Distributes each bar's volume across price bins between its low and high, then
 * derives the Point of Control (POC) and the Value Area (VAH/VAL) covering a
 * configurable share of total volume (70% by convention).
 */

const DEFAULT_BINS = 24;
const DEFAULT_VALUE_AREA_PCT = 0.7;

/**
 * @param {Array} bars  {high, low, close, volume}
 * @param {{ bins?: number, valueAreaPct?: number }} [opts]
 * @returns {{
 *   bins: Array<{ priceLow:number, priceHigh:number, mid:number, volume:number }>,
 *   poc: number|null, vah: number|null, val: number|null,
 *   maxVolume: number, totalVolume: number, priceMin:number, priceMax:number
 * }}
 */
export function computeVolumeProfile(bars, opts = {}) {
  const binCount = Math.max(4, Math.floor(opts.bins || DEFAULT_BINS));
  const valueAreaPct = opts.valueAreaPct ?? DEFAULT_VALUE_AREA_PCT;

  const empty = {
    bins: [], poc: null, vah: null, val: null,
    maxVolume: 0, totalVolume: 0, priceMin: 0, priceMax: 0,
  };
  if (!Array.isArray(bars) || bars.length === 0) return empty;

  let priceMin = Infinity;
  let priceMax = -Infinity;
  for (const b of bars) {
    const hi = Number(b.high);
    const lo = Number(b.low);
    if (Number.isFinite(hi)) priceMax = Math.max(priceMax, hi);
    if (Number.isFinite(lo)) priceMin = Math.min(priceMin, lo);
  }
  if (!Number.isFinite(priceMin) || !Number.isFinite(priceMax) || priceMax <= priceMin) {
    return empty;
  }

  const range = priceMax - priceMin;
  const binSize = range / binCount;
  const bins = Array.from({ length: binCount }, (_, i) => ({
    priceLow: priceMin + i * binSize,
    priceHigh: priceMin + (i + 1) * binSize,
    mid: priceMin + (i + 0.5) * binSize,
    volume: 0,
  }));

  for (const b of bars) {
    const hi = Number(b.high);
    const lo = Number(b.low);
    const vol = Number(b.volume) || 0;
    if (vol <= 0 || !Number.isFinite(hi) || !Number.isFinite(lo)) continue;

    const loIdx = clampBin(Math.floor((lo - priceMin) / binSize), binCount);
    const hiIdx = clampBin(Math.floor((hi - priceMin) / binSize), binCount);
    const span = hiIdx - loIdx + 1;
    const perBin = vol / span;
    for (let i = loIdx; i <= hiIdx; i++) bins[i].volume += perBin;
  }

  let maxVolume = 0;
  let pocIdx = 0;
  let totalVolume = 0;
  bins.forEach((bin, i) => {
    totalVolume += bin.volume;
    if (bin.volume > maxVolume) {
      maxVolume = bin.volume;
      pocIdx = i;
    }
  });

  const { vahIdx, valIdx } = computeValueArea(bins, pocIdx, totalVolume, valueAreaPct);

  return {
    bins,
    poc: bins[pocIdx]?.mid ?? null,
    vah: bins[vahIdx]?.priceHigh ?? null,
    val: bins[valIdx]?.priceLow ?? null,
    maxVolume,
    totalVolume,
    priceMin,
    priceMax,
  };
}

/**
 * Build ECharts `graphic` children for a volume profile, drawn as horizontal
 * bars anchored to the right edge of the plot extending left by volume share.
 *
 * @param {ReturnType<typeof computeVolumeProfile>} profile
 * @param {{
 *   plotRight:number, maxWidthPx:number, priceToY:(p:number)=>number|null,
 *   binPx:number, barColor?:string, pocColor?:string, vaColor?:string,
 *   showValueArea?:boolean
 * }} geom
 */
export function volumeProfileGraphic(profile, geom) {
  if (!profile || !profile.bins?.length || !geom) return [];
  const {
    plotRight, maxWidthPx, priceToY, binPx,
    barColor = 'rgba(96,165,250,0.28)',
    pocColor = '#f59e0b',
    vaColor = 'rgba(168,85,247,0.18)',
    showValueArea = true,
  } = geom;
  if (!(maxWidthPx > 0) || profile.maxVolume <= 0) return [];

  const children = [];
  const h = Math.max(1, binPx - 1);
  profile.bins.forEach((bin, i) => {
    const y = priceToY(bin.mid);
    if (y == null) return;
    const w = (bin.volume / profile.maxVolume) * maxWidthPx;
    if (w <= 0) return;
    const inVa = showValueArea
      && profile.val != null && profile.vah != null
      && bin.mid >= profile.val && bin.mid <= profile.vah;
    children.push({
      type: 'rect',
      id: `vp-bin-${i}`,
      shape: { x: plotRight - w, y: y - h / 2, width: w, height: h },
      style: { fill: inVa ? vaColor : barColor },
      silent: true,
      z: 2,
    });
  });

  if (profile.poc != null) {
    const y = priceToY(profile.poc);
    if (y != null) {
      children.push({
        type: 'line',
        id: 'vp-poc',
        shape: { x1: plotRight - maxWidthPx, y1: y, x2: plotRight, y2: y },
        style: { stroke: pocColor, lineWidth: 1.5 },
        silent: true,
        z: 3,
      });
      children.push({
        type: 'text',
        id: 'vp-poc-text',
        style: {
          text: 'POC', x: plotRight - maxWidthPx, y: y - 11,
          fill: pocColor, font: '10px sans-serif',
        },
        silent: true,
        z: 3,
      });
    }
  }
  return children;
}

function clampBin(idx, binCount) {
  if (idx < 0) return 0;
  if (idx >= binCount) return binCount - 1;
  return idx;
}

/**
 * Expand outward from the POC bin, always adding the higher-volume neighbor,
 * until the accumulated volume covers valueAreaPct of total.
 */
function computeValueArea(bins, pocIdx, totalVolume, valueAreaPct) {
  let lower = pocIdx;
  let upper = pocIdx;
  let acc = bins[pocIdx]?.volume || 0;
  const target = totalVolume * valueAreaPct;

  while (acc < target && (lower > 0 || upper < bins.length - 1)) {
    const belowVol = lower > 0 ? bins[lower - 1].volume : -1;
    const aboveVol = upper < bins.length - 1 ? bins[upper + 1].volume : -1;
    if (aboveVol >= belowVol) {
      upper += 1;
      acc += Math.max(aboveVol, 0);
    } else {
      lower -= 1;
      acc += Math.max(belowVol, 0);
    }
  }
  return { vahIdx: upper, valIdx: lower };
}
