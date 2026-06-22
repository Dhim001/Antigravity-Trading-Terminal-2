import { expect } from '@playwright/test';

const SETTINGS_KEY = 'terminal_settings_v1';

function buildSettings(overrides = {}) {
  const workspaceOverrides = overrides.workspace || {};
  const { workspace: _ws, ...rest } = overrides;
  return {
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
      ...workspaceOverrides,
    },
    chartLayout: { timeframe: '1m', chartType: 'candle', activeIndicators: {} },
    workspacePresets: [],
    alerts: [],
    onboardingCompleted: true,
    ...rest,
  };
}

/** Seed persisted settings before navigation (Playwright-serializable args). */
export async function seedSettings(page, overrides = {}) {
  const data = buildSettings(overrides);
  await page.addInitScript(
    ({ key, payload }) => {
      localStorage.setItem(key, JSON.stringify(payload));
    },
    { key: SETTINGS_KEY, payload: data },
  );
}

/** @deprecated Use seedSettings(page, overrides) — closures are not serialized by Playwright. */
export function defaultSettingsInitScript(overrides = {}) {
  const data = buildSettings(overrides);
  return () => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(data));
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
export async function gotoDashboard(page, { dismissOnboarding = true, settings = null } = {}) {
  await seedSettings(page, {
    onboardingCompleted: true,
    ...(settings || {}),
    workspace: {
      layoutMode: 'trade',
      zenMode: false,
      dockCollapsed: false,
      rightPanelCollapsed: false,
      dockHeight: 320,
      dockActiveTab: 'positions',
      dockGroup: 'portfolio',
      viewMode: 'single',
      ...(settings?.workspace || {}),
    },
  });
  await page.goto('/');
  await expect(page.locator('.dashboard-container')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText('Watchlist', { exact: true })).toBeVisible();
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

/** Submit a SIM market order from the order entry panel. */
export async function placeMarketOrder(page, { side = 'BUY', quantity, presetPct = 25 } = {}) {
  if (side === 'SELL') {
    await page.locator('.order-entry-side-toggle').getByRole('button', { name: /SELL/i }).click();
  }
  if (quantity != null) {
    await page.getByLabel('Quantity').fill(String(quantity));
  } else if (presetPct != null) {
    await page.locator('.order-entry-qty-presets').getByRole('button', { name: `${presetPct}%` }).click();
  }
  await page.waitForTimeout(450);
  const submit = page.locator('button[form="order-entry-form"]');
  await expect(submit).toBeEnabled({ timeout: 20_000 });
  await expect(submit).toContainText(new RegExp(`Place ${side}`, 'i'));
  await submit.click();
}
