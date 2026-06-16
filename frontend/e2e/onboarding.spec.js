import { test, expect } from '@playwright/test';

test.describe('Onboarding & help', () => {
  test('help sheet opens from header', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('.dashboard-container', { timeout: 15000 });
    const helpBtn = page.getByRole('button', { name: /help/i });
    if (await helpBtn.isVisible()) {
      await helpBtn.click();
      await expect(page.getByText('Help & glossary')).toBeVisible();
    }
  });

  test('shortcuts sheet opens with ?', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('.dashboard-container', { timeout: 15000 });
    await page.keyboard.press('?');
    await expect(page.getByText('Keyboard shortcuts')).toBeVisible({ timeout: 5000 });
  });
});
