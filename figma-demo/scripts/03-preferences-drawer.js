/**
 * Figma use_figma script — Step 3: Preferences drawer (Theme tab)
 *
 * Source: frontend/src/components/SettingsPanel.jsx
 * Width: 512px (sm:max-w-lg)
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
await figma.loadFontAsync({ family: 'Inter', style: 'Medium' });
await figma.loadFontAsync({ family: 'Inter', style: 'Semi Bold' });
await figma.loadFontAsync({ family: 'JetBrains Mono', style: 'Regular' });

let page = figma.root.children.find(p => p.name === 'Screens');
if (!page) {
  page = figma.createPage();
  page.name = 'Screens';
}
await figma.setCurrentPageAsync(page);

const createdNodeIds = [];
const DARK = {
  bg: '#111827',
  bgElevated: '#162038',
  border: 'rgba(255,255,255,0.08)',
  text: '#f1f5f9',
  textMuted: '#64748b',
  textSecondary: '#94a3b8',
  accent: '#2563eb',
  bullish: '#10b981',
  bearish: '#ef4444',
};

const sheet = figma.createFrame();
sheet.name = 'Preferences / Theme tab';
sheet.layoutMode = 'VERTICAL';
sheet.primaryAxisSizingMode = 'AUTO';
sheet.counterAxisSizingMode = 'FIXED';
sheet.resize(512, 100);
sheet.itemSpacing = 0;
sheet.fills = [{ type: 'SOLID', color: parseColor(DARK.bg) }];
sheet.strokes = [{ type: 'SOLID', color: parseColor(DARK.border) }];
sheet.strokeWeight = 1;
sheet.effects = [{ type: 'DROP_SHADOW', color: { r: 0, g: 0, b: 0, a: 0.55 }, offset: { x: -8, y: 0 }, radius: 24, spread: 0, visible: true, blendMode: 'NORMAL' }];
page.appendChild(sheet);
createdNodeIds.push(sheet.id);

// Header
const header = figma.createFrame();
header.name = 'Header';
header.layoutMode = 'VERTICAL';
header.primaryAxisSizingMode = 'AUTO';
header.counterAxisSizingMode = 'FILL';
header.layoutAlign = 'STRETCH';
header.itemSpacing = 4;
header.paddingTop = 24;
header.paddingBottom = 16;
header.paddingLeft = 24;
header.paddingRight = 24;
header.fills = [];
header.strokes = [{ type: 'SOLID', color: parseColor(DARK.border) }];
header.strokeBottomWeight = 1;
header.strokeTopWeight = 0;
header.strokeLeftWeight = 0;
header.strokeRightWeight = 0;
sheet.appendChild(header);
createdNodeIds.push(header.id);

const titleRow = figma.createFrame();
titleRow.layoutMode = 'HORIZONTAL';
titleRow.primaryAxisSizingMode = 'AUTO';
titleRow.counterAxisSizingMode = 'AUTO';
titleRow.itemSpacing = 8;
titleRow.fills = [];
header.appendChild(titleRow);
createdNodeIds.push(titleRow.id);

const title = figma.createText();
title.fontName = { family: 'Inter', style: 'Semi Bold' };
title.characters = 'Preferences';
title.fontSize = 16;
title.fills = [{ type: 'SOLID', color: parseColor(DARK.text) }];
titleRow.appendChild(title);
createdNodeIds.push(title.id);

const desc = figma.createText();
desc.fontName = { family: 'Inter', style: 'Regular' };
desc.characters = 'Appearance, charts, layout, and system controls.';
desc.fontSize = 13;
desc.fills = [{ type: 'SOLID', color: parseColor(DARK.textMuted) }];
header.appendChild(desc);
createdNodeIds.push(desc.id);

// Tab bar
const tabs = figma.createFrame();
tabs.name = 'Tab bar';
tabs.layoutMode = 'HORIZONTAL';
tabs.primaryAxisSizingMode = 'AUTO';
tabs.counterAxisSizingMode = 'FILL';
tabs.layoutAlign = 'STRETCH';
tabs.itemSpacing = 0;
tabs.paddingLeft = 16;
tabs.paddingRight = 16;
tabs.paddingTop = 0;
tabs.paddingBottom = 0;
tabs.fills = [];
tabs.strokes = [{ type: 'SOLID', color: parseColor(DARK.border) }];
tabs.strokeBottomWeight = 1;
sheet.appendChild(tabs);
createdNodeIds.push(tabs.id);

const tabLabels = ['Theme', 'Chart', 'Layout', 'System'];
for (let i = 0; i < tabLabels.length; i++) {
  const tab = figma.createFrame();
  tab.layoutMode = 'HORIZONTAL';
  tab.primaryAxisSizingMode = 'AUTO';
  tab.counterAxisSizingMode = 'AUTO';
  tab.paddingTop = 12;
  tab.paddingBottom = 12;
  tab.paddingLeft = 12;
  tab.paddingRight = 12;
  tab.fills = [];
  tabs.appendChild(tab);
  createdNodeIds.push(tab.id);

  const tabText = figma.createText();
  tabText.fontName = { family: 'Inter', style: i === 0 ? 'Semi Bold' : 'Regular' };
  tabText.characters = tabLabels[i];
  tabText.fontSize = 12;
  tabText.fills = [{ type: 'SOLID', color: i === 0 ? parseColor(DARK.accent) : parseColor(DARK.textSecondary) }];
  tab.appendChild(tabText);
  createdNodeIds.push(tabText.id);

  if (i === 0) {
    tab.strokes = [{ type: 'SOLID', color: parseColor(DARK.accent) }];
    tab.strokeBottomWeight = 2;
    tab.strokeTopWeight = 0;
    tab.strokeLeftWeight = 0;
    tab.strokeRightWeight = 0;
  }
}

// Body
const body = figma.createFrame();
body.name = 'Theme tab body';
body.layoutMode = 'VERTICAL';
body.primaryAxisSizingMode = 'AUTO';
body.counterAxisSizingMode = 'FILL';
body.layoutAlign = 'STRETCH';
body.itemSpacing = 16;
body.paddingTop = 16;
body.paddingBottom = 24;
body.paddingLeft = 20;
body.paddingRight = 20;
body.fills = [];
sheet.appendChild(body);
createdNodeIds.push(body.id);

function sectionTitle(parent, text, badge) {
  const row = figma.createFrame();
  row.layoutMode = 'HORIZONTAL';
  row.primaryAxisSizingMode = 'AUTO';
  row.counterAxisSizingMode = 'FILL';
  row.layoutAlign = 'STRETCH';
  row.primaryAxisAlignItems = 'CENTER';
  row.counterAxisAlignItems = 'CENTER';
  row.fills = [];
  parent.appendChild(row);
  createdNodeIds.push(row.id);

  const t = figma.createText();
  t.fontName = { family: 'Inter', style: 'Semi Bold' };
  t.characters = text;
  t.fontSize = 12;
  t.fills = [{ type: 'SOLID', color: parseColor(DARK.text) }];
  row.appendChild(t);
  createdNodeIds.push(t.id);

  if (badge) {
    const b = figma.createFrame();
    b.layoutMode = 'HORIZONTAL';
    b.primaryAxisSizingMode = 'AUTO';
    b.counterAxisSizingMode = 'AUTO';
    b.paddingTop = 2;
    b.paddingBottom = 2;
    b.paddingLeft = 8;
    b.paddingRight = 8;
    b.cornerRadius = 4;
    b.fills = [];
    b.strokes = [{ type: 'SOLID', color: parseColor(DARK.border) }];
    b.strokeWeight = 1;
    row.appendChild(b);
    b.layoutSizingHorizontal = 'HUG';
    createdNodeIds.push(b.id);

    const bt = figma.createText();
    bt.fontName = { family: 'Inter', style: 'Regular' };
    bt.characters = badge;
    bt.fontSize = 10;
    bt.fills = [{ type: 'SOLID', color: parseColor(DARK.textSecondary) }];
    b.appendChild(bt);
    createdNodeIds.push(bt.id);
  }
}

function separator(parent) {
  const line = figma.createRectangle();
  line.name = 'Separator';
  line.resize(472, 1);
  line.fills = [{ type: 'SOLID', color: parseColor(DARK.border) }];
  parent.appendChild(line);
  line.layoutSizingHorizontal = 'FILL';
  createdNodeIds.push(line.id);
}

function toggleGroup(parent, options, selected) {
  const group = figma.createFrame();
  group.name = 'Toggle group';
  group.layoutMode = 'HORIZONTAL';
  group.primaryAxisSizingMode = 'FIXED';
  group.counterAxisSizingMode = 'AUTO';
  group.resize(472, 36);
  group.itemSpacing = 0;
  group.cornerRadius = 6;
  group.fills = [{ type: 'SOLID', color: parseColor(DARK.bgElevated) }];
  group.strokes = [{ type: 'SOLID', color: parseColor(DARK.border) }];
  group.strokeWeight = 1;
  parent.appendChild(group);
  createdNodeIds.push(group.id);

  for (const opt of options) {
    const item = figma.createFrame();
    item.layoutMode = 'HORIZONTAL';
    item.primaryAxisSizingMode = 'FILL';
    item.counterAxisSizingMode = 'FILL';
    item.layoutGrow = 1;
    item.paddingTop = 8;
    item.paddingBottom = 8;
    item.cornerRadius = 4;
    item.fills = opt === selected
      ? [{ type: 'SOLID', color: parseColor(DARK.bg) }]
      : [];
    group.appendChild(item);
    createdNodeIds.push(item.id);

    const lbl = figma.createText();
    lbl.fontName = { family: 'Inter', style: opt === selected ? 'Semi Bold' : 'Regular' };
    lbl.characters = opt;
    lbl.fontSize = 12;
    lbl.fills = [{ type: 'SOLID', color: opt === selected ? parseColor(DARK.text) : parseColor(DARK.textSecondary) }];
    item.appendChild(lbl);
    lbl.layoutSizingHorizontal = 'HUG';
    createdNodeIds.push(lbl.id);
  }
}

function colorField(parent, label, hex, presets) {
  const field = figma.createFrame();
  field.layoutMode = 'VERTICAL';
  field.primaryAxisSizingMode = 'AUTO';
  field.counterAxisSizingMode = 'FILL';
  field.layoutAlign = 'STRETCH';
  field.itemSpacing = 6;
  field.fills = [];
  parent.appendChild(field);
  createdNodeIds.push(field.id);

  const lbl = figma.createText();
  lbl.fontName = { family: 'Inter', style: 'Regular' };
  lbl.characters = label;
  lbl.fontSize = 12;
  lbl.fills = [{ type: 'SOLID', color: parseColor(DARK.textMuted) }];
  field.appendChild(lbl);
  createdNodeIds.push(lbl.id);

  const row = figma.createFrame();
  row.layoutMode = 'HORIZONTAL';
  row.primaryAxisSizingMode = 'AUTO';
  row.counterAxisSizingMode = 'AUTO';
  row.itemSpacing = 8;
  row.fills = [];
  field.appendChild(row);
  createdNodeIds.push(row.id);

  const swatch = figma.createRectangle();
  swatch.resize(36, 36);
  swatch.cornerRadius = 4;
  swatch.fills = [{ type: 'SOLID', color: parseColor(hex) }];
  swatch.strokes = [{ type: 'SOLID', color: parseColor(DARK.border) }];
  swatch.strokeWeight = 1;
  row.appendChild(swatch);
  createdNodeIds.push(swatch.id);

  const hexInput = figma.createFrame();
  hexInput.layoutMode = 'HORIZONTAL';
  hexInput.primaryAxisSizingMode = 'AUTO';
  hexInput.counterAxisSizingMode = 'AUTO';
  hexInput.paddingTop = 8;
  hexInput.paddingBottom = 8;
  hexInput.paddingLeft = 10;
  hexInput.paddingRight = 10;
  hexInput.cornerRadius = 4;
  hexInput.fills = [{ type: 'SOLID', color: parseColor(DARK.bgElevated) }];
  hexInput.strokes = [{ type: 'SOLID', color: parseColor(DARK.border) }];
  hexInput.strokeWeight = 1;
  row.appendChild(hexInput);
  createdNodeIds.push(hexInput.id);

  const hexText = figma.createText();
  hexText.fontName = { family: 'JetBrains Mono', style: 'Regular' };
  hexText.characters = hex;
  hexText.fontSize = 12;
  hexText.fills = [{ type: 'SOLID', color: parseColor(DARK.text) }];
  hexInput.appendChild(hexText);
  createdNodeIds.push(hexText.id);

  const presetsRow = figma.createFrame();
  presetsRow.layoutMode = 'HORIZONTAL';
  presetsRow.primaryAxisSizingMode = 'AUTO';
  presetsRow.counterAxisSizingMode = 'AUTO';
  presetsRow.itemSpacing = 6;
  presetsRow.fills = [];
  field.appendChild(presetsRow);
  createdNodeIds.push(presetsRow.id);

  for (const p of presets) {
    const dot = figma.createRectangle();
    dot.resize(20, 20);
    dot.cornerRadius = 4;
    dot.fills = [{ type: 'SOLID', color: parseColor(p) }];
    dot.strokes = p === hex
      ? [{ type: 'SOLID', color: parseColor(DARK.accent) }]
      : [{ type: 'SOLID', color: parseColor(DARK.border) }];
    dot.strokeWeight = p === hex ? 2 : 1;
    presetsRow.appendChild(dot);
    createdNodeIds.push(dot.id);
  }
}

// Color mode section
sectionTitle(body, 'Color mode', 'dark');
toggleGroup(body, ['Dark', 'Light', 'System'], 'Dark');
separator(body);

// Trading colors section
sectionTitle(body, 'Trading colors', null);
colorField(body, 'Bullish / Up', DARK.bullish, ['#10b981', '#22c55e', '#00d4aa', '#4ade80']);
colorField(body, 'Bearish / Down', DARK.bearish, ['#ef4444', '#f87171', '#ff4757', '#dc2626']);
colorField(body, 'Accent', DARK.accent, ['#2563eb', '#3b82f6', '#6366f1', '#0ea5e9']);
separator(body);

// Reset button
const btnRow = figma.createFrame();
btnRow.layoutMode = 'HORIZONTAL';
btnRow.primaryAxisSizingMode = 'AUTO';
btnRow.counterAxisSizingMode = 'FILL';
btnRow.layoutAlign = 'STRETCH';
btnRow.primaryAxisAlignItems = 'MAX';
btnRow.fills = [];
body.appendChild(btnRow);
createdNodeIds.push(btnRow.id);

const resetBtn = figma.createFrame();
resetBtn.layoutMode = 'HORIZONTAL';
resetBtn.primaryAxisSizingMode = 'AUTO';
resetBtn.counterAxisSizingMode = 'AUTO';
resetBtn.itemSpacing = 6;
resetBtn.paddingTop = 6;
resetBtn.paddingBottom = 6;
resetBtn.paddingLeft = 12;
resetBtn.paddingRight = 12;
resetBtn.cornerRadius = 6;
resetBtn.fills = [];
resetBtn.strokes = [{ type: 'SOLID', color: parseColor(DARK.border) }];
resetBtn.strokeWeight = 1;
btnRow.appendChild(resetBtn);
createdNodeIds.push(resetBtn.id);

const resetLbl = figma.createText();
resetLbl.fontName = { family: 'Inter', style: 'Regular' };
resetLbl.characters = 'Reset appearance';
resetLbl.fontSize = 12;
resetLbl.fills = [{ type: 'SOLID', color: parseColor(DARK.text) }];
resetBtn.appendChild(resetLbl);
createdNodeIds.push(resetLbl.id);

// Code trace annotation
const note = figma.createText();
note.fontName = { family: 'Inter', style: 'Regular' };
note.characters = 'Code: SettingsPanel.jsx → appearance tab';
note.fontSize = 10;
note.fills = [{ type: 'SOLID', color: parseColor(DARK.textMuted) }];
body.appendChild(note);
createdNodeIds.push(note.id);

return { createdNodeIds, sheetId: sheet.id, pageId: page.id };
`;

export default script;
