/**
 * Figma use_figma script — Step 1: Create variable collections (Light + Dark modes)
 *
 * Run via Figma MCP when connected:
 *   use_figma({ fileKey: "<file_key>", code: <contents>, skillNames: "figma-use,figma-generate-library" })
 *
 * Source: figma-demo/tokens.json
 */
export const script = `
${`function parseColor(css) {
  if (css.startsWith('#')) {
    const h = css.replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
    return { r: ((n >> 16) & 255) / 255, g: ((n >> 8) & 255) / 255, b: (n & 255) / 255, a: 1 };
  }
  const m = css.match(/rgba?\\(([^)]+)\\)/);
  if (!m) return { r: 0, g: 0, b: 0, a: 1 };
  const parts = m[1].split(',').map(s => s.trim());
  return { r: Number(parts[0]) / 255, g: Number(parts[1]) / 255, b: Number(parts[2]) / 255, a: parts[3] !== undefined ? Number(parts[3]) : 1 };
}`}

const TOKEN_DATA = ${JSON.stringify({
  'color/surface': {
    'bg/primary': { Light: '#f1f5f9', Dark: '#070b14' },
    'bg/secondary': { Light: '#e2e8f0', Dark: '#0a0f1e' },
    'bg/tertiary': { Light: '#f8fafc', Dark: '#0f1629' },
    'bg/card': { Light: '#ffffff', Dark: '#111827' },
    'bg/elevated': { Light: '#f8fafc', Dark: '#162038' },
    'border/subtle': { Light: 'rgba(15,23,42,0.04)', Dark: 'rgba(255,255,255,0.04)' },
    'border/default': { Light: 'rgba(15,23,42,0.10)', Dark: 'rgba(255,255,255,0.08)' },
    'border/strong': { Light: 'rgba(15,23,42,0.16)', Dark: 'rgba(255,255,255,0.14)' },
    'border/focus': { Light: '#3b82f6', Dark: '#3b82f6' },
    'text/primary': { Light: '#0f172a', Dark: '#f1f5f9' },
    'text/secondary': { Light: '#475569', Dark: '#94a3b8' },
    'text/muted': { Light: '#64748b', Dark: '#64748b' },
    'text/disabled': { Light: '#94a3b8', Dark: '#475569' },
  },
  'color/trading': {
    bullish: { Light: '#10b981', Dark: '#10b981' },
    bearish: { Light: '#ef4444', Dark: '#ef4444' },
    accent: { Light: '#2563eb', Dark: '#2563eb' },
    'accent/light': { Light: '#3b82f6', Dark: '#3b82f6' },
    'up/bg': { Light: 'rgba(16,185,129,0.12)', Dark: 'rgba(16,185,129,0.12)' },
    'down/bg': { Light: 'rgba(239,68,68,0.12)', Dark: 'rgba(239,68,68,0.12)' },
    crypto: { Light: '#f59e0b', Dark: '#f59e0b' },
    equity: { Light: '#3b82f6', Dark: '#3b82f6' },
    etf: { Light: '#8b5cf6', Dark: '#8b5cf6' },
  },
  'color/chart': {
    'canvas/background': { Light: '#f8fafc', Dark: '#080d14' },
    'canvas/grid': { Light: 'rgba(15,23,42,0.07)', Dark: 'rgba(255,255,255,0.03)' },
    'canvas/crosshair': { Light: '#2563eb', Dark: '#3b82f6' },
    'canvas/axis-label': { Light: '#64748b', Dark: '#9ca3af' },
  },
}, null, 0)};

const FLOAT_DATA = ${JSON.stringify({
  layout: {
    'header-h': 52, 'strip-h': 34, 'sidebar-w': 260, 'panel-w': 316,
    'preferences-w': 512, 'dock-h': 320, 'dock-min': 200, 'control-h': 28,
    'widget-header-h': 36, 'panel-header-h': 32,
  },
  spacing: {
    'sp-1': 4, 'sp-2': 8, 'sp-3': 12, 'sp-4': 16, 'sp-5': 20, 'sp-6': 24, 'sp-8': 32,
    'icon-gap-tight': 4, 'icon-gap': 6, 'icon-gap-loose': 8,
  },
  radius: { sm: 4, md: 6, lg: 10, xl: 14 },
}, null, 0)};

const SCOPES = {
  surface: ['FRAME_FILL', 'SHAPE_FILL', 'STROKE_COLOR', 'TEXT_FILL'],
  trading: ['SHAPE_FILL', 'TEXT_FILL', 'STROKE_COLOR'],
  chart: ['FRAME_FILL', 'SHAPE_FILL', 'STROKE_COLOR'],
  layout: ['WIDTH_HEIGHT', 'GAP'],
  spacing: ['GAP', 'WIDTH_HEIGHT'],
  radius: ['CORNER_RADIUS'],
};

function createColorCollection(name, tokens, scopes) {
  const col = figma.variables.createVariableCollection(name);
  const lightId = col.modes[0].modeId;
  col.renameMode(lightId, 'Light');
  const darkId = col.addMode('Dark');
  const created = [];
  for (const [varName, modes] of Object.entries(tokens)) {
    const v = figma.variables.createVariable(varName, col, 'COLOR');
    v.scopes = scopes;
    v.setValueForMode(lightId, parseColor(modes.Light));
    v.setValueForMode(darkId, parseColor(modes.Dark));
    v.description = 'Synced from trading-terminal code';
    created.push(v.id);
  }
  return { collectionId: col.id, variableIds: created };
}

function createFloatCollection(name, tokens, scopes) {
  const col = figma.variables.createVariableCollection(name);
  const modeId = col.modes[0].modeId;
  col.renameMode(modeId, 'Value');
  const created = [];
  for (const [varName, value] of Object.entries(tokens)) {
    const v = figma.variables.createVariable(varName, col, 'FLOAT');
    v.scopes = scopes;
    v.setValueForMode(modeId, value);
    v.description = 'Synced from trading-terminal code';
    created.push(v.id);
  }
  return { collectionId: col.id, variableIds: created };
}

const results = { createdNodeIds: [], collections: {} };

results.collections.surface = createColorCollection('color/surface', TOKEN_DATA['color/surface'], SCOPES.surface);
results.collections.trading = createColorCollection('color/trading', TOKEN_DATA['color/trading'], SCOPES.trading);
results.collections.chart = createColorCollection('color/chart', TOKEN_DATA['color/chart'], SCOPES.chart);
results.collections.layout = createFloatCollection('layout', FLOAT_DATA.layout, SCOPES.layout);
results.collections.spacing = createFloatCollection('spacing', FLOAT_DATA.spacing, SCOPES.spacing);
results.collections.radius = createFloatCollection('radius', FLOAT_DATA.radius, SCOPES.radius);

return results;
`;

export default script;
