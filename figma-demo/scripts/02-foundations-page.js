/**
 * Figma use_figma script — Step 2: Foundations page with color swatches
 *
 * Prerequisite: run 01-create-variables.js first
 * Run on page named "Foundations" (creates if missing)
 */
export const script = `
function parseColor(css) {
  if (css.startsWith('#')) {
    const h = css.replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
    return { r: ((n >> 16) & 255) / 255, g: ((n >> 8) & 255) / 255, b: (n & 255) / 255, a: 1 };
  }
  const m = css.match(/rgba?\\(([^)]+)\\)/);
  if (!m) return { r: 0, g: 0, b: 0, a: 1 };
  const parts = m[1].split(',').map(s => s.trim());
  return { r: Number(parts[0]) / 255, g: Number(parts[1]) / 255, b: Number(parts[2]) / 255, a: parts[3] !== undefined ? Number(parts[3]) : 1 };
}

await figma.loadFontAsync({ family: 'Inter', style: 'Regular' });
await figma.loadFontAsync({ family: 'Inter', style: 'Semi Bold' });

let page = figma.root.children.find(p => p.name === 'Foundations');
if (!page) {
  page = figma.createPage();
  page.name = 'Foundations';
}
await figma.setCurrentPageAsync(page);

const createdNodeIds = [];

const pageFrame = figma.createFrame();
pageFrame.name = 'Foundations / Token Swatches';
pageFrame.layoutMode = 'VERTICAL';
pageFrame.primaryAxisSizingMode = 'AUTO';
pageFrame.counterAxisSizingMode = 'FIXED';
pageFrame.resize(900, 100);
pageFrame.itemSpacing = 32;
pageFrame.paddingTop = 40;
pageFrame.paddingBottom = 40;
pageFrame.paddingLeft = 40;
pageFrame.paddingRight = 40;
pageFrame.fills = [{ type: 'SOLID', color: parseColor('#070b14') }];
page.appendChild(pageFrame);
createdNodeIds.push(pageFrame.id);

function addTitle(parent, text) {
  const t = figma.createText();
  t.fontName = { family: 'Inter', style: 'Semi Bold' };
  t.characters = text;
  t.fontSize = 18;
  t.fills = [{ type: 'SOLID', color: { r: 0.95, g: 0.96, b: 0.98 } }];
  parent.appendChild(t);
  createdNodeIds.push(t.id);
  return t;
}

function addSwatchRow(parent, label, colors) {
  const row = figma.createFrame();
  row.name = label;
  row.layoutMode = 'VERTICAL';
  row.primaryAxisSizingMode = 'AUTO';
  row.counterAxisSizingMode = 'AUTO';
  row.itemSpacing = 8;
  row.fills = [];
  parent.appendChild(row);
  createdNodeIds.push(row.id);

  const lbl = figma.createText();
  lbl.fontName = { family: 'Inter', style: 'Semi Bold' };
  lbl.characters = label;
  lbl.fontSize = 12;
  lbl.fills = [{ type: 'SOLID', color: { r: 0.58, g: 0.64, b: 0.72 } }];
  row.appendChild(lbl);
  createdNodeIds.push(lbl.id);

  const swatches = figma.createFrame();
  swatches.layoutMode = 'HORIZONTAL';
  swatches.primaryAxisSizingMode = 'AUTO';
  swatches.counterAxisSizingMode = 'AUTO';
  swatches.itemSpacing = 12;
  swatches.fills = [];
  row.appendChild(swatches);
  createdNodeIds.push(swatches.id);

  for (const { name, hex } of colors) {
    const item = figma.createFrame();
    item.layoutMode = 'VERTICAL';
    item.primaryAxisSizingMode = 'AUTO';
    item.counterAxisSizingMode = 'AUTO';
    item.itemSpacing = 4;
    item.fills = [];
    swatches.appendChild(item);
    createdNodeIds.push(item.id);

    const box = figma.createRectangle();
    box.resize(64, 64);
    box.cornerRadius = 6;
    box.fills = [{ type: 'SOLID', color: parseColor(hex) }];
    box.strokes = [{ type: 'SOLID', color: { r: 1, g: 1, b: 1, a: 0.08 } }];
    box.strokeWeight = 1;
    item.appendChild(box);
    createdNodeIds.push(box.id);

    const cap = figma.createText();
    cap.fontName = { family: 'Inter', style: 'Regular' };
    cap.characters = name + '\\n' + hex;
    cap.fontSize = 10;
    cap.fills = [{ type: 'SOLID', color: { r: 0.45, g: 0.51, b: 0.58 } }];
    item.appendChild(cap);
    createdNodeIds.push(cap.id);
  }
}

addTitle(pageFrame, 'Antigravity Trading Terminal — Design Tokens');
addTitle(pageFrame, 'Source: frontend/src/index.css + settings/themePresets.js');

addSwatchRow(pageFrame, 'Trading colors', [
  { name: 'Bullish', hex: '#10b981' },
  { name: 'Bearish', hex: '#ef4444' },
  { name: 'Accent', hex: '#2563eb' },
  { name: 'Crypto', hex: '#f59e0b' },
  { name: 'Equity', hex: '#3b82f6' },
  { name: 'ETF', hex: '#8b5cf6' },
]);

addSwatchRow(pageFrame, 'Chart canvas — Dark', [
  { name: 'Background', hex: '#080d14' },
  { name: 'Crosshair', hex: '#3b82f6' },
  { name: 'Axis label', hex: '#9ca3af' },
]);

addSwatchRow(pageFrame, 'Chart canvas — Light', [
  { name: 'Background', hex: '#f8fafc' },
  { name: 'Crosshair', hex: '#2563eb' },
  { name: 'Axis label', hex: '#64748b' },
]);

addSwatchRow(pageFrame, 'Surface — Dark', [
  { name: 'bg-primary', hex: '#070b14' },
  { name: 'bg-card', hex: '#111827' },
  { name: 'bg-elevated', hex: '#162038' },
  { name: 'text-primary', hex: '#f1f5f9' },
]);

addSwatchRow(pageFrame, 'Surface — Light', [
  { name: 'bg-primary', hex: '#f1f5f9' },
  { name: 'bg-card', hex: '#ffffff' },
  { name: 'bg-elevated', hex: '#f8fafc' },
  { name: 'text-primary', hex: '#0f172a' },
]);

return { createdNodeIds, pageId: page.id };
`;

export default script;
