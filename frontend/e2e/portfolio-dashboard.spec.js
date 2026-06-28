import { test, expect } from '@playwright/test';
import { gotoDashboard, waitForBootstrap, ensureDockExpanded } from './helpers.js';

test.describe('Portfolio Dashboard', () => {
  test('opens and shows analytics tabs with content', async ({ page }) => {
    const errors = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text());
    });

    await gotoDashboard(page);
    await waitForBootstrap(page);
    await ensureDockExpanded(page);

    const dock = page.locator('.bottom-dock');
    await dock.getByRole('button', { name: 'Dashboard' }).click();

    const sheet = page.getByLabel('Portfolio Dashboard');
    await expect(sheet).toBeVisible({ timeout: 15_000 });
    await expect(sheet.getByText('Portfolio Dashboard')).toBeVisible();
    await expect(sheet.getByRole('tab', { name: 'Overview' })).toBeVisible();
    await expect(sheet.getByRole('tab', { name: 'Breakdown' })).toBeVisible();
    await expect(sheet.getByRole('tab', { name: 'P&L Calendar' })).toBeVisible();
    await expect(sheet.getByRole('tab', { name: 'Journal' })).toBeVisible();

    // Should not stay blank — either stat cards or empty-state messages appear
    await expect(
      sheet.getByText(/Total P&L|No equity data yet|No closed trades|Equity Curve/i).first(),
    ).toBeVisible({ timeout: 20_000 });

    await expect(sheet.getByText(/Analytics timed out|Unknown action/i)).toHaveCount(0);

    const critical = errors.filter((e) => !/favicon|404.*\.map/i.test(e));
    expect(critical).toEqual([]);
  });
});
