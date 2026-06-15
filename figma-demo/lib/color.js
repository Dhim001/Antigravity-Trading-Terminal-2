/**
 * Color helpers for use_figma scripts (Figma uses 0–1 RGBA).
 * Used by scripts in figma-demo/scripts/
 */

/** @param {string} hex #rrggbb */
export function hexToRgb(hex) {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
  return {
    r: ((n >> 16) & 255) / 255,
    g: ((n >> 8) & 255) / 255,
    b: (n & 255) / 255,
  };
}

/** @param {string} css rgba(r,g,b,a) or #hex */
export function parseColor(css) {
  if (css.startsWith('#')) return { ...hexToRgb(css), a: 1 };
  const m = css.match(/rgba?\(([^)]+)\)/);
  if (!m) return { r: 0, g: 0, b: 0, a: 1 };
  const parts = m[1].split(',').map((s) => s.trim());
  return {
    r: Number(parts[0]) / 255,
    g: Number(parts[1]) / 255,
    b: Number(parts[2]) / 255,
    a: parts[3] !== undefined ? Number(parts[3]) : 1,
  };
}

/** Inline copy for use_figma (no imports in Figma sandbox) */
export const PARSE_COLOR_FN = `
function parseColor(css) {
  if (css.startsWith('#')) {
    const h = css.replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
    return { r: ((n >> 16) & 255) / 255, g: ((n >> 8) & 255) / 255, b: (n & 255) / 255, a: 1 };
  }
  const m = css.match(/rgba?\\(([^)]+)\\)/);
  if (!m) return { r: 0, g: 0, b: 0, a: 1 };
  const parts = m[1].split(',').map(s => s.trim());
  return {
    r: Number(parts[0]) / 255,
    g: Number(parts[1]) / 255,
    b: Number(parts[2]) / 255,
    a: parts[3] !== undefined ? Number(parts[3]) : 1,
  };
}
`;
