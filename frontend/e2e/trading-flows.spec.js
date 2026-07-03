/**
 * Full SIM trading flows — place order, deploy bot, REST transport fallback.
 */
import { test, expect } from '@playwright/test';
import {
  gotoDashboard,
  openAlgoTab,
  openPositionsTab,
  placeMarketOrder,
  fillBracketSlTp,
  waitForBootstrap,
} from './helpers.js';

const API = process.env.E2E_API_URL || 'http://127.0.0.1:8766';

test.describe.configure({ mode: 'serial' });

test.describe('SIM trading flows (UI)', () => {
  test('market order via order entry opens position', async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);

    await placeMarketOrder(page, { presetPct: 25 });

    await expect(
      page.locator('[data-sonner-toast]').filter({ hasText: /success|filled|placed|executed/i }).first(),
    ).toBeVisible({ timeout: 20_000 });

    await openPositionsTab(page);
    await expect(page.locator('.dock-panel-tab--positions').getByText('BTCUSDT')).toBeVisible({
      timeout: 15_000,
    });
  });

  test('bracket SL/TP shows badge and pre-trade preview costs', async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);

    await page.locator('.order-entry-type-toggle').getByRole('button', { name: 'MARKET' }).click();
    await page.locator('.order-entry-qty-presets').getByRole('button', { name: '25%' }).click();
    await fillBracketSlTp(page, { slPct: 2, tpPct: 4 });

    await expect(page.getByText('Bracket', { exact: true })).toBeVisible();
    await expect(page.getByText('Pre-trade preview')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/Fee ~\$/)).toBeVisible();
    await expect(page.getByText(/R:R 1:/)).toBeVisible();
  });

  test('positions row exposes quick-trade actions', async ({ page }) => {
    await gotoDashboard(page);
    await waitForBootstrap(page);
    await openPositionsTab(page);

    const panel = page.locator('.dock-panel-tab--positions');
    const hasPosition = await panel.getByText('BTCUSDT').isVisible().catch(() => false);
    if (!hasPosition) {
      await placeMarketOrder(page, { presetPct: 25 });
      await expect(
        page.locator('[data-sonner-toast]').filter({ hasText: /success|filled|placed|executed/i }).first(),
      ).toBeVisible({ timeout: 20_000 });
      await openPositionsTab(page);
    }

    await expect(panel.getByRole('button', { name: '50%' }).first()).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Close' }).first()).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Rev' }).first()).toBeVisible();
  });

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

  test('session exposes order_capabilities', async ({ request }) => {
    const resp = await request.get(`${API}/api/v1/session`);
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    const caps = body.session?.terminal?.order_capabilities;
    expect(caps).toBeDefined();
    expect(caps.partial_close).toBe(true);
    expect(caps.order_preview_costs).toBe(true);
    expect(caps.broker).toBeDefined();
  });

  test('bracket preview includes SL/TP and cost estimates', async ({ request }) => {
    const resp = await request.post(`${API}/api/v1/orders/preview`, {
      data: {
        symbol: 'BTCUSDT',
        type: 'MARKET',
        side: 'BUY',
        quantity: 0.01,
        stop_loss_percent: 2,
        take_profit_percent: 4,
        bracket: true,
      },
    });
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.ok).toBe(true);
    const data = body.data ?? body.messages?.find((m) => m.type === 'order_preview')?.data;
    expect(data?.allowed).toBe(true);
    expect(data?.stop_loss_price).toBeGreaterThan(0);
    expect(data?.take_profit_price).toBeGreaterThan(0);
    expect(data?.costs?.estimated_fee).toBeGreaterThanOrEqual(0);
    expect(data?.risk_reward_ratio).toBeDefined();
  });

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
