# Grafana dashboard import checklist

Use this checklist when wiring Prometheus + Grafana for the trading terminal.

## Prerequisites

1. Backend HTTP API reachable (default `http://127.0.0.1:8766`).
2. `GET /metrics` returns Prometheus text (proxied via Vite in dev, nginx in Docker).
3. Prometheus scrape job targets the backend `/metrics` endpoint every 15–30s.

## Import dashboard

1. Open Grafana → **Dashboards** → **New** → **Import**.
2. Upload [`trading-terminal.json`](./trading-terminal.json) or paste its JSON.
3. Select your Prometheus data source when prompted.
4. Save the dashboard.

## Panels to verify

| Panel | Expected metric |
|-------|-----------------|
| Orders placed | `orders_place_total` |
| Preview allowed / blocked | `orders_preview_allowed_total`, `orders_preview_blocked_total` |
| Agent analyze p99 | `agent_analyze_duration_seconds{quantile="0.99"}` |
| Bot signals | `bot_signals_total` |
| Bot orders blocked | `bot_orders_blocked_total` |

## Docker / remote WebSocket validation

1. Start stack: `docker compose up --build`.
2. Confirm browser connects to same-origin WebSocket (`/ws` through nginx).
3. Place a sim order and deploy a bot — counters on `/metrics` should increment.
4. Settings → System → **Metrics snapshot** should show analyze p99 and bot counters.
5. With `REDIS_URL` and a worker container, worker heartbeat appears under `/health` and Settings.

## Optional alerts

- `agent_analyze_duration_seconds{quantile="0.99"} > 2` for 5m — analyst latency regression.
- `bot_orders_blocked_total` rate spike — risk gate or CHART_AGENT filter pressure.
- Worker heartbeat age > 35s when distributed mode is enabled.

## Structured logs

Set `LOG_JSON=true` on the backend for JSON logs grepable by `bot_id`, `symbol`, and `insight_id` on trade and agent paths.
