# Order execution UX

Capability flags (`orderCapabilities` / `order_capabilities`) are sent on WebSocket
`terminal_config` and `GET /api/v1/session`. The frontend uses them to show or hide
quick-trade actions per broker.

| Flag | Meaning |
|------|---------|
| `partial_close` | Close 50% / partial market exit |
| `reverse_position` | Flip long ↔ short (SIM / short-capable brokers) |
| `bracket_orders` | Entry attaches SL/TP at broker (eToro, Alpaca, SIM) |
| `oco` | Linked SL/TP exit legs — SIM creates `OCO_ACTIVE` rows; sibling cancels on fill |
| `trailing_stop_manual` | Trailing stop % in order ticket (SIM) |
| `order_preview_costs` | Fee / slippage / margin in `/orders/preview` |

## Preview costs

Configure defaults via environment:

- `ORDER_PREVIEW_FEE_BPS` (default `10`)
- `ORDER_PREVIEW_SLIPPAGE_BPS` (default `5`)

Margin impact uses the same `RISK_MARGIN_*` settings as bot risk gates.

## Quick trade

- **50%** — market order for half the position (rounded down).
- **Close** — full market close.
- **Rev** — single order at 2× size to flip (SIM only when `reverse_position` is true).

eToro: partial close uses `UnitsToDeduct`; reverse is disabled (long-only).

## Chart SL/TP overlay (Phase 6)

Stop-loss and take-profit levels render as draggable lines on the main chart:

- **Live position** — solid red (SL) / green (TP) lines with handles on the right plot edge. Dragging commits via `UPDATE_POSITION_SL_TP`.
- **Order ticket draft** — dashed overlay when bracket SL/TP is set in Order Entry (fixed prices only; trailing stop is ticket-only). Dragging syncs back to the ticket fields.
- **Click-to-set** — Positions panel “Edit on chart” still uses click mode (`edit_sl` / `edit_tp`); drag and click can coexist.

Draft state lives in `chartSlTpDraft` (Zustand) with `source: 'ticket' | 'chart'` to avoid sync loops. Draft clears on symbol change.

## E2E tests (Phase 7)

Playwright specs in `frontend/e2e/trading-flows.spec.js`:

- **UI:** market order via Order Entry → toast + Positions row
- **UI:** bracket SL/TP badge + pre-trade preview (fee, R:R)
- **UI:** quick-trade buttons (50% / Close / Rev) on open position
- **REST:** `GET /api/v1/session` → `order_capabilities`
- **REST:** `POST /api/v1/orders/preview` with bracket percents

Run against a live SIM backend:

```bash
# terminal 1 — backend on :8766
# terminal 2
cd frontend && npm run test:e2e -- e2e/trading-flows.spec.js
```
