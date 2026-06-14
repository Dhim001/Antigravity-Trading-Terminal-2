/**
 * Interactive ECharts smoke — drag/zoom, timeframe + indicator toggles.
 * Run: E2E_BASE_URL=http://127.0.0.1:5173 npx playwright test e2e/chart-interaction.spec.js
 */
import { test, expect } from '@playwright/test';
import { gotoDashboard, waitForBootstrap } from './helpers.js';

test.describe('Chart interaction audit', () => {
  test('drag, zoom, toggle — collect console issues', async ({ page }) => {
    const consoleLines = [];
    const pageErrors = [];

    page.on('console', (msg) => {
      const type = msg.type();
      const text = msg.text();
      consoleLines.push(`[${type}] ${text}`);
    });
    page.on('pageerror', (err) => pageErrors.push(String(err)));

    await gotoDashboard(page);
    await waitForBootstrap(page);

    // Let live ticks run (sim feed updates)
    await page.waitForTimeout(12_000);

    const chartCanvas = page.locator('main canvas').first();
    await expect(chartCanvas).toBeVisible({ timeout: 25_000 });
    await page.waitForTimeout(4000);

    const box = await chartCanvas.boundingBox();
    expect(box).toBeTruthy();

    const cx = box.x + box.width * 0.55;
    const cy = box.y + box.height * 0.45;

    // Capture marker positions before/after drag
    const markerSnap = async (label) => {
      return page.evaluate((tag) => {
        const container = document.querySelector('[data-chart-root="main"]');
        const inst = container?.__chartInstance;
        if (!inst) return { tag, error: 'no instance' };
        const markers = (inst.getOption()?.series ?? []).find((s) => s.id === 'signal-markers');
        const data = markers?.data ?? [];
        const converted = data.slice(0, 5).map((d) => {
          const v = d.value ?? d;
          const px = inst.convertToPixel({ seriesIndex: inst.getOption().series.findIndex((s) => s.id === 'signal-markers') }, v);
          return { value: v, pixel: px };
        });
        const dz = inst.getOption()?.dataZoom?.[0];
        return { tag, markerCount: data.length, sample: converted, startValue: dz?.startValue, endValue: dz?.endValue };
      }, label);
    };

    const beforeDrag = await markerSnap('before');
    await page.mouse.move(cx, cy);
    await page.mouse.down();
    await page.mouse.move(cx - 200, cy, { steps: 15 });
    await page.mouse.up();
    await page.waitForTimeout(1000);
    const afterDrag = await markerSnap('after');

    // Marker drift: x must stay fixed in category-key space after pan/zoom
    const beforeX = beforeDrag.sample.map((s) => s.value?.[0]);
    const afterX = afterDrag.sample.map((s) => s.value?.[0]);
    if (beforeX.length && beforeX.join(',') !== afterX.join(',')) {
      console.log('\n=== MARKER X DRIFT DETECTED ===\n', { beforeX, afterX });
    }

    for (const s of [...beforeDrag.sample, ...afterDrag.sample]) {
      const x = s.value?.[0];
      if (typeof x === 'number' && x < 100_000) {
        console.log('\n=== MARKER USES INDEX NOT TIMESTAMP ===\n', s);
      }
    }

    // Wheel zoom
    await page.mouse.move(cx, cy);
    await page.mouse.wheel(0, -400);
    await page.waitForTimeout(500);
    await page.mouse.wheel(0, 400);
    await page.waitForTimeout(500);

    // Timeframe toggles
    for (const tf of ['5m', '15m', '1H', '1m']) {
      await page.getByRole('radio', { name: tf, exact: true }).click();
      await page.waitForTimeout(1200);
    }

    // Toggle volume off/on via indicator toggle
    const volumeToggle = page.getByRole('button', { name: /Volume/i });
    if (await volumeToggle.isVisible()) {
      await volumeToggle.click();
      await page.waitForTimeout(800);
      await volumeToggle.click();
      await page.waitForTimeout(800);
    }

    // Toggle RSI (sub-pane layout change)
    const rsiToggle = page.getByRole('button', { name: /RSI/i });
    if (await rsiToggle.isVisible()) {
      await rsiToggle.click();
      await page.waitForTimeout(800);
      await rsiToggle.click();
      await page.waitForTimeout(800);
    }

    // Line vs candle
    await page.getByRole('radio', { name: /Line/i }).click();
    await page.waitForTimeout(1000);
    await page.getByRole('radio', { name: /Candle/i }).click();
    await page.waitForTimeout(1000);

    // Inspect ECharts option sanity via canvas parent
    const chartAudit = await page.evaluate(() => {
      const issues = [];
      const canvases = document.querySelectorAll('canvas');
      issues.push(`canvasCount=${canvases.length}`);

      const container = document.querySelector('[data-chart-root="main"]');
      let echartsInfo = null;
      if (container) {
        const inst = container.__chartInstance;
        if (inst) {
          const opt = inst.getOption();
          const x0 = opt?.xAxis?.[0]?.data ?? [];
          const nullCats = x0.filter((v) => v == null).length;
          const main = (opt?.series ?? []).find((s) => s.id === 'main');
          const markers = (opt?.series ?? []).find((s) => s.id === 'signal-markers');
          echartsInfo = {
            xLen: x0.length,
            nullCategories: nullCats,
            mainPoints: main?.data?.length ?? 0,
            markerPoints: markers?.data?.length ?? 0,
            dataZoom: opt?.dataZoom?.[0]?.startValue,
            dataZoomEnd: opt?.dataZoom?.[0]?.endValue,
          };
        } else {
          issues.push('ECharts instance missing on chart container');
        }
      } else {
        issues.push('chart container or window.echarts not found');
      }

      return { issues, echartsInfo };
    });

    // Report — test always passes but logs findings
    const report = {
      pageErrors,
      consoleLines: [...new Set(consoleLines)],
      chartAudit,
      beforeDrag,
      afterDrag,
    };
    console.log('\n=== CHART INTERACTION AUDIT ===\n', JSON.stringify(report, null, 2));

    expect(pageErrors, `Page errors: ${pageErrors.join('; ')}`).toHaveLength(0);

    const criticalConsole = consoleLines.filter(
      (l) => l.startsWith('[error]') || /overlay patch failed|live candle update failed|zoom preservation failed/i.test(l),
    );
    expect(criticalConsole, `Console errors: ${criticalConsole.join('; ')}`).toHaveLength(0);
  });
});
