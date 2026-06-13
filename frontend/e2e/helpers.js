import { expect } from '@playwright/test';

/** Navigate to dashboard and wait for shell chrome. */
export async function gotoDashboard(page) {
  await page.goto('/');
  await expect(page.locator('.dashboard-container')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText('Watchlist')).toBeVisible();
}

/** Open Algo Bot dock tab via keyboard shortcut (works when tab labels are icon-only). */
export async function openAlgoTab(page) {
  await page.keyboard.press('Control+b');
  await expect(page.getByText('Deploy Bot', { exact: true })).toBeVisible({ timeout: 10_000 });
}

/** Strategy template display names from useStore defaults. */
export const STRATEGY_TEMPLATE_NAMES = [
  'Bull Market Scalper',
  'Trend Follower',
  'Mean Reversion Scalp',
  'VWAP Pullback',
];

/** Wait until REST bootstrap completes (connection badge leaves loading state). */
export async function waitForBootstrap(page) {
  const loading = page.getByText('Loading snapshot via REST…');
  if (await loading.isVisible().catch(() => false)) {
    await expect(loading).toBeHidden({ timeout: 20_000 });
  }
  await expect(
    page.locator(
      'header [title*="connected"], header [title*="REST"], header [title*="retrying"], header [title*="Live"]',
    ).first(),
  ).toBeVisible({ timeout: 20_000 });
}
