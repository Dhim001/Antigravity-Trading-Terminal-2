import { test, expect } from '@playwright/test';
import { gotoDashboard, openAnalystTab, waitForBootstrap } from './helpers.js';

test.describe('Chart Analyst (Phase 6)', () => {
  test('agent insights API responds', async ({ request }) => {
    const apiUrl = process.env.E2E_API_URL || 'http://127.0.0.1:8766';
    const resp = await request.get(`${apiUrl}/api/v1/agent/insights/BTCUSDT?limit=5`);
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.ok).toBe(true);
    expect(body.symbol).toBe('BTCUSDT');
    expect(Array.isArray(body.insights)).toBe(true);
  });

  test('analyst dock tab opens via keyboard shortcut', async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);
    await openAnalystTab(page);
    await expect(page.getByRole('button', { name: /^Analyze$/i })).toBeVisible();
  });

  test('command palette navigates to analyst tab', async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);
    await page.keyboard.press('Control+k');
    await expect(page.getByPlaceholder('Search symbol or command…')).toBeVisible();
    await page.getByPlaceholder('Search symbol or command…').fill('analyst');
    await page.getByText('Chart Analyst History').click();
    await expect(page.getByText('Chart Analyst', { exact: true })).toBeVisible();
  });
});
