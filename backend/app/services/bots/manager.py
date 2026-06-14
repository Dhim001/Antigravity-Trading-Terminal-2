import logging
import json
import uuid
from datetime import datetime, timezone

from app.config import TERMINAL_MODE, ALLOW_LIVE_BOTS, BOT_LOG_RETENTION
from app.database import get_connection
from app.api.outbound import publish_bot_detail, publish_bot_log, publish_bots_update, publish_post_trade_bundle
from app.services.bots.indicators import prepare_strategy_df
from app.services.bots.strategies import get_strategy, normalize_strategy_name
from app.services.bots.bar_events import BarCloseTracker
from app.services.bots.risk_gate import RiskGate
from app.services.bots import analytics as bot_analytics
from app.services.bots import positions as bot_positions

ACTIVE_STATUSES = ("RUNNING", "PAUSED", "ERROR")


def _coerce_bar_time(bar_time) -> int | None:
    if bar_time is None:
        return None
    try:
        return int(bar_time)
    except (TypeError, ValueError):
        return None


class BotManagerService:
    def __init__(self, oms_service, screener_service, broadcast_cb):
        self.logger = logging.getLogger(__name__)
        self.oms = oms_service
        self.screener = screener_service
        self.broadcast_cb = broadcast_cb
        self.active_bots = {}
        self._bar_tracker = BarCloseTracker()
        self._risk_gate = RiskGate()
        self._executed_signals: set[str] = set()
        self._log_writes = 0

    def _get_daily_pnl(self, bot_id: str) -> float:
        return bot_analytics.get_daily_pnl(bot_id)

    def load_bots_from_db(self):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bots WHERE status IN ('RUNNING', 'PAUSED', 'ERROR')")
        rows = cursor.fetchall()
        for row in rows:
            bot_id = row["id"]
            self.active_bots[bot_id] = dict(row)
            self.active_bots[bot_id]["config"] = json.loads(row["config"])
            self.active_bots[bot_id]["last_signal_bar_time"] = None
            self.active_bots[bot_id]["last_signal_at"] = None
            self.active_bots[bot_id]["strategy_instance"] = get_strategy(
                row["strategy"], self.active_bots[bot_id]["config"]
            )
        conn.close()
        self.logger.info(f"Loaded {len(self.active_bots)} bots from DB.")

    async def log_bot_event(self, bot_id: str, level: str, message: str):
        self.logger.info(f"[BOT {bot_id}] {level} - {message}")
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bot_logs (bot_id, level, message) VALUES (?, ?, ?)",
            (bot_id, level, message),
        )
        conn.commit()
        conn.close()

        self._log_writes += 1
        if self._log_writes % 25 == 0:
            bot_analytics.prune_bot_logs(BOT_LOG_RETENTION)

        await publish_bot_log(self.broadcast_cb, bot_id, level, message)

    def get_account_balance(self):
        balances = self.oms.get_account_data().get("balances", {})
        usd = balances.get("USD", {}).get("balance")
        if usd is not None:
            return usd
        return balances.get("USDT", {}).get("balance", 0)

    def _get_bot_position(self, bot_id: str, symbol: str) -> dict:
        return bot_positions.get_bot_position(bot_id, symbol)

    def _get_bot_position_size(self, bot_id: str, symbol: str) -> float:
        return bot_positions.get_bot_size(bot_id, symbol)

    def _get_position(self, symbol: str) -> dict:
        positions = self.oms.get_account_data().get("positions", {})
        return positions.get(symbol) or {}

    def _get_position_size(self, symbol: str) -> float:
        pos = self._get_position(symbol)
        return float(pos.get("size", 0) or 0)

    def get_recent_logs(self, limit: int = 100):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT bot_id, level, message, timestamp FROM bot_logs ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def _calc_exit_pnl(self, side: str, qty: float, exit_price: float, entry_price: float) -> float:
        if side == "SELL":
            return (exit_price - entry_price) * qty
        return (entry_price - exit_price) * qty

    def record_snapshot_for_bot(self, bot_id: str):
        if bot_id not in self.active_bots:
            return
        bot = self.active_bots[bot_id]
        symbol = bot["symbol"]
        stats = bot_analytics.get_bot_stats(bot_id)
        realized = stats["total_pnl"]
        bot_pos = self._get_bot_position(bot_id, symbol)
        pos_size = float(bot_pos.get("size") or 0)
        avg = float(bot_pos.get("avg_price") or 0)
        mark = avg
        if hasattr(self.oms, "feed") and hasattr(self.oms.feed, "_symbols"):
            mark = float(self.oms.feed._symbols.get(symbol, {}).get("price", mark))
        unrealized = pos_size * (mark - avg) if pos_size else 0.0
        equity = float(bot["allocation"]) + realized + unrealized
        open_positions = 1 if abs(pos_size) > 0 else 0
        bot_analytics.record_snapshot(
            bot_id, equity, unrealized, realized, open_positions
        )

    async def snapshot_all_bots(self):
        for bot_id in list(self.active_bots.keys()):
            self.record_snapshot_for_bot(bot_id)

    def get_bot_detail(self, bot_id: str) -> dict | None:
        if bot_id not in self.active_bots:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bots WHERE id = ?", (bot_id,))
            row = cursor.fetchone()
            conn.close()
            if not row:
                return None
            bot = dict(row)
            bot["config"] = json.loads(bot["config"])
        else:
            bot = self.active_bots[bot_id]
        stats = bot_analytics.get_bot_stats(bot_id)
        return {
            "bot": {
                "id": bot["id"],
                "strategy": bot["strategy"],
                "symbol": bot["symbol"],
                "timeframe": bot["timeframe"],
                "status": bot["status"],
                "allocation": bot["allocation"],
                "config": bot.get("config", {}),
            },
            "stats": stats,
            "trades": bot_analytics.get_trades(bot_id, 50),
            "snapshots": bot_analytics.get_snapshots(bot_id, 30),
        }

    async def _set_bot_status(self, bot_id: str, status: str):
        if bot_id in self.active_bots:
            self.active_bots[bot_id]["status"] = status
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE bots SET status = ? WHERE id = ?", (status, bot_id))
        conn.commit()
        conn.close()

    async def _halt_bot(self, bot_id: str, reason: str):
        await self._set_bot_status(bot_id, "ERROR")
        await self.log_bot_event(bot_id, "ERROR", reason)

    async def process_market_tick(self, symbol: str, ohlcv_data: list):
        if TERMINAL_MODE != "SIMULATED" and not ALLOW_LIVE_BOTS:
            return
        if not self.active_bots or not any(
            b["symbol"] == symbol and b.get("status") == "RUNNING"
            for b in self.active_bots.values()
        ):
            return
        if not self._bar_tracker.check(symbol, ohlcv_data):
            return

        for bot_id, bot in list(self.active_bots.items()):
            if bot["symbol"] != symbol or bot.get("status") != "RUNNING":
                continue

            strat = bot.get("strategy_instance")
            if not strat:
                continue

            bot_config = bot.get("config", {})
            bot_strategy = bot["strategy"]
            df = self.screener.process_candles(
                symbol, ohlcv_data, bot_config, bot_strategy
            )
            if df.empty or len(df) < 2:
                continue

            df = prepare_strategy_df(df, bot_strategy, bot_config)
            eval_row = df.iloc[-2].to_dict()
            bar_time = eval_row.get("time")
            eval_price = eval_row.get("close")

            signal_data = strat.evaluate(eval_row)
            signal = signal_data.get("signal")
            if signal not in ("BUY", "SELL", "CLOSE"):
                continue

            if bar_time is not None and bot.get("last_signal_bar_time") == bar_time:
                continue

            await self._handle_signal(bot, signal, signal_data, eval_price, bar_time)

    async def _handle_signal(self, bot, signal: str, signal_data: dict, eval_price: float, bar_time):
        bot_id = bot["id"]
        symbol = bot["symbol"]
        bot_pos = self._get_bot_position(bot_id, symbol)
        pos_size = float(bot_pos.get("size") or 0)

        if signal == "CLOSE":
            if pos_size > 0:
                await self._execute_order(
                    bot, "SELL", abs(pos_size), eval_price, signal_data,
                    is_exit=True, bar_time=bar_time,
                    entry_price=float(bot_pos.get("avg_price") or eval_price),
                )
            elif pos_size < 0:
                await self._execute_order(
                    bot, "BUY", abs(pos_size), eval_price, signal_data,
                    is_exit=True, bar_time=bar_time,
                    entry_price=float(bot_pos.get("avg_price") or eval_price),
                )
            return

        if signal == "BUY":
            if pos_size > 0:
                return
            await self._execute_order(
                bot, "BUY", None, eval_price, signal_data,
                is_exit=False, bar_time=bar_time,
            )
            return

        if signal == "SELL":
            if pos_size > 0:
                await self._execute_order(
                    bot, "SELL", abs(pos_size), eval_price, signal_data,
                    is_exit=True, bar_time=bar_time,
                    entry_price=float(bot_pos.get("avg_price") or eval_price),
                )
            return

    async def _execute_order(
        self,
        bot,
        side: str,
        quantity: float | None,
        current_price: float,
        signal_data: dict,
        *,
        is_exit: bool,
        bar_time,
        entry_price: float | None = None,
    ):
        bot_id = bot["id"]
        symbol = bot["symbol"]
        signal_kind = "CLOSE" if is_exit else side
        signal_id = f"{bot_id}:{bar_time}:{signal_kind}"

        if signal_id in self._executed_signals:
            return

        if not is_exit:
            account_balance = self.get_account_balance()
            risk_amount = account_balance * 0.01

            stop_loss_price = signal_data.get("stop_loss_price")
            if not stop_loss_price:
                sl_dist = signal_data.get("stop_loss_distance", current_price * 0.02)
                stop_loss_price = (
                    current_price - sl_dist if side == "BUY" else current_price + sl_dist
                )

            price_diff = abs(current_price - stop_loss_price)
            if price_diff <= 0:
                await self.log_bot_event(bot_id, "ERROR", "Stop loss distance is 0. Aborting trade.")
                return

            quantity = risk_amount / price_diff

        pos_size = self._get_bot_position_size(bot_id, symbol)
        decision = self._risk_gate.validate_trade(
            bot,
            side,
            quantity,
            current_price,
            is_exit=is_exit,
            daily_pnl=self._get_daily_pnl(bot_id),
            position_size=pos_size,
        )

        if not decision.allowed:
            await self.log_bot_event(bot_id, "WARN", f"Risk blocked: {decision.reason}")
            if "Daily loss limit" in decision.reason:
                await self._halt_bot(bot_id, decision.reason)
            return

        quantity = decision.quantity if decision.quantity is not None else quantity
        if decision.reason not in ("OK",):
            await self.log_bot_event(bot_id, "INFO", decision.reason)

        if quantity < 0.001:
            await self.log_bot_event(bot_id, "INFO", "Signal ignored: quantity too small.")
            return

        action = "Exit" if is_exit else "Entry"
        bot["last_signal_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        await self.log_bot_event(
            bot_id,
            "INFO",
            f"{action} {side} signal @ {current_price:.2f}, qty {quantity:.4f}",
        )

        self._executed_signals.add(signal_id)

        try:
            result = await self.oms.place_order({
                "symbol": symbol,
                "type": "MARKET",
                "side": side,
                "quantity": quantity,
                "stop_loss_percent": (
                    None if is_exit
                    else bot.get("config", {}).get("trailing_stop_percent")
                    or bot.get("config", {}).get("stop_loss_percent")
                ),
                "take_profit_percent": (
                    None if is_exit else bot.get("config", {}).get("take_profit_percent")
                ),
                "bot_id": bot_id,
                "signal_id": signal_id,
            })

            if result.get("status") == "success":
                if bar_time is not None:
                    bot["last_signal_bar_time"] = bar_time
                order_id = result.get("order_id")
                fill_price = float(result.get("average_fill_price") or current_price)
                filled_qty = float(result.get("filled_quantity") or quantity or 0)
                live_submitted = TERMINAL_MODE != "SIMULATED" and result.get("average_fill_price") is None

                if live_submitted:
                    bot_analytics.record_pending_fill(
                        bot_id,
                        order_id,
                        symbol,
                        side,
                        filled_qty,
                        current_price,
                        signal_id=signal_id,
                        is_exit=is_exit,
                        entry_price=entry_price,
                    )
                    await self.log_bot_event(
                        bot_id,
                        "SUCCESS",
                        f"Submitted {side} {filled_qty:.4f} @ ~{current_price:.4f} (order {order_id}). Awaiting broker fill.",
                    )
                else:
                    await self.log_bot_event(
                        bot_id,
                        "SUCCESS",
                        f"Filled {side} {filled_qty:.4f} @ {fill_price:.4f} (order {order_id}).",
                    )

                    trade_pnl = None
                    if is_exit and entry_price is not None:
                        trade_pnl = self._calc_exit_pnl(side, filled_qty, fill_price, entry_price)

                    bot_analytics.record_trade(
                        bot_id,
                        order_id,
                        symbol,
                        side,
                        filled_qty,
                        fill_price,
                        pnl=trade_pnl,
                        signal_id=signal_id,
                        signal_bar_time=_coerce_bar_time(bar_time),
                        is_exit=is_exit,
                    )
                    self.record_snapshot_for_bot(bot_id)

                await publish_post_trade_bundle(
                    self.broadcast_cb,
                    self.oms.get_account_data(),
                    self.oms.get_trade_history(),
                )
                await publish_bots_update(self.broadcast_cb, self.list_bots_public())
                detail = self.get_bot_detail(bot_id)
                if detail:
                    await publish_bot_detail(self.broadcast_cb, detail)
            else:
                self._executed_signals.discard(signal_id)
                msg = result.get("message", "Unknown error")
                await self.log_bot_event(bot_id, "ERROR", f"Order failed: {msg}")
                if TERMINAL_MODE != "SIMULATED":
                    await self.log_bot_event(
                        bot_id,
                        "WARN",
                        "Live order not retried (at-most-once). Reconcile manually if needed.",
                    )
        except Exception as e:
            self._executed_signals.discard(signal_id)
            await self.log_bot_event(bot_id, "ERROR", f"Order exception: {str(e)}")
            if TERMINAL_MODE != "SIMULATED":
                await self.log_bot_event(
                    bot_id,
                    "WARN",
                    "Ambiguous live outcome — do not resend; reconcile via broker.",
                )

    async def create_bot(self, strategy: str, symbol: str, timeframe: str, allocation: float, config: dict):
        if TERMINAL_MODE != "SIMULATED" and not ALLOW_LIVE_BOTS:
            raise ValueError(
                "Live bot trading is disabled. Set ALLOW_LIVE_BOTS=true in .env to enable."
            )

        decision = self._risk_gate.validate_create(len(self.active_bots))
        if not decision.allowed:
            raise ValueError(decision.reason)

        strategy = normalize_strategy_name(strategy)
        bot_id = str(uuid.uuid4())
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (bot_id, strategy, symbol, timeframe, "RUNNING", allocation, json.dumps(config)),
        )
        conn.commit()
        conn.close()

        self.active_bots[bot_id] = {
            "id": bot_id,
            "strategy": strategy,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "RUNNING",
            "allocation": allocation,
            "config": config,
            "last_signal_bar_time": None,
            "last_signal_at": None,
            "strategy_instance": get_strategy(strategy, config),
        }

        await self.log_bot_event(bot_id, "INFO", f"Bot created and started for {symbol} using {strategy}.")
        self.record_snapshot_for_bot(bot_id)
        return bot_id

    async def pause_bot(self, bot_id: str):
        if bot_id not in self.active_bots:
            raise ValueError("Bot not found")
        await self._set_bot_status(bot_id, "PAUSED")
        await self.log_bot_event(bot_id, "INFO", "Bot paused.")

    async def resume_bot(self, bot_id: str):
        if bot_id not in self.active_bots:
            raise ValueError("Bot not found")
        bot = self.active_bots[bot_id]
        if bot.get("status") == "ERROR":
            raise ValueError("Bot is in ERROR state — stop and redeploy.")
        await self._set_bot_status(bot_id, "RUNNING")
        await self.log_bot_event(bot_id, "INFO", "Bot resumed.")

    async def stop_bot(self, bot_id: str):
        if bot_id in self.active_bots:
            del self.active_bots[bot_id]

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE bots SET status = 'STOPPED' WHERE id = ?", (bot_id,))
        conn.commit()
        conn.close()

        await self.log_bot_event(bot_id, "INFO", "Bot stopped.")

    async def stop_all_bots(self):
        bot_ids = list(self.active_bots.keys())
        for bot_id in bot_ids:
            await self.stop_bot(bot_id)
        return len(bot_ids)

    def list_bots_public(self) -> list:
        out = []
        for bot in self.active_bots.values():
            bot_id = bot["id"]
            stats = bot_analytics.get_bot_stats(bot_id)
            out.append({
                "id": bot_id,
                "strategy": bot["strategy"],
                "symbol": bot["symbol"],
                "timeframe": bot["timeframe"],
                "status": bot["status"],
                "allocation": bot["allocation"],
                "config": bot.get("config", {}),
                "daily_pnl": stats["daily_pnl"],
                "total_pnl": stats["total_pnl"],
                "trade_count": stats["trade_count"],
                "win_rate": stats["win_rate"],
                "last_signal_at": bot.get("last_signal_at"),
            })
        return out

    def list_all_bots_public(self, limit: int = 100) -> list:
        """All bots from DB (active + stopped) with aggregated stats."""
        limit = max(1, min(limit, 500))
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bots ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        out = []
        for row in rows:
            bot_id = row["id"]
            if bot_id in self.active_bots:
                bot = self.active_bots[bot_id]
            else:
                bot = {
                    **row,
                    "config": json.loads(row.get("config") or "{}"),
                }
            stats = bot_analytics.get_bot_stats(bot_id)
            out.append({
                "id": bot_id,
                "strategy": bot["strategy"],
                "symbol": bot["symbol"],
                "timeframe": bot["timeframe"],
                "status": bot["status"],
                "allocation": bot["allocation"],
                "config": bot.get("config", {}),
                "daily_pnl": stats["daily_pnl"],
                "total_pnl": stats["total_pnl"],
                "trade_count": stats["trade_count"],
                "win_rate": stats["win_rate"],
                "exit_count": stats["exit_count"],
                "created_at": row.get("created_at"),
                "last_signal_at": bot.get("last_signal_at"),
            })
        return out

    async def handle_sl_tp_exits(self, bot_exits: list[dict]):
        """Record SIM stop-loss / take-profit exits in bot analytics."""
        if not bot_exits:
            return

        touched: set[str] = set()
        for exit_info in bot_exits:
            bot_id = exit_info["bot_id"]
            side = exit_info["side"]
            qty = float(exit_info["quantity"])
            price = float(exit_info["price"])
            entry_price = float(exit_info.get("entry_price") or price)
            trigger = exit_info.get("trigger_type", "SL/TP")
            trade_pnl = self._calc_exit_pnl(side, qty, price, entry_price)

            bot_analytics.record_trade(
                bot_id,
                exit_info.get("order_id"),
                exit_info["symbol"],
                side,
                qty,
                price,
                pnl=trade_pnl,
                signal_id=exit_info.get("signal_id"),
                is_exit=True,
            )
            self.record_snapshot_for_bot(bot_id)
            touched.add(bot_id)

            await self.log_bot_event(
                bot_id,
                "INFO",
                f"{trigger} exit {side} {qty:.4f} @ {price:.4f} (PnL {trade_pnl:+.2f}).",
            )

        await publish_post_trade_bundle(
            self.broadcast_cb,
            self.oms.get_account_data(),
            self.oms.get_trade_history(),
        )
        await publish_bots_update(self.broadcast_cb, self.list_bots_public())
        for bot_id in touched:
            detail = self.get_bot_detail(bot_id)
            if detail:
                await publish_bot_detail(self.broadcast_cb, detail)

    async def reconcile_pending_fills(self) -> int:
        """Confirm live bot orders against broker trade history before recording analytics."""
        pending = bot_analytics.list_pending_fills()
        if not pending:
            return 0

        history = self.oms.get_trade_history()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT order_id FROM bot_trades WHERE order_id IS NOT NULL")
        recorded_order_ids = {str(r[0]) for r in cursor.fetchall()}
        conn.close()

        confirmed = 0
        touched: set[str] = set()

        for p in pending:
            order_id = p.get("order_id")
            match = None

            if order_id:
                for trade in history:
                    if str(trade.get("id")) == str(order_id):
                        match = trade
                        break

            if not match:
                for trade in history:
                    tid = str(trade.get("id", ""))
                    if tid and tid in recorded_order_ids:
                        continue
                    if trade.get("symbol") != p["symbol"]:
                        continue
                    if trade.get("side") != p["side"]:
                        continue
                    tqty = float(trade.get("filled_quantity") or trade.get("quantity") or 0)
                    pqty = float(p["quantity"])
                    if pqty <= 0:
                        continue
                    if abs(tqty - pqty) / pqty > 0.08:
                        continue
                    match = trade
                    break

            if not match:
                continue

            fill_price = float(match.get("average_fill_price") or p["signal_price"])
            filled_qty = float(match.get("filled_quantity") or match.get("quantity") or p["quantity"])
            resolved_order_id = order_id or str(match.get("id"))
            is_exit = bool(p.get("is_exit"))
            entry_price = p.get("entry_price")
            trade_pnl = None
            if is_exit and entry_price is not None:
                trade_pnl = self._calc_exit_pnl(
                    p["side"], filled_qty, fill_price, float(entry_price)
                )

            bot_analytics.record_trade(
                p["bot_id"],
                resolved_order_id,
                p["symbol"],
                p["side"],
                filled_qty,
                fill_price,
                pnl=trade_pnl,
                signal_id=p.get("signal_id"),
                signal_bar_time=bot_analytics.signal_bar_time_from_id(p.get("signal_id")),
                is_exit=is_exit,
            )
            bot_positions.apply_fill(p["bot_id"], p["symbol"], p["side"], filled_qty, fill_price)
            bot_analytics.delete_pending_fill(p["id"])
            if resolved_order_id:
                recorded_order_ids.add(str(resolved_order_id))
            confirmed += 1
            touched.add(p["bot_id"])

            await self.log_bot_event(
                p["bot_id"],
                "SUCCESS",
                f"Broker confirmed {p['side']} {filled_qty:.4f} @ {fill_price:.4f} (order {resolved_order_id}).",
            )

        if confirmed:
            for bot_id in touched:
                self.record_snapshot_for_bot(bot_id)
            await publish_post_trade_bundle(
                self.broadcast_cb,
                self.oms.get_account_data(),
                self.oms.get_trade_history(),
            )
            await publish_bots_update(self.broadcast_cb, self.list_bots_public())

        return confirmed
