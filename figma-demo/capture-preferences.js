/**
 * Capture the live Preferences panel for visual reference.
 * Replaces generate_figma_design when Figma MCP is unavailable.
 *
 * Usage:
 *   1. Start backend + frontend (npm run dev in frontend/)
 *   2. node figma-demo/capture-preferences.js
 *
 * Output: figma-demo/reference/preferences-panel-dark.png
 */
import { createRequire } from 'module';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(__dirname, '../frontend/package.json'));
const { chromium } = require('@playwright/test');
const OUT_DIR = path.join(__dirname, 'reference');
const BASE_URL = process.env.VITE_URL || 'http://localhost:5173';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

fs.mkdirSync(OUT_DIR, { recursive: true });

console.log(`Navigating to ${BASE_URL}...`);
try {
  await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 30000 });
} catch (err) {
  console.error('Could not reach dev server. Start it with: cd frontend && npm run dev');
  console.error(err.message);
  await browser.close();
  process.exit(1);
}

await page.waitForTimeout(1500);

// Open Preferences via keyboard shortcut (Ctrl+,)
await page.keyboard.press('Control+,');
await page.waitForTimeout(800);

const outPath = path.join(OUT_DIR, 'preferences-panel-dark.png');
await page.screenshot({ path: outPath, fullPage: false });
console.log(`Saved ${outPath}`);

const sheet = page.locator('[data-slot="sheet-content"], .settings-panel').first();
if (await sheet.count()) {
  const sheetPath = path.join(OUT_DIR, 'preferences-sheet-crop.png');
  await sheet.screenshot({ path: sheetPath });
  console.log(`Saved ${sheetPath}`);
}

await browser.close();
