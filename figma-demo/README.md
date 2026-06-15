# Figma Demo — Antigravity Trading Terminal

Local design-system artifacts extracted from this app's code. Use these when Figma MCP is connected, or as a reference for manual Figma work.

## What's here

| File | Purpose |
|------|---------|
| [`tokens.json`](./tokens.json) | Single source of truth — colors, layout, spacing, typography from `index.css` + `themePresets.js` |
| [`scripts/01-create-variables.js`](./scripts/01-create-variables.js) | `use_figma` script: Figma variable collections (Light/Dark) |
| [`scripts/02-foundations-page.js`](./scripts/02-foundations-page.js) | `use_figma` script: Foundations swatch page |
| [`scripts/03-preferences-drawer.js`](./scripts/03-preferences-drawer.js) | `use_figma` script: Preferences drawer (Theme tab) |
| [`capture-preferences.js`](./capture-preferences.js) | Puppeteer capture of live app for pixel reference |
| [`reference/`](./reference/) | Screenshots from capture (generated) |

## Code sources

- **CSS tokens:** [`frontend/src/index.css`](../frontend/src/index.css) (`:root` + `.dark`)
- **Chart presets:** [`frontend/src/settings/themePresets.js`](../frontend/src/settings/themePresets.js)
- **UI component:** [`frontend/src/components/SettingsPanel.jsx`](../frontend/src/components/SettingsPanel.jsx)

## Run with Figma MCP (when connected)

1. **Authenticate:** Cursor Settings → MCP → Figma → Connect
2. **Create file:** In agent chat, run `/figma-create-new-file design Antigravity Trading Terminal`
3. **Run scripts in order** (pass `fileKey` from step 2):

```
use_figma → scripts/01-create-variables.js   (skillNames: figma-use, figma-generate-library)
use_figma → scripts/02-foundations-page.js
use_figma → scripts/03-preferences-drawer.js
```

4. **Optional pixel capture:** With dev server running, also call `generate_figma_design` on the same file while Preferences is open (`Ctrl+,`), then reconcile spacing against script output.

## Capture reference locally (no Figma)

```bash
cd frontend && npm run dev
# In another terminal:
node figma-demo/capture-preferences.js
```

Output lands in `figma-demo/reference/`.

## Why this is valuable for this app

The terminal's theming is split across CSS variables, JS presets, and a shadcn Settings panel. These artifacts:

- Document **actual** dark/light/chart colors in one manifest
- Give designers a **Preferences drawer** spec tied to `SettingsPanel.jsx`
- Provide ready-to-run Figma scripts so code and design stay in sync when MCP is available

## Next steps (optional)

- Add Code Connect mappings for shadcn `Button`, `Tabs`, `Sheet` → [`frontend/src/components/ui/`](../frontend/src/components/ui/)
- Extend `03-preferences-drawer.js` with Chart, Layout, and System tabs
- Bind Figma frame fills to variables created in step 01 (currently uses inline dark-theme hex for standalone readability)
