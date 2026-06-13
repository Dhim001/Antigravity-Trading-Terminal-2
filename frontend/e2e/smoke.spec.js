import { test, expect } from '@playwright/test';
import {
  gotoDashboard,
  openAlgoTab,
  waitForBootstrap,
  STRATEGY_TEMPLATE_NAMES,
} from './helpers.js';

test.describe('Trading terminal smoke', () => {
  test('backend health endpoint responds', async ({ request }) => {
    const apiUrl = process.env.E2E_API_URL || 'http://127.0.0.1:8766';
    const resp = await request.get(`${apiUrl}/health`);
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.ok).toBe(true);
    expect(body.service).toBe('trading-terminal');
  });

  test('dashboard loads and shows brand header', async ({ page }) => {
    await gotoDashboard(page);
    await expect(page.locator('.brand-title')).toHaveText('ANTIGRAVITY');
  });

  test('REST bootstrap hydrates account via proxy', async ({ request }) => {
    const apiUrl = process.env.E2E_API_URL || 'http://127.0.0.1:8766';
    const resp = await request.get(`${apiUrl}/api/v1/account`);
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.ok).toBe(true);
    expect(body.type).toBe('account_update');
  });

  test('bootstrap completes without layout regression', async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);

    await expect(page.getByText('Watchlist')).toBeVisible();
    await expect(page.getByText('Order Entry')).toBeVisible();
    await expect(page.locator('.bottom-dock')).toBeVisible();
  });

  test('algo tab shows strategy cards after keyboard shortcut', async ({ page }) => {
    await gotoDashboard(page);
    await openAlgoTab(page);

    await expect(page.locator('.algo-template-grid')).toBeVisible();
    for (const name of STRATEGY_TEMPLATE_NAMES) {
      await expect(page.getByRole('button', { name })).toBeVisible();
    }

    await expect(page.getByRole('button', { name: /BACKTEST/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /^DEPLOY$/i })).toBeVisible();
  });

  test('dock tab navigation switches content', async ({ page }) => {
    await gotoDashboard(page);

    await page.getByRole('tab', { name: /Positions/i }).click();
    await expect(page.getByText(/No open positions/i)).toBeVisible({ timeout: 10_000 });

    await openAlgoTab(page);
    await expect(page.getByText('Capital Allocation')).toBeVisible();
  });
});
