import { test, expect } from '@playwright/test';
import { gotoDashboard, insightsHubLocator, openAnalystInHub, waitForBootstrap } from './helpers.js';

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

  test('Insights Hub opens via keyboard shortcut with analyst tab', async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);
    await openAnalystInHub(page);
    await expect(page.getByRole('button', { name: /^Analyze$/i })).toBeVisible();
  });

  test('command palette navigates to Insights Hub', async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);
    await page.keyboard.press('Control+k');
    await expect(page.getByPlaceholder('Search symbol or command…')).toBeVisible();
    await page.getByPlaceholder('Search symbol or command…').fill('insights');
    await page.getByLabel('Navigation').getByText('Insights Hub').click();
    const hub = insightsHubLocator(page);
    await expect(hub).toBeVisible();
    await hub.getByRole('tab', { name: 'Analyst' }).click();
    await expect(hub.getByRole('button', { name: /^Analyze$/i })).toBeVisible();
  });
});
