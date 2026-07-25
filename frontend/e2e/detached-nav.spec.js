import { test, expect } from '@playwright/test';
import { dismissOnboardingIfVisible, seedSettings } from './helpers.js';

/**
 * Live check: links to/from detached standalone panels.
 * Targets the running Vite UI (set E2E_BASE_URL, e.g. http://127.0.0.1:5176).
 *
 * Note: FlexLayout shell no longer uses `.dashboard-container`; wait on brand chrome.
 */
async function gotoTerminal(page) {
  await seedSettings(page, {
    onboardingCompleted: true,
    workspace: {
      layoutMode: 'trade',
      zenMode: false,
      dockCollapsed: false,
      rightPanelCollapsed: false,
      dockHeight: 320,
      dockActiveTab: 'positions',
      dockGroup: 'portfolio',
      viewMode: 'single',
      detachedTabs: [],
    },
  });
  await page.goto('/');
  await expect(page.locator('.brand-title')).toHaveText('ANTIGRAVITY', { timeout: 20_000 });
  await dismissOnboardingIfVisible(page);
  // Bootstrap may still be loading — give WS/REST a moment.
  await page.waitForTimeout(800);
}

test.describe('Detached panel link navigation', () => {
  test('focusStandaloneForDockTab opens ?panel= when tab is detached', async ({ page }) => {
    await gotoTerminal(page);

    const result = await page.evaluate(async () => {
      const mod = await import('/src/lib/standalonePanels.js');
      const calls = [];
      const realOpen = window.open.bind(window);
      window.open = (url, name, features) => {
        calls.push({ url: String(url), name, features });
        const w = realOpen('about:blank', name || 'tt-probe', 'width=100,height=100');
        return w;
      };
      try {
        const opened = mod.focusStandaloneForDockTab('ml-training', ['ml-training']);
        const skipped = mod.focusStandaloneForDockTab('ml-training', []);
        return {
          opened,
          skipped,
          calls,
          urlHasPanel: calls[0]?.url?.includes('panel=ml-lab') || false,
        };
      } finally {
        window.open = realOpen;
        for (const id of Object.keys(window.__ttDetachedPanels || {})) {
          try {
            window.__ttDetachedPanels[id]?.close?.();
          } catch {
            /* ignore */
          }
          delete window.__ttDetachedPanels[id];
        }
      }
    });

    expect(result.opened).toBe(true);
    expect(result.skipped).toBe(false);
    expect(result.urlHasPanel).toBe(true);
  });

  test('dock-tab on main focuses detached ML Lab window', async ({ page, context }) => {
    await gotoTerminal(page);

    await page.evaluate(async () => {
      const { useSettingsStore } = await import('/src/store/useSettingsStore.js');
      useSettingsStore.getState().updateWorkspace({ detachedTabs: ['ml-training'] });
    });

    const popupPromise = context.waitForEvent('page', { timeout: 15_000 });

    await page.evaluate(async () => {
      const { focusWorkspacePanel } = await import('/src/lib/workspaceNav.js');
      focusWorkspacePanel('ml-training');
    });

    const popup = await popupPromise;
    await popup.waitForLoadState('domcontentloaded');
    expect(popup.url()).toContain('panel=ml-lab');
    await expect(popup.getByText(/standalone/i)).toBeVisible({ timeout: 20_000 });

    const pagesBefore = context.pages().length;
    await page.evaluate(async () => {
      const { focusWorkspacePanel } = await import('/src/lib/workspaceNav.js');
      focusWorkspacePanel('ml-training');
    });
    await page.waitForTimeout(500);
    expect(context.pages().length).toBe(pagesBefore);

    await popup.close();
  });

  test('dock-tab from standalone relays to main terminal', async ({ page, context }) => {
    await gotoTerminal(page);

    const popupPromise = context.waitForEvent('page', { timeout: 15_000 });
    await page.evaluate(() => {
      window.open(`${window.location.origin}/?panel=insights`, 'tt-insights', 'width=900,height=700');
    });
    const popup = await popupPromise;
    await popup.waitForLoadState('domcontentloaded');
    await expect(popup.getByText(/standalone/i)).toBeVisible({ timeout: 20_000 });

    await page.evaluate(() => {
      window.__ttNavHits = [];
      window.addEventListener('dock-tab', (e) => {
        window.__ttNavHits.push(e.detail);
      });
      window.__ttBcHits = [];
      try {
        const bc = new BroadcastChannel('tt-terminal-nav');
        bc.onmessage = (ev) => window.__ttBcHits.push(ev.data);
        window.__ttBc = bc;
      } catch {
        /* ignore */
      }
    });

    await popup.evaluate(() => {
      window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'algo' }));
    });

    await expect
      .poll(async () => page.evaluate(() => {
        const hits = window.__ttNavHits || [];
        const bc = window.__ttBcHits || [];
        return hits.includes('algo')
          || bc.some((m) => m?.type === 'focus-panel' && m?.panelId === 'algo');
      }), { timeout: 10_000 })
      .toBe(true);

    await popup.close();
  });

  test('symbol set in standalone syncs to main via localStorage', async ({ page, context }) => {
    await gotoTerminal(page);

    const before = await page.evaluate(() => window.__ttGetState?.()?.activeSymbol || null);

    const popupPromise = context.waitForEvent('page', { timeout: 15_000 });
    await page.evaluate(() => {
      window.open(`${window.location.origin}/?panel=ml-lab`, 'tt-ml-lab', 'width=900,height=700');
    });
    const popup = await popupPromise;
    await popup.waitForLoadState('domcontentloaded');
    await expect(popup.getByText(/standalone/i)).toBeVisible({ timeout: 20_000 });

    const probe = `E2E${Date.now().toString().slice(-6)}USDT`;
    await popup.evaluate(async (sym) => {
      const { useStore } = await import('/src/store/useStore.js');
      useStore.getState().setActiveSymbol(sym);
    }, probe);

    await expect
      .poll(async () => page.evaluate(() => window.__ttGetState?.()?.activeSymbol), {
        timeout: 10_000,
      })
      .toBe(probe);

    if (before) {
      await page.evaluate((sym) => {
        window.__ttGetState?.().setActiveSymbol?.(sym);
      }, before);
    }

    await popup.close();
  });
});
