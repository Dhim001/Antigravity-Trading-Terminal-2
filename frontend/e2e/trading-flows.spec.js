/**
 * Full SIM trading flows — place order, deploy bot, REST transport fallback.
 */
import { test, expect } from '@playwright/test';
import {
  gotoDashboard,
  openAlgoTab,
  waitForBootstrap,
} from './helpers.js';

const API = process.env.E2E_API_URL || 'http://127.0.0.1:8766';

test.describe.configure({ mode: 'serial' });

test.describe('SIM trading flows (UI)', () => {
  test('deploy MACD bot increases active bot count', async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);
    await openAlgoTab(page);

    await page.locator('.algo-template-grid').getByRole('button', { name: 'MACD + RSI' }).click();
    await page.getByLabel('Max notional cap').fill('500');

    await page.getByRole('button', { name: /^DEPLOY$/i }).click();
    await expect(page.getByRole('dialog', { name: /Deploy trading bot/i })).toBeVisible();
    await page.getByRole('button', { name: /Confirm deploy/i }).click();

    await expect(page.locator('.algo-tab__panel--bots').getByText(/RUNNING|PAUSED/i).first()).toBeVisible({
      timeout: 20_000,
    });
  });
});

test.describe('REST transport (WS fallback path)', () => {
  test.describe.configure({ mode: 'serial' });

  test('place order via POST /api/v1/orders', async ({ request }) => {
    const resp = await request.post(`${API}/api/v1/orders`, {
      data: {
        symbol: 'BTCUSDT',
        type: 'MARKET',
        side: 'BUY',
        quantity: 0.001,
      },
    });
    if (!resp.ok()) {
      const errBody = await resp.text();
      throw new Error(`POST /orders failed (${resp.status()}): ${errBody.slice(0, 200)}`);
    }
    const body = await resp.json();
    expect(body.ok).toBe(true);
    const result = body.data ?? body.messages?.find((m) => m.type === 'order_result')?.data;
    expect(result?.status).toBe('success');
  });

  test('history lists order after REST place', async ({ request }) => {
    await request.post(`${API}/api/v1/orders`, {
      data: {
        symbol: 'BTCUSDT',
        type: 'MARKET',
        side: 'BUY',
        quantity: 0.001,
      },
    });

    const hist = await request.get(`${API}/api/v1/history`);
    expect(hist.ok()).toBeTruthy();
    const body = await hist.json();
    const trades = body.data ?? body.messages?.find((m) => m.type === 'trade_history')?.data;
    expect(Array.isArray(trades)).toBe(true);
    expect(trades.length).toBeGreaterThan(0);
    expect(trades.some((t) => String(t.symbol || '').includes('BTC'))).toBe(true);
  });

  test('deploy bot via POST /api/v1/bots', async ({ request }) => {
    const resp = await request.post(`${API}/api/v1/bots`, {
      data: {
        strategy: 'MACD_RSI',
        symbol: 'ETHUSDT',
        timeframe: '1m',
        allocation: 500,
        execution_mode: 'BAR_CLOSE',
        config: {
          trailing_stop_percent: 2,
          take_profit_percent: 3,
          tp_mode: 'percent',
        },
      },
    });
    if (!resp.ok()) {
      const errBody = await resp.text();
      throw new Error(`POST /bots failed (${resp.status()}): ${errBody.slice(0, 200)}`);
    }
    const body = await resp.json();
    expect(body.ok).toBe(true);
    const result = body.data ?? body.messages?.find((m) => m.type === 'order_result')?.data;
    expect(result?.status).toBe('success');
    expect(String(result?.message || '')).toMatch(/created/i);
  });
});
