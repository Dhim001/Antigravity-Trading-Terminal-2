/**
 * Minimal preload — exposes profile hint for optional UI branding later.
 */
const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('terminalDesktop', {
  isDesktop: true,
  platform: process.platform,
  profile: (process.env.TERMINAL_PROFILE || process.env.VITE_TERMINAL_PROFILE || 'sim').toLowerCase(),
});
