/**
 * Medium frontend performance & stress tests.
 * Requires backend (8765/8766) and preview/dev server (4173 or 5173).
 */
import { test, expect } from '@playwright/test';
import { gotoDashboard, waitForBootstrap, openDockTab, openAlgoTab } from './helpers.js';

const BUDGETS = {
  /** First meaningful paint proxy: dashboard shell visible */
  dashboardVisibleMs: 8000,
  /** Full bootstrap (REST + WS badge) */
  bootstrapMs: 20_000,
  /** Average tab switch after warm load */
  tabSwitchAvgMs: 800,
  /** Watchlist symbol switch (5 clicks) avg */
  symbolSwitchAvgMs: 1500,
  /** JS heap after stress (MB) — soft ceiling */
  heapAfterStressMb: 180,
};

async function measureMs(fn) {
  const t0 = performance.now();
  await fn();
  return performance.now() - t0;
}

test.describe('Frontend performance & stress', () => {
  test('cold load within budget', async ({ page }) => {
    const t0 = Date.now();
    await page.goto('/');
    await expect(page.locator('.dashboard-container')).toBeVisible({ timeout: BUDGETS.dashboardVisibleMs });
    const elapsed = Date.now() - t0;
    expect(elapsed).toBeLessThan(BUDGETS.dashboardVisibleMs);

    const nav = await page.evaluate(() => {
      const n = performance.getEntriesByType('navigation')[0];
      return n
        ? {
            domContentLoaded: n.domContentLoadedEventEnd - n.startTime,
            loadComplete: n.loadEventEnd - n.startTime,
          }
        : null;
    });
    test.info().attach('navigation-timing', { body: JSON.stringify(nav, null, 2), contentType: 'application/json' });
  });

  test('bootstrap completes within budget', async ({ page }) => {
    const t0 = Date.now();
    await gotoDashboard(page);
    await waitForBootstrap(page);
    const elapsed = Date.now() - t0;
    expect(elapsed).toBeLessThan(BUDGETS.bootstrapMs);
  });

  test('dock tab thrash stays responsive', async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);

    const dock = page.locator('.bottom-dock');
    const tabRoutes = [
      { group: 'Portfolio', tab: /Positions/i },
      { group: 'Portfolio', tab: /Orders/i },
      { group: 'Portfolio', tab: /Balances/i },
      { algo: true },
      { group: 'Data', tab: /History/i },
      { group: 'Data', tab: 'Equity Curve' },
    ];
    const timings = [];

    for (let round = 0; round < 3; round++) {
      for (const route of tabRoutes) {
        const ms = await measureMs(async () => {
          if (route.algo) {
            await openAlgoTab(page);
          } else {
            await openDockTab(page, route.tab, route.group);
          }
          await expect(dock.locator('.dock-tab-body[data-state="active"]')).toBeVisible();
        });
        timings.push(ms);
      }
    }

    const avg = timings.reduce((a, b) => a + b, 0) / timings.length;
    const p95 = [...timings].sort((a, b) => a - b)[Math.floor(timings.length * 0.95)] ?? avg;

    test.info().attach('tab-switch-ms', {
      body: JSON.stringify({ avg: Math.round(avg), p95: Math.round(p95), samples: timings.length }, null, 2),
      contentType: 'application/json',
    });

    expect(avg).toBeLessThan(BUDGETS.tabSwitchAvgMs);
    expect(p95).toBeLessThan(BUDGETS.tabSwitchAvgMs * 2.5);
  });

  test('watchlist symbol switching under load', async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);

    const rows = page.locator('.watchlist-table tbody tr');
    const count = await rows.count();
    test.skip(count < 3, 'Need at least 3 watchlist rows');

    const clicks = Math.min(8, count);
    const timings = [];

    for (let i = 0; i < clicks; i++) {
      const ms = await measureMs(async () => {
        await rows.nth(i % count).click();
        await page.waitForTimeout(50);
      });
      timings.push(ms);
    }

    const avg = timings.reduce((a, b) => a + b, 0) / timings.length;
    test.info().attach('symbol-switch-ms', {
      body: JSON.stringify({ avg: Math.round(avg), timings: timings.map(Math.round) }, null, 2),
      contentType: 'application/json',
    });
    expect(avg).toBeLessThan(BUDGETS.symbolSwitchAvgMs);
  });

  test('multi-chart view switch and layout stress', async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);

    const ms = await measureMs(async () => {
      await page.getByRole('tab', { name: /Multi-Chart/i }).click();
      await expect(page.locator('.multi-chart-grid')).toBeVisible({ timeout: 10_000 });
    });
    expect(ms).toBeLessThan(5000);

    await page.getByRole('tab', { name: 'Chart', exact: true }).click();
    await expect(page.locator('.workspace-main')).toBeVisible();
  });

  test('sidebar collapse/expand and resize handle', async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);

    const toggle = page.locator('.sidebar-edge-toggle');
    await expect(toggle).toBeVisible({ timeout: 5000 });

    await toggle.hover();
    await toggle.click();
    await expect(page.locator('.watchlist-sidebar--collapsed')).toBeVisible();

    const expandToggle = page.locator('.sidebar-edge-toggle--visible');
    await expandToggle.click();
    await expect(page.locator('.watchlist-sidebar--collapsed')).toBeHidden({ timeout: 3000 });
  });

  test('heap stable after interaction stress', async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);

    for (let i = 0; i < 5; i++) {
      await openDockTab(page, /Orders/i, 'Portfolio');
      await openDockTab(page, /Positions/i, 'Portfolio');
      await openAlgoTab(page);
    }

    const heap = await page.evaluate(() => {
      if (performance.memory) {
        return Math.round(performance.memory.usedJSHeapSize / (1024 * 1024));
      }
      return null;
    });

    test.info().attach('heap-mb', { body: String(heap ?? 'n/a'), contentType: 'text/plain' });
    if (heap != null) {
      expect(heap).toBeLessThan(BUDGETS.heapAfterStressMb);
    }
  });

  test('HTTP API sustains parallel reads via proxy', async ({ request }) => {
    const apiUrl = process.env.E2E_API_URL || 'http://127.0.0.1:8766';
    const n = 15;
    const t0 = Date.now();
    const responses = await Promise.all(
      Array.from({ length: n }, () => request.get(`${apiUrl}/api/v1/account`)),
    );
    const elapsed = Date.now() - t0;
    const ok = responses.filter(r => r.ok()).length;
    expect(ok / n).toBeGreaterThanOrEqual(0.95);
    expect(elapsed / n).toBeLessThan(500);
  });
});
