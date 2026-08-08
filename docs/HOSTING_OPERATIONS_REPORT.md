# Hosting & Operations Report — Antigravity Trading Terminal

This is a **single-operator, always-on trading lab**, not a multi-tenant SaaS. The UI is React/Vite (Electron optional). The backend is a Python **Starlette + uvicorn HTTP** process plus a **separate WebSocket** process, with bots/feeds/agents in-process. The repo already ships **Docker Compose** (Postgres + nginx UI + backend) and an optional Redis server/worker split.

---

## 1. What you are actually hosting

```text
Browser (or Electron) 
  ├── REST  → /api, /health  → Starlette :8766
  └── WS    → /ws            → websockets :8765  (must stay sticky / single instance)

Storage
  ├── Postgres (Compose) or SQLite (local profiles)
  ├── Volume: backend/data  (ML models, Optuna, journals, caches)
  └── Optional Redis (distributed bots / RQ backtests)

External APIs (outbound only)
  Alpaca · Binance · eToro · IB Gateway (local) · Massive · Finnhub · OpenRouter/Ollama · yfinance
```

| Piece | Stack | Persist? | Always-on? |
|--------|--------|----------|------------|
| **Frontend** | React 19 + Vite → static nginx | No (rebuild on deploy) | CDN/static is fine |
| **HTTP API** | Starlette/uvicorn | Stateless-ish | Yes |
| **WebSocket** | `websockets` on separate port | In-memory state | **Yes — critical** |
| **Bot / feed / agents** | Same backend process (or `worker`) | Needs DB + process up | **Yes** |
| **DB** | SQLite or Timescale/Postgres | **Yes** | Yes |
| **ML artifacts** | Files under `backend/data/*_models/` | **Yes** | Volume |
| **ML training** | Process pool / optional GPU | Checkpoints on volume | **Burst** (not 24/7 GPU) |
| **Electron** | Thin desktop shell | Local only | Not for cloud |

**Hard constraints for 24/7:**

- Do **not** put the API on serverless (Vercel/Netlify functions) — cold starts, no long WS, no bot loops.
- Keep **one** WS/feed “server” instance (or sticky session). Horizontal scale of identical `all` replicas will break clients.
- **IB** needs a colocated Interactive Brokers Gateway — usually a VPS or your own machine, not a typical PaaS.
- Broker keys stay in env/secrets; set `HTTP_API_KEY`, lock CORS, never expose Postgres publicly.

### Architecture summary (in-repo)

| Layer | Detail |
|--------|--------|
| Frontend | React 19 + Vite 8 + Zustand; optional Electron (`desktop/`) |
| Backend HTTP | Starlette + uvicorn (default `:8766`) |
| Backend WS | `websockets` library (default `:8765`) |
| Roles | `TERMINAL_ROLE=all` (monolith) · `server` · `worker` |
| Modes | `SIMULATED` · `LIVE_ALPACA` · `LIVE_BINANCE` · `LIVE_ETORO` · `LIVE_IB` · `LIVE_MASSIVE` |
| Compose UI | Host **8080** → nginx:80 (`docker-compose.yml`, `frontend/nginx.conf`) |
| Auth | Single-tenant; optional shared `HTTP_API_KEY` → `X-API-Key` |

**Dev profile ports** (`scripts/terminal-profiles.ps1`):

| Profile | UI | Backend WS | Backend HTTP |
|---------|-----|------------|--------------|
| Sim | 5173 | 8765 | 8766 |
| IB | 5174 | 8775 | 8776 |
| Massive | 5175 | 8785 | 8786 |
| Alpaca | 5176 | 8795 | 8796 |

**Must persist:** DB (orders, bots, archive bars, sentiment, notifications) and `backend/data/*_models/` if you care about trained ML. SQLite WAL siblings (`*-shm`, `*-wal`) travel with the DB file.

---

## 2. Recommended hosting by part of the app

### A. Production 24/7 (paper or live bots) — primary stack

| Component | Best host | Why | Alternative |
|-----------|-----------|-----|-------------|
| **Full stack (compose)** | **Hetzner Cloud** CX31+ (8–16 GB) or **DigitalOcean Droplet** | You already have `docker-compose.yml`; cheapest reliable always-on; volumes for Postgres + `backend_data` | Contabo / Vultr |
| **Ops UI on that VPS** | **Coolify** or **Dokku** on the same VPS | Git push → rebuild → restart; SSL via Traefik/Caddy | Plain `docker compose` + Caddy |
| **Managed PaaS (less SSH)** | **Render** (web + worker + Postgres + disk) | Clear always-on services, workers, managed Postgres | **Railway** (faster DX, watch usage $) |
| **WS-heavy / Docker-native** | **Fly.io** (1 region, persistent volume) | Excellent long-lived WS; volumes for models | Render with single instance |

**Practical default:** **Hetzner (or DO) + Docker Compose + Caddy/Coolify** using the existing compose file. Closest to how the repo is meant to run (`http://localhost:8080` pattern → public HTTPS).

**Sizing (24/7 trading, light–moderate ML):**

| Workload | Spec |
|----------|------|
| Sim / one live profile, few bots | 4 vCPU, **8 GB RAM**, 80+ GB SSD |
| Alpaca/Massive + archive + agents | 4–8 vCPU, **16 GB RAM** |
| Heavy backtests + train on same box | 16–32 GB RAM; prefer **offloading GPU** (below) |

Compose already maps nginx `/ws` with `proxy_read_timeout 86400` — keep that behind TLS (Caddy/Traefik).

---

### B. Frontend only (optional split)

| Use | Host | Notes |
|-----|------|--------|
| Static UI CDN | **Cloudflare Pages** or **Netlify** | Build `frontend/` with `VITE_HTTP_BASE_URL` + `VITE_WS_URL` pointing at API host |
| Same-origin (simplest) | **Keep UI in Compose nginx** | Avoids CORS/WS cross-origin pain — **recommended for production** |

**Avoid Vercel as the only “app” host** — fine for static UI, wrong for the Python backend.

---

### C. Database & storage

| Store | Host | Recommendation |
|-------|------|----------------|
| **Postgres / Timescale** | Same VPS volume **or** Render/Railway/DO Managed Postgres | Prefer managed if you don’t want backup ops; keep in same region as backend |
| **SQLite** | Single VPS disk only | OK for personal lab; **not** multi-instance; Fly LiteFS only if you stay single-writer |
| **`backend/data` (ML)** | Persistent volume (Hetzner volume / DO block / Render disk / Fly volume) | **Must** backup; models are not in Git |
| **Redis** | Same compose `distributed` profile, or Upstash/Redis Cloud | Only if you use server/worker or RQ |
| **Object backups** | **Backblaze B2** or **Cloudflare R2** | Nightly dump of Postgres + tar of `data/*_models` |

---

### D. ML training / prototypes (burst, not always-on)

| Workload | Host | Why |
|----------|------|-----|
| Reliable GPU train | **RunPod** (Secure Cloud pods) | Predictable, templates, per-second billing |
| Cheapest experiments | **Vast.ai** | Marketplace pricing; more ops friction |
| Python-native burst jobs | **Modal** | Good for “spin up train → write artifacts → die” |
| Light CPU train | Same production VPS | Docker image is slim (no torch by default) — install torch in a **GPU image** or train offline |

**Pattern:** Train on RunPod/Vast → sync artifacts into production `backend/data` volume (SCP/R2) → Activate version via Lab API. Do **not** pay for 24/7 GPU unless you serve live GPU inference (runtime mostly uses ONNX/CPU).

---

### E. Staging / preview / prototypes

| Purpose | Host |
|---------|------|
| Full-stack preview from Git | **Railway** or **Render Preview** |
| Cheap disposable lab | Second small Hetzner CX22 + compose |
| UI-only design experiments | Cloudflare Pages / Netlify preview |
| Figma/demo (`figma-demo/`) | Netlify / Pages — keep separate from trading stack |

Never point staging at **live** broker keys. Use paper Alpaca / sim profile.

---

### F. CI, testing, monitoring

| Concern | Service |
|---------|---------|
| CI (`.github/workflows/ci.yml`) | **GitHub Actions** |
| Uptime 24/7 | **Better Stack**, **UptimeRobot**, or self-host **Uptime Kuma** hitting `/health/live` |
| Logs | Compose json logs + **Better Stack** / **Axiom** / DO monitoring |
| Errors (optional) | Sentry (frontend + backend) |
| Secrets | GitHub Environments + host secret store (Coolify/Render/Railway) — **never** commit `.env` |

---

### G. What not to use as primary production host

| Platform | Why it fails for this app |
|----------|---------------------------|
| Vercel / Netlify (as backend) | No long-running Python bots / native WS server |
| AWS Lambda / Cloud Run scale-to-zero | Feed + bots must stay awake |
| Shared cheap shared-hosting | No Docker, no WS, no process control |
| Multi-region active-active | Breaks in-memory WS/feed state |

---

## 3. Recommended topologies

### Topology 1 — Personal 24/7 (recommended start)

```text
Hetzner CX31/CX41 (or DO 8–16GB)
  └── Coolify or Caddy + docker compose
        ├── postgres (volume)
        ├── backend (TERMINAL_ROLE=all, LIVE_ALPACA paper)
        └── frontend nginx :443
Nightly: pg_dump + tar data/ → B2/R2
Alerts: Uptime Kuma → Telegram/Discord
```

**Cost ballpark:** ~€10–40/mo compute + storage; broker APIs separate.

### Topology 2 — Managed PaaS

```text
Render/Railway:
  ├── Web service: backend (single instance, no scale-out)
  ├── Disk: /app/data
  ├── Postgres managed
  ├── Static site OR second service: frontend
  └── Optional worker if COMPOSE_PROFILES=distributed
```

Wire custom domain; long WS timeouts; disable idle spin-down.

### Topology 3 — Serious ops (bots + heavy lab)

```text
Always-on VPS: feed + WS + HTTP + light bots + Postgres
Separate GPU (RunPod): ML train jobs only
Redis + worker: only if bot load needs split
Staging VPS: sim/paper only
```

### Compose quick start (local / VPS)

```bash
cp .env.example .env
docker compose up --build
# UI: http://localhost:8080  (or HTTPS via Caddy/Coolify in production)
```

Distributed server/worker split:

```bash
# In .env: COMPOSE_PROFILES=distributed  TERMINAL_ROLE=server  REDIS_URL=redis://redis:6379/0
docker compose up --build
```

---

## 4. Day-2 operations: updates, bugs, testing, prototypes

### Updating (safe deploy loop)

1. **Branch** → PR → GitHub Actions (backend tests, frontend build, Playwright).
2. Deploy to **staging** (paper/sim) first.
3. Production: `git pull` / Coolify deploy / `docker compose up --build -d`.
4. Run migrations (`ALEMBIC_AUTO_UPGRADE=1` already in compose — still verify).
5. Health: `/health/live`, WS reconnect from UI, one bot tick, model-status.
6. Keep previous image tag for **rollback** (`docker compose` previous image or Coolify rollback).
7. After deploy, expect in-memory state reset — bots should reload from DB; confirm positions reconcile.

**Rule:** Never deploy mid-session without knowing OMS mode (paper vs live). Prefer paper window for first cloud deploys.

### Resolving bugs / incidents

| Layer | How |
|-------|-----|
| App crash | `restart: unless-stopped` + alert on failed health |
| Bad release | Rollback image; keep `data/` + DB volumes untouched |
| Data bug | Restore from nightly backup to staging; fix; then careful prod patch |
| Broker oddity | Profile-specific health (`/health/alpaca` etc.); don’t recycle blindly during open SL/TP |
| ML champion wrong | Use version Activate; validation sidecar fingerprint; don’t delete volumes |

### Testing before production

| Gate | Where |
|------|--------|
| Unit / API | GitHub Actions + local `pytest` |
| UI build / E2E | Existing CI Playwright |
| Paper trading soak | Staging VPS **48–72h** before live keys |
| ML | Train on GPU host → copy artifacts → Validate/WF on staging |
| Load | One profile only; don’t run Sim+IB+Alpaca+Massive on one small box |

### Prototypes

| Type | Where |
|------|--------|
| UI experiments | Feature branch + Pages preview or local Vite |
| Strategy / ML | Staging + paper; or RunPod train → import models |
| New broker mode | Isolated profile/env; never share SQLite with live |
| Infra experiments | Throwaway CX22; destroy when done |

---

## 5. Security checklist (cloud)

- `HTTP_API_KEY` + HTTPS only; restrict CORS to your domain
- Firewall: 443 (and SSH) only; DB/Redis not public
- Broker keys: trade-only, IP allowlist to VPS, no withdrawal
- Separate **paper** vs **live** env files / projects
- Encrypted backups; rotate `NOTIFICATION_ENCRYPTION_KEY` / VAPID carefully
- This app is **not** multi-user — don’t share the URL without the API key and network limits

### Key secrets (from `.env` / profiles)

| Integration | Env vars |
|-------------|----------|
| **Alpaca** | `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL` |
| **Binance** | `BINANCE_API_KEY`, `BINANCE_SECRET_KEY` |
| **eToro** | Bearer **or** `ETORO_API_KEY`+`ETORO_USER_KEY` (never both) |
| **IB** | `IB_HOST`, `IB_PORT` (local Gateway — no cloud API key) |
| **Massive** | `MASSIVE_API_KEY` |
| **Finnhub** | `FINNHUB_API_KEY` |
| **LLM** | `OLLAMA_BASE_URL` and/or `OPENROUTER_API_KEY` |
| **Gate** | `HTTP_API_KEY` |
| **Push / notify** | `VAPID_*`, `NOTIFICATION_ENCRYPTION_KEY` |

---

## 6. 7-day go-live plan

| Day | Action |
|-----|--------|
| 1 | Provision Hetzner/DO 8–16 GB; Docker + compose; TLS domain |
| 2 | Secrets (paper Alpaca/Finnhub); Postgres volume; `ALLOW_LIVE_BOTS` false until ready |
| 3 | Deploy; confirm WS ticks + health; import/migrate DB if needed |
| 4 | Enable 1–2 paper bots; notifications (Telegram/Discord) |
| 5 | Backup job + restore drill |
| 6 | Staging ML train path (optional RunPod); activate on staging |
| 7 | 24h soak review → only then consider live keys / `ALLOW_LIVE_BOTS` |

---

## 7. Quick decision matrix

| Goal | Pick |
|------|------|
| Cheapest reliable 24/7 | **Hetzner + Docker Compose** (+ Coolify optional) |
| Least SSH / managed feel | **Render** (or Railway for speed) |
| Best WebSocket fit on PaaS | **Fly.io** (single region + volume) |
| Static UI CDN | **Cloudflare Pages** (still point WS to backend host) |
| GPU train / prototypes | **RunPod** (or Vast.ai if optimizing $) |
| CI | **GitHub Actions** (already in repo) |
| Backups | Volume snapshots + **B2/R2** |
| Don’t | Serverless backend, multi-replica WS without redesign |

---

## 8. Resource guidance (approx.)

| Workload | Guidance |
|----------|----------|
| Baseline (sim or one live profile, no heavy ML) | ~4–8 GB RAM; prefer one profile on 16 GB |
| Multiple profiles on one machine | Can exceed 16 GB — not recommended |
| ML train (LSTM/TCN/Transformer/PPO) | Optional CUDA GPU; soft RSS ~4–6 GB/worker |
| CPU backtests / Optuna | Keep `BACKTEST_PARALLEL_WORKERS` conservative on small VPS |
| GPU | Not required for TA bots / paper; recommended for serious ML Lab |

---

## Bottom line

Treat this as a **single always-on Docker host** (Hetzner/DO or Render/Fly), with **Postgres + `backend/data` volumes**, optional **GPU offload** for ML, and a **staging twin** for updates/bugs. The Compose + nginx `/ws` path is already the right production shape; Electron stays local.
