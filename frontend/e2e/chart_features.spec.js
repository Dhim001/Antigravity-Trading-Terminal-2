import { test, expect } from '@playwright/test';
import { gotoDashboard } from './helpers.js';

/**
 * Exercises the Chart Widget enhancements against a running terminal:
 * candle types (Heikin-Ashi / Renko), Volume Profile, drawing tools + the
 * graphic overlay, replay mode, and comparison mode.
 *
 * Run against a live UI, e.g.:
 *   E2E_BASE_URL=http://127.0.0.1:5175 npx playwright test e2e/chart_features.spec.js
 */

const chartRoot = '[data-chart-root="main"]';

async function getChartOption(page) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel);
    const inst = el && el.__chartInstance;
    if (!inst) return null;
    const opt = inst.getOption();
    // ECharts normalizes graphic to [{ elements: [...] }] (flat elements).
    const elements = (opt.graphic && opt.graphic[0] && opt.graphic[0].elements) || [];
    const overlay = elements.map((e) => ({ id: e.id, type: e.type }));
    const seriesIds = (opt.series || []).map((s) => s.id).filter(Boolean);
    return { overlay, seriesIds };
  }, chartRoot);
}

test.describe('Chart widget enhancements', () => {
  test('candle types, VPVR, drawings, replay, comparison', async ({ page }) => {
    const errors = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    page.on('pageerror', (err) => errors.push(String(err)));

    await gotoDashboard(page);

    // Chart canvas should mount.
    const canvas = page.locator(`${chartRoot} canvas`).first();
    await expect(canvas).toBeVisible({ timeout: 20_000 });

    // Wait for the ECharts instance to be ready with a main series.
    await expect.poll(async () => {
      const opt = await getChartOption(page);
      return opt?.seriesIds?.includes('main') ? 'ready' : 'pending';
    }, { timeout: 20_000 }).toBe('ready');

    // ── Candle types (Radix toggle items may be button or radio role) ──
    const clickToggle = async (name) => {
      const byBtn = page.getByRole('button', { name, exact: true });
      const byRadio = page.getByRole('radio', { name, exact: true });
      await byBtn.or(byRadio).first().click();
    };
    await clickToggle('HA');
    await page.waitForTimeout(400);
    await clickToggle('Renko');
    await page.waitForTimeout(400);
    await clickToggle('Candle');
    await page.waitForTimeout(300);

    // ── Volume Profile ──
    await page.getByTitle('Volume Profile (VPVR)').click();
    await expect.poll(async () => {
      const opt = await getChartOption(page);
      return (opt?.overlay || []).filter((e) => String(e.id).startsWith('vp-bin')).length;
    }, { timeout: 10_000 }).toBeGreaterThan(0);

    // ── Drawing tool: trendline (two clicks on the chart) ──
    await page.getByTitle('Trendline (2 clicks)').click();
    const box = await canvas.boundingBox();
    expect(box).toBeTruthy();
    // Both points in the upper (main price) grid — lower areas are the volume subpane.
    await page.mouse.click(box.x + box.width * 0.4, box.y + box.height * 0.35);
    await page.waitForTimeout(150);
    await page.mouse.click(box.x + box.width * 0.6, box.y + box.height * 0.2);
    await page.waitForTimeout(400);

    // The overlay should now contain a line element (the trendline) — i.e. an
    // overlay id that is not a VPVR element.
    const afterDraw = await getChartOption(page);
    const drawingLines = (afterDraw.overlay || []).filter(
      (e) => e.type === 'line' && !String(e.id).startsWith('vp-'),
    );
    expect(drawingLines.length, 'trendline rendered as a line element').toBeGreaterThan(0);

    // ── Replay mode ──
    await page.getByTitle('Replay mode (bar-by-bar)').click();
    await expect(page.getByTitle('Step forward')).toBeVisible({ timeout: 5_000 });
    await page.getByTitle('Step forward').click();
    await page.getByTitle('Step forward').click();
    await page.getByTitle('Exit replay').click();
    await expect(page.getByTitle('Step forward')).toBeHidden();

    // ── Comparison mode ──
    const compare = page.getByLabel('Compare symbol');
    if (await compare.isVisible().catch(() => false)) {
      await compare.click();
      const firstOption = page.getByRole('option').nth(1); // [0] is "Compare: off"
      await firstOption.click();
      await expect.poll(async () => {
        const opt = await getChartOption(page);
        return opt?.seriesIds?.includes('compare') ? 'yes' : 'no';
      }, { timeout: 15_000 }).toBe('yes');
    }

    // No console/page errors from chart interactions.
    const chartErrors = errors.filter((e) => !/ResizeObserver|favicon/i.test(e));
    expect(chartErrors, `console errors: ${chartErrors.join('\n')}`).toEqual([]);
  });
});
