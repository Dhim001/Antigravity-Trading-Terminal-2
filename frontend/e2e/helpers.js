import { expect } from '@playwright/test';

const SETTINGS_KEY = 'terminal_settings_v1';

/** Seed workspace settings before navigation (Playwright addInitScript). */
export function defaultSettingsInitScript(overrides = {}) {
  return () => {
    const base = {
      version: 1,
      theme: 'dark',
      workspace: {
        layoutMode: 'trade',
        zenMode: false,
        dockCollapsed: false,
        rightPanelCollapsed: false,
        dockHeight: 320,
        dockActiveTab: 'positions',
        dockGroup: 'portfolio',
        viewMode: 'single',
        ...overrides.workspace,
      },
      chartLayout: { timeframe: '1m', chartType: 'candle', activeIndicators: {} },
      workspacePresets: [],
      alerts: [],
      onboardingCompleted: true,
      ...overrides,
    };
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(base));
  };
}

/** Dismiss onboarding tour if visible. */
export async function dismissOnboardingIfVisible(page) {
  const skip = page.getByRole('button', { name: 'Skip tour' });
  if (await skip.isVisible().catch(() => false)) {
    await skip.click();
  }
}

/** Navigate to dashboard and wait for shell chrome. */
export async function gotoDashboard(page, { dismissOnboarding = false } = {}) {
  await page.goto('/');
  await expect(page.locator('.dashboard-container')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText('Watchlist')).toBeVisible();
  if (dismissOnboarding) {
    await dismissOnboardingIfVisible(page);
  }
}

/** Open Algo Bot dock tab via keyboard shortcut. */
export async function openAlgoTab(page) {
  await page.keyboard.press('Control+b');
  await expect(page.getByText('Deploy Bot', { exact: true })).toBeVisible({ timeout: 10_000 });
}

/** Insights Hub sheet (dialog role — avoids matching hidden dock analyst tab). */
export function insightsHubLocator(page) {
  return page.getByRole('dialog', { name: 'Insights Hub' });
}

/** Open Insights Hub via keyboard shortcut (⌘I). */
export async function openInsightsHub(page) {
  await page.keyboard.press('Control+i');
  await expect(insightsHubLocator(page)).toBeVisible({ timeout: 10_000 });
}

/** Open Analyst sub-tab inside Insights Hub. */
export async function openAnalystInHub(page) {
  await openInsightsHub(page);
  const hub = insightsHubLocator(page);
  await hub.getByRole('tab', { name: 'Analyst' }).click();
  await expect(hub.getByRole('button', { name: /^Analyze$/i })).toBeVisible({ timeout: 10_000 });
}

/** Open Analyst dock tab (Intelligence group). */
export async function openAnalystDockTab(page) {
  await openDockTab(page, 'Analyst', 'Intelligence');
}

/** @deprecated Use openAnalystInHub or openAnalystDockTab */
export async function openAnalystTab(page) {
  return openAnalystInHub(page);
}

/** BAR_CLOSE strategy template names from /api/v1/strategies catalog (after bootstrap). */
export const STRATEGY_TEMPLATE_NAMES = [
  'MACD + RSI',
  'Bollinger RSI Stochastic',
  'Supertrend + ADX',
  'VWAP Pullback',
  'Chart Analyst Agent',
];

/** Switch dock to a tab (handles grouped dock rails). */
export async function openDockTab(page, tabName, groupName) {
  const dock = page.locator('.bottom-dock');
  if (groupName) {
    await dock.getByRole('button', { name: groupName }).click();
  }
  await dock.getByRole('tab', { name: tabName }).click();
}

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
