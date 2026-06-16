import { test, expect } from '@playwright/test';
import { gotoDashboard } from './helpers.js';

test.describe('Single chart maximize / minimize', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('terminal_settings_v1', JSON.stringify({
        version: 1,
        theme: 'dark',
        workspace: {
          layoutMode: 'trade',
          zenMode: false,
          dockCollapsed: false,
          rightPanelCollapsed: false,
          dockHeight: 320,
          viewMode: 'single',
        },
        chartLayout: { timeframe: '1m', chartType: 'candle', activeIndicators: {} },
        workspacePresets: [],
        alerts: [],
        onboardingCompleted: true,
      }));
    });
    await gotoDashboard(page);
    await expect(page.getByRole('tab', { name: 'Chart', exact: true })).toBeVisible();
  });

  test('chart header maximize then minimize restores dock and panel', async ({ page }) => {
    const dashboard = page.locator('.dashboard-container');
    const dock = page.locator('.bottom-dock');
    const tradingPanel = page.locator('.trading-panel');

    await expect(dock).toBeVisible();
    await expect(tradingPanel).toBeVisible();

    const maximizeBtn = page.getByTitle('Maximize chart (F)');
    await expect(maximizeBtn).toBeVisible();
    await maximizeBtn.click();

    await expect(dashboard).toHaveAttribute('data-zen', '');
    await expect(dock).toBeHidden();
    await expect(page.getByTitle('Restore layout (F)')).toBeVisible();

    const minimizeBtn = page.getByTitle('Restore layout (F)');
    await minimizeBtn.click();

    await expect(dashboard).not.toHaveAttribute('data-zen', '');
    await expect(dock).toBeVisible({ timeout: 5000 });
    await expect(tradingPanel).toBeVisible({ timeout: 5000 });

    const zenMode = await page.evaluate(() => {
      const raw = localStorage.getItem('terminal_settings_v1');
      return JSON.parse(raw || '{}')?.workspace?.zenMode;
    });
    expect(zenMode).toBe(false);
  });

  test('F key toggles zen mode on single chart', async ({ page }) => {
    const dashboard = page.locator('.dashboard-container');
    await page.keyboard.press('f');
    await expect(dashboard).toHaveAttribute('data-zen', '');
    await page.keyboard.press('f');
    await expect(dashboard).not.toHaveAttribute('data-zen', '');
    await expect(page.locator('.bottom-dock')).toBeVisible();
  });

  test('zen mode expands chart to fill workspace below header', async ({ page }) => {
    const dashboard = page.locator('.dashboard-container');
    const chartRoot = page.locator('[data-chart-root="main"]');

    const before = await chartRoot.boundingBox();
    expect(before?.height ?? 0).toBeGreaterThan(200);

    await page.getByTitle('Maximize chart (F)').click();
    await expect(dashboard).toHaveAttribute('data-zen', '');
    await expect(dashboard).not.toHaveAttribute('data-dock-hidden', '');

    const after = await chartRoot.boundingBox();
    const viewport = page.viewportSize();
    expect(after?.height ?? 0).toBeGreaterThan((before?.height ?? 0) * 1.25);
    expect(after?.height ?? 0).toBeGreaterThan((viewport?.height ?? 0) * 0.55);
  });

  test('restore works when page loads already in zen mode', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('terminal_settings_v1', JSON.stringify({
        version: 1,
        theme: 'dark',
        workspace: {
          layoutMode: 'trade',
          zenMode: true,
          dockCollapsed: true,
          rightPanelCollapsed: true,
          dockHeight: 320,
          viewMode: 'single',
        },
        chartLayout: { timeframe: '1m', chartType: 'candle', activeIndicators: {} },
        workspacePresets: [],
        alerts: [],
        onboardingCompleted: true,
      }));
    });
    await page.goto('/');
    await expect(page.locator('.dashboard-container')).toBeVisible({ timeout: 15_000 });
    const dashboard = page.locator('.dashboard-container');
    await expect(dashboard).toHaveAttribute('data-zen', '');

    await page.getByTitle('Restore layout (F)').click();
    await expect(dashboard).not.toHaveAttribute('data-zen', '');
    await expect(page.locator('.bottom-dock')).toBeVisible();
    await expect(page.locator('.trading-panel')).toBeVisible();
  });
});
