import path from 'path'
import { fileURLToPath } from 'url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

function backendProxy(httpPort, wsPort) {
  const httpTarget = `http://127.0.0.1:${httpPort}`
  const wsTarget = `ws://127.0.0.1:${wsPort}`
  return {
    '/api': { target: httpTarget, changeOrigin: true },
    '/health': { target: httpTarget, changeOrigin: true },
    '/metrics': { target: httpTarget, changeOrigin: true },
    '/ws': { target: wsTarget, ws: true, changeOrigin: true },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  const httpPort = env.VITE_BACKEND_HTTP_PORT || '8766'
  const wsPort = env.VITE_BACKEND_WS_PORT || '8765'
  const devPort = Number(env.VITE_DEV_PORT || '5173')
  const proxy = backendProxy(httpPort, wsPort)

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      host: '127.0.0.1',
      port: devPort,
      strictPort: true,
      hmr: {
        overlay: false,
      },
      proxy,
    },
    preview: {
      host: '127.0.0.1',
      port: devPort,
      strictPort: true,
      proxy,
    },
  }
})
