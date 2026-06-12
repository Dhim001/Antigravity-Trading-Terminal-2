/** API base URLs — empty HTTP base uses same-origin (Vite dev proxy → :8766). */

export const HTTP_BASE_URL = import.meta.env.VITE_HTTP_BASE_URL ?? '';
export const WS_URL = import.meta.env.VITE_WS_URL ?? 'ws://127.0.0.1:8765';
