import { test, expect } from '@playwright/test';

const API = process.env.E2E_API_URL || 'http://127.0.0.1:8766';

test.describe('Trading flows API', () => {
  test('order preview returns allowed structure', async ({ request }) => {
    const resp = await request.post(`${API}/api/v1/orders/preview`, {
      data: {
        symbol: 'BTCUSDT',
        type: 'MARKET',
        side: 'BUY',
        quantity: 0.001,
      },
    });
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.ok).toBe(true);
    expect(body.type).toBe('order_preview');
    expect(body.data).toHaveProperty('allowed');
    expect(body.data).toHaveProperty('notional');
  });

  test('market scan returns rows', async ({ request }) => {
    const resp = await request.post(`${API}/api/v1/scanner/scan`, {
      data: {
        symbols: ['BTCUSDT', 'ETHUSDT'],
        signal_filter: 'any',
        sort_by: 'score',
      },
    });
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.ok).toBe(true);
    const data = body.data ?? body.messages?.find((m) => m.type === 'scan_results')?.data;
    expect(data?.rows).toBeDefined();
    expect(Array.isArray(data.rows)).toBe(true);
  });

  test('chart analyze includes sub_reports v2', async ({ request }) => {
    const resp = await request.post(`${API}/api/v1/agent/analyze`, {
      data: { symbol: 'BTCUSDT' },
    });
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    const insight = body.data ?? body.messages?.find((m) => m.type === 'agent_insight')?.data;
    if (insight?.version >= 2) {
      expect(insight.sub_reports).toBeDefined();
      expect(insight.sub_reports.trend).toBeDefined();
      expect(insight.sub_reports.momentum).toBeDefined();
      expect(insight.sub_reports.risk).toBeDefined();
    }
  });
});
