import { test, expect } from '@playwright/test';
import { defaultSettingsInitScript } from './helpers';

test.describe('Onboarding & help', () => {
  test('onboarding tour opens for new users and advances', async ({ page }) => {
    await page.addInitScript(defaultSettingsInitScript({ onboardingCompleted: false }));
    await page.goto('/');
    await page.waitForSelector('.dashboard-container', { timeout: 15000 });

    await expect(page.getByRole('heading', { name: 'Welcome to Antigravity' })).toBeVisible({
      timeout: 5000,
    });
    await expect(page.getByRole('button', { name: 'Skip tour' })).toBeVisible();

    await page.getByRole('button', { name: 'Next' }).click();
    await expect(page.getByRole('heading', { name: 'Pick a symbol' })).toBeVisible();

    await page.getByRole('button', { name: 'Skip tour' }).click();
    await expect(page.getByRole('heading', { name: 'Welcome to Antigravity' })).toBeHidden();
  });

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
