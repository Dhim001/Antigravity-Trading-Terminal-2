/**
 * Optional E2E against a LIVE_MASSIVE backend with Massive API key configured.
 *
 * Run locally after `.\scripts\start-massive.ps1`:
 *   E2E_MASSIVE_API_URL=http://127.0.0.1:8786 npx playwright test e2e/massive-gateway.spec.js
 */
import { test, expect } from '@playwright/test';

const massiveApiUrl = process.env.E2E_MASSIVE_API_URL;

test.describe('Massive feed (optional)', () => {
  test.skip(!massiveApiUrl, 'Set E2E_MASSIVE_API_URL to run Massive E2E (e.g. http://127.0.0.1:8786)');

  test('health reports LIVE_MASSIVE and massive feed status', async ({ request }) => {
    const resp = await request.get(`${massiveApiUrl}/health`);
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.ok).toBe(true);
    expect(body.terminal_mode).toBe('LIVE_MASSIVE');
    expect(body.massive).toBeDefined();
    expect(typeof body.massive.stocks_mode).toBe('string');
    expect(typeof body.massive.crypto_mode).toBe('string');
    expect(typeof body.massive.quotes_enabled).toBe('boolean');
  });

  test('session exposes equities and crypto symbols', async ({ request }) => {
    const resp = await request.get(`${massiveApiUrl}/api/v1/session`);
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    const symbols = body.session?.terminal?.symbols ?? [];
    expect(symbols.length).toBeGreaterThan(10);
    expect(symbols.some((s) => String(s).includes('USDT'))).toBe(true);
    expect(symbols.some((s) => s === 'AAPL')).toBe(true);
  });

  test('observability includes massive counters when feed active', async ({ request }) => {
    const resp = await request.get(`${massiveApiUrl}/health`);
    const body = await resp.json();
    if (body.observability) {
      expect(body.observability).toHaveProperty('massive_bars_received_total');
    }
  });
});
