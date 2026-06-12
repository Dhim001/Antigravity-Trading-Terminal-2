import { test, expect } from '@playwright/test';

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
    const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:4173';
    await page.goto(baseUrl);
    await expect(page.getByText('ANTIGRAVITY')).toBeVisible({ timeout: 15000 });
  });

  test('REST bootstrap hydrates account via proxy', async ({ request }) => {
    const apiUrl = process.env.E2E_API_URL || 'http://127.0.0.1:8766';
    const resp = await request.get(`${apiUrl}/api/v1/account`);
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.ok).toBe(true);
    expect(body.type).toBe('account_update');
  });
});
