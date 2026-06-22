/**
 * Workspace persistence + multi-chart link groups (Track B polish).
 */
import { test, expect } from '@playwright/test';
import { gotoDashboard, openAlgoTab } from './helpers.js';

test.describe('Workspace persistence', () => {
  test('reload restores dock height and active algo tab', async ({ page }) => {
    await gotoDashboard(page, {
      settings: {
        workspace: {
          layoutMode: 'automate',
          dockHeight: 380,
          dockActiveTab: 'algo',
          dockGroup: 'automation',
          viewMode: 'single',
        },
      },
    });

    await expect(page.locator('.dashboard-container')).toHaveAttribute('data-layout-mode', 'automate');
    const dockH = await page.locator('.dashboard-container').evaluate(
      (el) => getComputedStyle(el).getPropertyValue('--dock-h').trim(),
    );
    expect(dockH).toBe('380px');
    await expect(page.getByText('Deploy Bot', { exact: true })).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('.algo-tab__panel--deploy')).toBeVisible();
  });

  test('layout mode preset survives reload', async ({ page }) => {
    await gotoDashboard(page, {
      settings: {
        workspace: { layoutMode: 'portfolio', dockActiveTab: 'positions', dockGroup: 'portfolio' },
      },
    });
    await expect(page.locator('.dashboard-container')).toHaveAttribute('data-layout-mode', 'portfolio');
  });
});

test.describe('Multi-chart link groups', () => {
  test.use({ viewport: { width: 1600, height: 900 } });

  test('multi-chart view renders linked pane controls', async ({ page }) => {
    await gotoDashboard(page, {
      settings: { workspace: { viewMode: 'multi', layoutMode: 'analyze' } },
    });

    await page.keyboard.press('Control+2');
    await expect(page.locator('.multi-chart-root')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/link groups/i).first()).toBeVisible({ timeout: 10_000 });
  });
});
