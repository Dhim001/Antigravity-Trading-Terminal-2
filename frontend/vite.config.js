import path from 'path'
import { fileURLToPath } from 'url'
import fs from 'fs'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

/** Dev UI port → backend ports (must match scripts/terminal-profiles.ps1). */
const PROFILE_PORTS_BY_DEV = {
  5173: { http: '8766', ws: '8765', profile: 'sim' },
  5174: { http: '8776', ws: '8775', profile: 'ib' },
  5175: { http: '8786', ws: '8785', profile: 'massive' },
}

function parseEnvFile(filePath) {
  const out = {}
  if (!fs.existsSync(filePath)) return out
  for (const line of fs.readFileSync(filePath, 'utf8').split('\n')) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) continue
    const eq = trimmed.indexOf('=')
    if (eq <= 0) continue
    out[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim()
  }
  return out
}

function cliDevPort() {
  const idx = process.argv.indexOf('--port')
  if (idx >= 0 && process.argv[idx + 1]) {
    const n = Number(process.argv[idx + 1])
    if (Number.isFinite(n) && n > 0) return n
  }
  return null
}

function resolveBackendPorts(env, devPort) {
  const profileKey = (
    env.VITE_TERMINAL_PROFILE
    || process.env.VITE_TERMINAL_PROFILE
    || PROFILE_PORTS_BY_DEV[devPort]?.profile
    || ''
  ).toLowerCase()

  const profileFile = profileKey
    ? parseEnvFile(path.join(__dirname, 'env.profiles', `${profileKey}.env`))
    : {}

  const httpPort =
    env.VITE_BACKEND_HTTP_PORT
    || profileFile.VITE_BACKEND_HTTP_PORT
    || PROFILE_PORTS_BY_DEV[devPort]?.http
    || '8766'

  const wsPort =
    env.VITE_BACKEND_WS_PORT
    || profileFile.VITE_BACKEND_WS_PORT
    || PROFILE_PORTS_BY_DEV[devPort]?.ws
    || '8765'

  return { httpPort, wsPort, profileKey }
}

function backendProxy(httpPort, wsPort) {
  const httpTarget = `http://127.0.0.1:${httpPort}`
  const wsTarget = `ws://127.0.0.1:${wsPort}`
  return {
    '/api': { target: httpTarget, changeOrigin: true },
    '/health': { target: httpTarget, changeOrigin: true },
    '/metrics': { target: httpTarget, changeOrigin: true },
    '/ws': {
      target: wsTarget,
      ws: true,
      changeOrigin: true,
      // Backend listens on / (not /ws); strip path for the upstream handshake.
      rewrite: (p) => {
        const stripped = p.replace(/^\/ws/, '')
        return stripped || '/'
      },
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  const devPort = Number(
    cliDevPort()
    || env.VITE_DEV_PORT
    || process.env.VITE_DEV_PORT
    || '5173',
  )
  const { httpPort, wsPort, profileKey } = resolveBackendPorts(env, devPort)
  const proxy = backendProxy(httpPort, wsPort)

  if (profileKey) {
    console.log(
      `[vite] ${profileKey} profile → HTTP :${httpPort}, WS :${wsPort} (UI :${devPort})`,
    )
  }

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
        host: '127.0.0.1',
        port: devPort,
        clientPort: devPort,
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
