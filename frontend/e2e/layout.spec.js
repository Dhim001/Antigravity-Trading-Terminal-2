import { test, expect } from '@playwright/test';
import {
  gotoDashboard,
  openAlgoTab,
  openDockTab,
  seedSettings,
  STRATEGY_TEMPLATE_NAMES,
} from './helpers.js';

test.describe('Layout QA — Algo tab & dock', () => {
  test.beforeEach(async ({ page }) => {
    await gotoDashboard(page);
    await openAlgoTab(page);
  });

  test('desktop viewport shows all three algo panels', async ({ page }) => {
    await expect(page.locator('.algo-tab__panel--deploy')).toBeVisible();
    await expect(page.locator('.algo-tab__panel--bots')).toBeVisible();
    await expect(page.locator('.algo-tab__panel--log')).toBeVisible();

    const panels = page.locator('.algo-tab__panel');
    await expect(panels).toHaveCount(3);
  });

  test('strategy template cards render in deploy panel', async ({ page }) => {
    const grid = page.locator('.algo-template-grid');
    const cards = grid.locator('.algo-template-btn');
    await expect(cards).toHaveCount(5);

    for (const name of STRATEGY_TEMPLATE_NAMES) {
      await expect(grid.getByRole('button', { name })).toBeVisible();
    }
  });

  test('selecting a strategy template marks it active', async ({ page }) => {
    const trendCard = page.locator('.algo-template-grid').getByRole('button', { name: 'Supertrend + ADX' });
    await trendCard.click();
    await expect(trendCard).toHaveClass(/algo-template-btn--active/);
  });

  test('history tab opens and shows filter toolbar', async ({ page }) => {
    await openDockTab(page, /History/i, 'Data');
    await expect(page.getByPlaceholder('Search symbol, ID, type…')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('button', { name: /Refresh/i })).toBeVisible();
  });

  test('history fullscreen sheet opens from dock', async ({ page }) => {
    await openDockTab(page, /History/i, 'Data');
    await page.getByTitle('Expand to fullscreen').click();

    await expect(page.getByText('Transaction History')).toBeVisible();
    await expect(page.locator('[data-slot="sheet-content"]')).toBeVisible();
  });
});

test.describe('Layout QA — compact dock', () => {
  test('compact dock applies data-compact when height is low', async ({ page }) => {
    await seedSettings(page, {
      workspace: {
        layoutMode: 'trade',
        dockHeight: 240,
        dockActiveTab: 'algo',
        dockGroup: 'automation',
        viewMode: 'single',
      },
    });
    await gotoDashboard(page);
    await openAlgoTab(page);

    const dock = page.locator('.bottom-dock');
    await expect(dock).toHaveAttribute('data-compact', '');
  });
});

test.describe('Layout QA — mobile viewport', () => {
  test.use({ viewport: { width: 768, height: 900 } });

  test('algo tab remains usable on narrow screens', async ({ page }) => {
    await gotoDashboard(page);
    await openAlgoTab(page);

    await expect(page.locator('.algo-tab__panel--deploy')).toBeVisible();
    await expect(page.locator('.algo-template-btn').first()).toBeVisible();
    await expect(page.locator('.algo-tab__panel--bots')).toBeAttached();
  });
});
