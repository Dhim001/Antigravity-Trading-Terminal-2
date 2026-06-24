/**
 * Optional E2E against a LIVE_IB backend with IB Gateway (paper port 4002).
 *
 * Run locally after `.\scripts\start-ib.ps1` with Gateway up:
 *   E2E_IB_API_URL=http://127.0.0.1:8776 npx playwright test e2e/ib-gateway.spec.js
 */
import { test, expect } from '@playwright/test';

const ibApiUrl = process.env.E2E_IB_API_URL;

test.describe('IB Gateway (optional)', () => {
  test.skip(!ibApiUrl, 'Set E2E_IB_API_URL to run IB Gateway E2E (e.g. http://127.0.0.1:8776)');

  test('health reports LIVE_IB and IB feed status', async ({ request }) => {
    const resp = await request.get(`${ibApiUrl}/health`);
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.ok).toBe(true);
    expect(body.terminal_mode).toBe('LIVE_IB');
    expect(body.ib).toBeDefined();
    expect(typeof body.ib.connected).toBe('boolean');
  });

  test('session exposes equity symbols without crypto', async ({ request }) => {
    const resp = await request.get(`${ibApiUrl}/api/v1/session`);
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    const symbols = body.session?.terminal?.symbols ?? [];
    expect(symbols.length).toBeGreaterThan(0);
    expect(symbols.some((s) => String(s).includes('USDT'))).toBe(false);
  });

  test('account snapshot loads via REST', async ({ request }) => {
    const resp = await request.get(`${ibApiUrl}/api/v1/account`);
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.ok).toBe(true);
    expect(body.type).toBe('account_update');
  });
});
