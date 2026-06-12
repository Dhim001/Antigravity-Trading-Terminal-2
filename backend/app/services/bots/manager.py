import logging
import json
import uuid
from datetime import datetime, timezone

from app.config import TERMINAL_MODE, ALLOW_LIVE_BOTS, BOT_LOG_RETENTION
from app.database import get_connection
from app.api.outbound import publish_bot_detail, publish_bot_log, publish_bots_update
from app.services.bots.indicators import prepare_strategy_df
from app.services.bots.strategies import get_strategy
from app.services.bots.bar_events import BarCloseTracker
from app.services.bots.risk_gate import RiskGate
from app.services.bots import analytics as bot_analytics

ACTIVE_STATUSES = ("RUNNING", "PAUSED", "ERROR")


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
        pos = self._get_position(symbol)
        pos_size = float(pos.get("size", 0) or 0)
        avg = float(pos.get("avg_price") or 0)
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
        pos_size = self._get_position_size(symbol)
        pos = self._get_position(symbol)

        if signal == "CLOSE":
            if pos_size > 0:
                await self._execute_order(
                    bot, "SELL", abs(pos_size), eval_price, signal_data,
                    is_exit=True, bar_time=bar_time,
                    entry_price=float(pos.get("avg_price") or eval_price),
                )
            elif pos_size < 0:
                await self._execute_order(
                    bot, "BUY", abs(pos_size), eval_price, signal_data,
                    is_exit=True, bar_time=bar_time,
                    entry_price=float(pos.get("avg_price") or eval_price),
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
                    entry_price=float(pos.get("avg_price") or eval_price),
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

        pos_size = self._get_position_size(symbol)
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
                await self.log_bot_event(bot_id, "SUCCESS", f"Placed {side} order {order_id}.")

                trade_pnl = None
                if is_exit and entry_price is not None:
                    trade_pnl = self._calc_exit_pnl(side, quantity, current_price, entry_price)

                bot_analytics.record_trade(
                    bot_id,
                    order_id,
                    symbol,
                    side,
                    quantity,
                    current_price,
                    pnl=trade_pnl,
                    signal_id=signal_id,
                    is_exit=is_exit,
                )
                self.record_snapshot_for_bot(bot_id)

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
