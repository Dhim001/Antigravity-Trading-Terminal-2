# Desktop window shell

Opens the trading terminal in a **standalone window** (Electron) instead of a browser tab.

## Quick start (Windows)

From the repo root:

```powershell
.\scripts\start-desktop.ps1                  # Sim (default)
.\scripts\start-desktop.ps1 -Profile Massive
.\scripts\start-desktop.ps1 -Profile Ib
```

The script will:

1. Start the backend if it is not already healthy
2. Start the Vite dev server in the background if needed
3. Open the Electron window pointed at `http://127.0.0.1:5173` (or the profile port)

Closing the desktop window **does not** stop the backend or Vite — same as the regular `start-*.ps1` scripts. Use `-Recycle` to restart listeners.

## Manual (backend + Vite already running)

```powershell
cd desktop
npm install    # first time only
npm run start:sim
```

## Profiles

| Profile | UI port | npm script |
|---------|---------|------------|
| sim | 5173 | `npm run start:sim` |
| ib | 5174 | `npm run start:ib` |
| massive | 5175 | `npm run start:massive` |

## Troubleshooting

**Blank / dark window** — close Electron, restart Vite (`-Recycle` if needed), then relaunch. The desktop shell skips the service worker (it conflicts with Vite dev). Press **Alt** → View → Toggle Developer Tools to see renderer errors.

**GPU crash on launch** — set before running:
```powershell
$env:TERMINAL_ELECTRON_SOFTWARE_GPU = '1'
.\scripts\start-desktop.ps1 -Profile Massive
```

- DevTools: **View → Toggle Developer Tools** (menu bar appears when Alt is pressed if hidden).
- PWA install remains available in Chrome/Edge if you prefer that route.
- Production static builds (`npm run build` + nginx) are not wired into Electron yet; dev-server mode is the supported desktop path.
