import { test, expect } from '@playwright/test';
import {
  gotoDashboard,
  openAlgoTab,
  waitForBootstrap,
} from './helpers.js';

test.describe('Backtest Lab', () => {
  test.beforeEach(async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);
    await openAlgoTab(page);
  });

  test('opens Lab Optimizer tab from OPTIMIZE button', async ({ page }) => {
    await page.getByRole('button', { name: /^OPTIMIZE$/i }).click();
    const lab = page.getByRole('dialog', { name: /Backtest Lab/i });
    await expect(lab).toBeVisible({ timeout: 10_000 });
    await expect(lab.getByRole('tab', { name: 'Optimizer' })).toBeVisible();
    await expect(lab.getByText('Parameter sweep')).toBeVisible();
  });

  test('optimizer shows sweep mode selector and run controls', async ({ page }) => {
    await page.getByRole('button', { name: /^OPTIMIZE$/i }).click();
    const lab = page.getByRole('dialog', { name: /Backtest Lab/i });
    await expect(lab).toBeVisible({ timeout: 10_000 });
    await expect(lab.getByText('Sweep mode')).toBeVisible();
    await expect(lab.getByRole('button', { name: /Run sweep/i })).toBeVisible();
  });

  test('tick strategy enables BACKTEST button', async ({ page }) => {
    const modeSelect = page.getByLabel('Execution mode');
    if (await modeSelect.isVisible().catch(() => false)) {
      await modeSelect.click();
      await page.getByRole('option', { name: /Tick/i }).click();
    }
    const backtestBtn = page.getByRole('button', { name: /BACKTEST/i });
    await expect(backtestBtn).toBeEnabled();
  });
});
