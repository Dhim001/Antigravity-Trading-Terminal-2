import asyncio
import logging
import json
import uuid
import time
from datetime import datetime, timezone

from app.db.async_bridge import run_db

from app.config import TERMINAL_MODE, ALLOW_LIVE_BOTS, BOT_LOG_RETENTION
from app.services.bots.execution_mode import is_live_massive, uses_paper_oms
from app.database import get_connection
from app.api.outbound import publish_bot_detail, publish_bot_log, publish_bots_update, publish_post_trade_bundle
from app.observability.metrics import inc
from app.observability.json_log import log_event
from app.services.bots.indicators import prepare_strategy_df, config_cache_key, merge_strategy_config
from app.services.bots.strategies import normalize_strategy_name
from app.services.bots.strategies import get_strategy, normalize_strategy_name
from app.services.bots.take_profit import format_tp_summary, merge_tp_config, resolve_take_profit
from app.services.bots.tick_strategies import get_tick_strategy, is_tick_strategy, merge_tick_config
from app.services.bots.tick_screener import TickScreener
from app.services.bots.bar_events import BarCloseTracker
from app.services.agent.bar_time import coerce_bar_time
from app.services.bots.candle_source import candles_for_timeframe, get_bot_candles
from app.services.market.timeframes import is_valid_timeframe, normalize_timeframe
from app.services.bots.risk_gate import RiskGate, get_bot_entry_hold
from app.services.bots.risk_sizing import RISK_PCT
from app.services.bots import analytics as bot_analytics
from app.services.bots import positions as bot_positions
from app.services.bots import signal_ledger
from app.services.bots.config_validation import normalize_bot_config, sanitize_bot_config
from app.services.runtime import system_state

ACTIVE_STATUSES = ("RUNNING", "PAUSED", "ERROR")

logger = logging.getLogger(__name__)


def _block_reason_bucket(reason: str) -> str:
    text = (reason or "").lower()
    if "daily loss" in text:
        return "daily_loss"
    if "portfolio" in text:
        return "portfolio"
    if "quantity" in text or "too small" in text:
        return "quantity"
    if "stop loss distance" in text:
        return "sizing"
    return "risk"


def _build_filter(bot_config: dict):
    """Lazily import and construct a strategy filter from bot config."""
    from app.services.bots.strategy_filter import build_filter_from_config

    return build_filter_from_config(bot_config)


def _record_order_blocked(bot: dict, reason: str) -> None:
    bucket = _block_reason_bucket(reason)
    strat = normalize_strategy_name(bot.get("strategy", ""))
    inc("bot_orders_blocked_total", labels={"strategy": strat, "reason": bucket})
    log_event(
        logger,
        "bot_order_blocked",
        bot_id=bot.get("id"),
        symbol=bot.get("symbol"),
        action="bot_order",
    )


def _record_ambiguous_outcome(
    order_req: dict,
    signal_id: str,
    message: str,
    *,
    bot_id: str,
    order_id: str | None = None,
) -> None:
    signal_ledger.mark_signal_ambiguous(signal_id, message, order_id=order_id)
    if uses_paper_oms():
        return
    try:
        from app.services.reconciliation import record_ambiguous_order

        record_ambiguous_order(
            order_req,
            message,
            bot_id=bot_id,
            broker=TERMINAL_MODE,
        )
    except Exception as exc:
        logger.warning("Failed to record ambiguous order for reconciliation UI: %s", exc)


def _coerce_bar_time(bar_time) -> int | None:
    if bar_time is None:
        return None
    try:
        return int(bar_time)
    except (TypeError, ValueError):
        return None


def _bot_bar_timeframe(bot: dict) -> str:
    raw = (bot.get("timeframe") or "1m").strip()
    if raw.lower() == "tick":
        return "1m"
    try:
        return normalize_timeframe(raw)
    except ValueError:
        return "1m"


def _strategy_runtime_config(bot_id: str, bot: dict) -> dict:
    """Runtime-only keys (_bot_id, symbol/timeframe) for strategy evaluation."""
    config = dict(bot.get("config") or {})
    config["_bot_id"] = bot_id
    if normalize_strategy_name(bot.get("strategy", "")) == "CHART_AGENT":
        config["symbol"] = bot.get("symbol")
        config["timeframe"] = _bot_bar_timeframe(bot)
    return config


class BotManagerService:
    def __init__(self, oms_service, screener_service, broadcast_cb):
        self.logger = logging.getLogger(__name__)
        self.oms = oms_service
        self.screener = screener_service
        self.broadcast_cb = broadcast_cb
        self.active_bots = {}
        self._bar_tracker = BarCloseTracker()
        self._risk_gate = RiskGate()
        self._log_writes = 0
        self._log_buffer: list[tuple[str, str, str]] = []
        self._log_flush_task = None
        self._tick_screener = TickScreener()

    def _get_daily_pnl(self, bot_id: str) -> float:
        return bot_analytics.get_daily_pnl(bot_id)

    def load_bots_from_db(self):
        runtime_state = {
            bot_id: {
                "last_signal_bar_time": bot.get("last_signal_bar_time"),
                "last_signal_at": bot.get("last_signal_at"),
                "last_tick_signal_at": bot.get("last_tick_signal_at"),
            }
            for bot_id, bot in self.active_bots.items()
        }
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bots WHERE status IN ('RUNNING', 'PAUSED', 'ERROR')")
        rows = cursor.fetchall()
        loaded_ids: set[str] = set()
        for row in rows:
            bot_id = row["id"]
            loaded_ids.add(bot_id)
            self.active_bots[bot_id] = dict(row)
            self.active_bots[bot_id]["config"] = json.loads(row["config"])
            cfg, cfg_warnings = sanitize_bot_config(self.active_bots[bot_id]["config"])
            if cfg != self.active_bots[bot_id]["config"]:
                self.active_bots[bot_id]["config"] = cfg
                cursor.execute(
                    "UPDATE bots SET config = ? WHERE id = ?",
                    (json.dumps(cfg), bot_id),
                )
                conn.commit()
            for msg in cfg_warnings:
                self.logger.warning("Bot %s config: %s", bot_id[:8], msg)
            prev = runtime_state.get(bot_id)
            self.active_bots[bot_id]["last_signal_bar_time"] = (
                prev["last_signal_bar_time"] if prev else None
            )
            self.active_bots[bot_id]["last_signal_at"] = (
                prev["last_signal_at"] if prev else None
            )
            self.active_bots[bot_id]["last_tick_signal_at"] = (
                prev["last_tick_signal_at"] if prev else 0
            )
            mode = (row["execution_mode"] or "BAR_CLOSE").upper()
            self.active_bots[bot_id]["execution_mode"] = mode
            self.active_bots[bot_id]["timeframe"] = _bot_bar_timeframe(self.active_bots[bot_id])
            config = self.active_bots[bot_id]["config"]
            strategy = row["strategy"]
            runtime_config = _strategy_runtime_config(bot_id, self.active_bots[bot_id])
            if mode == "TICK" or is_tick_strategy(strategy):
                self.active_bots[bot_id]["execution_mode"] = "TICK"
                self.active_bots[bot_id]["tick_strategy_instance"] = get_tick_strategy(strategy, runtime_config)
                self.active_bots[bot_id]["strategy_instance"] = None
            else:
                self.active_bots[bot_id]["strategy_instance"] = get_strategy(strategy, runtime_config)
                self.active_bots[bot_id]["tick_strategy_instance"] = None
        stale_ids = [bot_id for bot_id in self.active_bots if bot_id not in loaded_ids]
        for bot_id in stale_ids:
            del self.active_bots[bot_id]
        conn.close()
        self.logger.info(f"Loaded {len(self.active_bots)} bots from DB.")

    def restore_runtime_checkpoint(self, checkpoint: dict) -> int:
        """Restore in-memory signal timing and resume bots that were RUNNING before shutdown."""
        resumed = 0
        conn = get_connection()
        cursor = conn.cursor()
        try:
            for bot_id, fields in checkpoint.items():
                if not isinstance(fields, dict):
                    continue
                bot = self.active_bots.get(bot_id)
                if bot:
                    for key in ("last_signal_bar_time", "last_signal_at", "last_tick_signal_at"):
                        if key in fields and fields[key] is not None:
                            bot[key] = fields[key]
                prior_status = (fields.get("status") or "").upper()
                if prior_status == "RUNNING":
                    cursor.execute("UPDATE bots SET status = 'RUNNING' WHERE id = ?", (bot_id,))
                    if bot:
                        bot["status"] = "RUNNING"
                    resumed += 1
            conn.commit()
        finally:
            conn.close()
        return resumed

    def apply_safe_mode_pause(self) -> int:
        """Pause all RUNNING bots in DB and memory. Returns count paused."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM bots WHERE status = 'RUNNING'")
        rows = cursor.fetchall()
        running_ids = [row["id"] if isinstance(row, dict) else row[0] for row in rows]
        if running_ids:
            cursor.executemany(
                "UPDATE bots SET status = 'PAUSED' WHERE id = ?",
                [(bid,) for bid in running_ids],
            )
            conn.commit()
        conn.close()
        for bot_id in running_ids:
            if bot_id in self.active_bots:
                self.active_bots[bot_id]["status"] = "PAUSED"
        return len(running_ids)

    async def pause_all_running_bots(self) -> int:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM bots WHERE status = 'RUNNING'")
        rows = cursor.fetchall()
        conn.close()
        paused = 0
        for row in rows:
            bot_id = row["id"] if isinstance(row, dict) else row[0]
            await self.pause_bot(bot_id)
            paused += 1
        return paused

    async def _flush_log_buffer(self):
        if not self._log_buffer:
            return
        batch = self._log_buffer[:]
        self._log_buffer.clear()
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.executemany(
                "INSERT INTO bot_logs (bot_id, level, message, meta) VALUES (?, ?, ?, ?)",
                batch,
            )
            conn.commit()
            self._log_writes += len(batch)
            if self._log_writes % 25 == 0:
                bot_analytics.prune_bot_logs(BOT_LOG_RETENTION)
        finally:
            conn.close()

        for entry in batch:
            bot_id, level, message, meta_json = entry
            meta = json.loads(meta_json) if meta_json else None
            await publish_bot_log(self.broadcast_cb, bot_id, level, message, meta=meta)

    async def _schedule_log_flush(self):
        if self._log_flush_task and not self._log_flush_task.done():
            return

        async def _delayed():
            await asyncio.sleep(0.4)
            await self._flush_log_buffer()
            self._log_flush_task = None

        self._log_flush_task = asyncio.create_task(_delayed())

    async def log_bot_event(self, bot_id: str, level: str, message: str, *, meta: dict | None = None):
        self.logger.info(f"[BOT {bot_id}] {level} - {message}")
        meta_json = json.dumps(meta) if meta else None
        self._log_buffer.append((bot_id, level, message, meta_json))
        if len(self._log_buffer) >= 12:
            await self._flush_log_buffer()
        else:
            await self._schedule_log_flush()
        try:
            sym = self.active_bots.get(bot_id, {}).get("symbol")
            from app.services.notifications.dispatcher import notify_bot_log
            import asyncio
            asyncio.create_task(
                notify_bot_log(bot_id, level, message, symbol=sym, meta=meta)
            )
        except Exception:
            pass

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

    def _get_model_staleness(self, bot_id: str) -> dict | None:
        """Return model staleness report, or None if meta-label not configured."""
        try:
            bot = self.active_bots.get(bot_id) or {}
            cfg = bot.get("config") or {}
            if isinstance(cfg, str):
                import json as _json
                cfg = _json.loads(cfg) if cfg else {}
            mode = str(cfg.get("meta_label_model_mode", "")).lower()
            if mode not in ("gbm", "hybrid") and not cfg.get("meta_label_model_enabled"):
                return None
            from app.services.bots.meta_label_operational import get_model_staleness_report
            return get_model_staleness_report(bot_id)
        except Exception:
            return None

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
            """
            SELECT bot_id, level, message, timestamp, meta
            FROM bot_logs ORDER BY timestamp DESC LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        conn.close()
        out = []
        for row in rows:
            item = dict(row)
            raw_meta = item.pop("meta", None)
            if raw_meta:
                try:
                    item["meta"] = json.loads(raw_meta)
                except (json.JSONDecodeError, TypeError):
                    item["meta"] = None
            out.append(item)
        return out

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
        def _sync_snapshots() -> None:
            for bot_id in list(self.active_bots.keys()):
                self.record_snapshot_for_bot(bot_id)

        await run_db(_sync_snapshots)

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
        position = bot_positions.get_bot_position(bot_id, bot["symbol"])
        pos_payload = None
        if abs(position["size"]) > 1e-8:
            pos_payload = position
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
            "position": pos_payload,
            "stats": stats,
            "trades": bot_analytics.get_trades(bot_id, 50),
            "snapshots": bot_analytics.get_snapshots(bot_id, 30),
            "consecutive_losses": bot_analytics.get_recent_consecutive_losses(bot_id),
            "risk_hold": get_bot_entry_hold(
                {**bot, "total_pnl": float(stats.get("total_pnl") or 0)},
            ),
            "model_staleness": self._get_model_staleness(bot_id),
        }

    async def update_bot_config(self, bot_id: str, config_patch: dict) -> dict:
        if not bot_id:
            raise ValueError("bot_id is required")
        if not isinstance(config_patch, dict):
            raise ValueError("config patch must be an object")

        if bot_id in self.active_bots:
            bot = self.active_bots[bot_id]
        else:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bots WHERE id = ?", (bot_id,))
            row = cursor.fetchone()
            conn.close()
            if not row:
                raise ValueError("Bot not found")
            bot = dict(row)
            bot["config"] = json.loads(bot["config"])

        merged_config = {**(bot.get("config") or {}), **config_patch}
        merged_config = normalize_bot_config(
            merged_config,
            bot_timeframe=bot.get("timeframe"),
        )
        bot_cfg = merge_tp_config(bot.get("strategy", ""), merged_config)

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bots SET config = ? WHERE id = ?",
            (json.dumps(merged_config), bot_id),
        )
        conn.commit()
        conn.close()

        if bot_id in self.active_bots:
            self.active_bots[bot_id]["config"] = merged_config
            self._refresh_strategy_instance(bot_id)

        symbol = bot["symbol"]
        pos = bot_positions.get_bot_position(bot_id, symbol)
        if abs(pos["size"]) > 1e-8:
            side = "BUY" if pos["size"] > 0 else "SELL"
            sl_pct = merged_config.get("trailing_stop_percent") or merged_config.get("stop_loss_percent")
            tp_pct, tp_price = resolve_take_profit(bot_cfg, {}, side, pos["avg_price"])
            bot_positions.update_bot_risk(
                bot_id,
                symbol,
                pos["avg_price"],
                side,
                stop_loss_percent=sl_pct,
                take_profit_percent=tp_pct,
                take_profit_price=tp_price,
            )
            owners = bot_positions.get_symbol_owners(symbol)
            if len(owners) == 1 and abs(owners[0]["size"] - pos["size"]) < 1e-6:
                await self.oms.update_position_sl_tp(
                    symbol,
                    stop_loss_percent=sl_pct,
                    take_profit_percent=tp_pct,
                    take_profit_price=tp_price,
                )

        detail = self.get_bot_detail(bot_id)
        if not detail:
            raise ValueError("Bot not found after config update")
        return detail

    def _refresh_strategy_instance(self, bot_id: str) -> None:
        bot = self.active_bots.get(bot_id)
        if not bot:
            return
        strategy = bot.get("strategy")
        mode = (bot.get("execution_mode") or "BAR_CLOSE").upper()
        runtime = _strategy_runtime_config(bot_id, bot)
        if mode == "TICK" or is_tick_strategy(strategy):
            bot["tick_strategy_instance"] = get_tick_strategy(strategy, runtime)
            bot["strategy_instance"] = None
        else:
            bot["strategy_instance"] = get_strategy(strategy, runtime)
            bot["tick_strategy_instance"] = None

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

    async def process_market_tick(self, symbol: str, ohlcv_1m: list | None = None, *, feed=None):
        try:
            from app.services.notifications.alert_rules.engine import maybe_evaluate_alert_rules

            await maybe_evaluate_alert_rules(symbol, ohlcv_1m=ohlcv_1m, feed=feed)
        except Exception as exc:
            self.logger.debug("Alert rule evaluation skipped for %s: %s", symbol, exc)

        if TERMINAL_MODE != "SIMULATED" and not ALLOW_LIVE_BOTS:
            return
        if system_state.is_safe_mode_active():
            return

        if not self.active_bots or not any(
            b["symbol"] == symbol and b.get("status") == "RUNNING"
            for b in self.active_bots.values()
        ):
            return

        await self._warm_chart_agent_caches(
            symbol,
            ohlcv_1m,
            feed=feed,
            timeframes=None if not is_live_massive() else {"1m"},
        )

        timeframes = {
            _bot_bar_timeframe(bot)
            for bot in self.active_bots.values()
            if bot["symbol"] == symbol
            and bot.get("status") == "RUNNING"
            and bot.get("execution_mode", "BAR_CLOSE") != "TICK"
            and bot.get("strategy_instance")
        }
        if not timeframes:
            return

        for timeframe in sorted(timeframes):
            if feed is not None:
                ohlcv = get_bot_candles(symbol, feed, timeframe=timeframe)
            elif is_live_massive():
                # Massive HT/1m must come from feed REST/WS — never resample a 1m tail.
                continue
            elif ohlcv_1m:
                ohlcv = candles_for_timeframe(ohlcv_1m, timeframe)
            else:
                continue

            if not ohlcv or not self._bar_tracker.check(symbol, ohlcv, timeframe=timeframe):
                continue

            await self._evaluate_bar_close_bots(symbol, timeframe, ohlcv)

    async def process_massive_ht_bar_close(
        self,
        symbol: str,
        feed,
        timeframes: set[str] | None = None,
    ) -> None:
        """LIVE_MASSIVE: evaluate HT BAR_CLOSE bots from native REST/cache only."""
        if not is_live_massive() or not ALLOW_LIVE_BOTS or feed is None:
            return
        if not self.active_bots:
            return

        tfs = timeframes or {
            _bot_bar_timeframe(bot)
            for bot in self.active_bots.values()
            if bot["symbol"] == symbol
            and bot.get("status") == "RUNNING"
            and bot.get("execution_mode", "BAR_CLOSE") != "TICK"
            and _bot_bar_timeframe(bot) != "1m"
        }
        if not tfs:
            return

        for timeframe in sorted(tfs):
            if timeframe == "1m":
                continue
            ohlcv = await run_db(
                get_bot_candles, symbol, feed, timeframe=timeframe,
            )
            if not ohlcv or len(ohlcv) < 2:
                continue
            if not self._bar_tracker.check(symbol, ohlcv, timeframe=timeframe):
                continue
            warm_tfs = {timeframe}
            for bot in self.active_bots.values():
                if bot.get("status") != "RUNNING":
                    continue
                if bot["symbol"] != symbol:
                    continue
                if normalize_strategy_name(bot.get("strategy", "")) != "CHART_AGENT":
                    continue
                if _bot_bar_timeframe(bot) != timeframe:
                    continue
                confirm_tf = (bot.get("config", {}) or {}).get("confirm_timeframe", "").strip()
                if not confirm_tf:
                    continue
                try:
                    warm_tfs.add(normalize_timeframe(confirm_tf))
                except ValueError:
                    continue
            await self._warm_chart_agent_caches(
                symbol,
                None,
                feed=feed,
                timeframes=warm_tfs,
            )
            await self._evaluate_bar_close_bots(symbol, timeframe, ohlcv)
            try:
                from app.services.notifications.alert_rules.engine import evaluate_rules_for_bar

                await evaluate_rules_for_bar(symbol, timeframe, ohlcv)
            except Exception as exc:
                self.logger.debug("HT alert rules skipped %s %s: %s", symbol, timeframe, exc)

    async def _warm_chart_agent_caches(
        self,
        symbol: str,
        ohlcv_1m: list | None,
        *,
        feed=None,
        timeframes: set[str] | None = None,
    ) -> None:
        """Prefetch analyst cache for active CHART_AGENT symbol+TF pairs on this symbol."""
        try:
            from app.services.agent.chart_analyst import get_chart_analyst

            analyst = get_chart_analyst()
            targets = [
                (sym, tf)
                for sym, tf in analyst.chart_agent_targets(self)
                if sym == symbol
            ]
            if timeframes is not None:
                allowed = {normalize_timeframe(tf) for tf in timeframes}
                targets = [(sym, tf) for sym, tf in targets if tf in allowed]
            if not targets:
                return

            warmed_confirm: set[tuple[str, str]] = set()
            for sym, tf in targets:
                if feed is not None:
                    ohlcv = get_bot_candles(sym, feed, timeframe=tf)
                elif ohlcv_1m:
                    ohlcv = candles_for_timeframe(ohlcv_1m, tf)
                else:
                    continue
                if not ohlcv or len(ohlcv) < 2:
                    continue

                bar_time = coerce_bar_time(ohlcv[-2].get("time"))
                force_llm = any(
                    b.get("config", {}).get("use_llm")
                    for b in self.active_bots.values()
                    if b["symbol"] == sym
                    and normalize_strategy_name(b.get("strategy", "")) == "CHART_AGENT"
                    and _bot_bar_timeframe(b) == tf
                )
                await analyst.ensure_for_bar(
                    sym,
                    ohlcv,
                    bar_time,
                    timeframe=tf,
                    force_llm=force_llm,
                )

                for bot in self.active_bots.values():
                    if bot.get("status") != "RUNNING":
                        continue
                    if bot["symbol"] != sym:
                        continue
                    if normalize_strategy_name(bot.get("strategy", "")) != "CHART_AGENT":
                        continue
                    if _bot_bar_timeframe(bot) != tf:
                        continue
                    confirm_tf = (bot.get("config", {}) or {}).get("confirm_timeframe", "").strip()
                    if not confirm_tf:
                        continue
                    try:
                        cf_tf = normalize_timeframe(confirm_tf)
                    except ValueError:
                        continue
                    warm_key = (sym, cf_tf)
                    if warm_key in warmed_confirm:
                        continue
                    warmed_confirm.add(warm_key)
                    if feed is not None:
                        cf_candles = get_bot_candles(sym, feed, timeframe=cf_tf)
                    elif ohlcv_1m:
                        cf_candles = candles_for_timeframe(ohlcv_1m, cf_tf)
                    else:
                        continue
                    if not cf_candles or len(cf_candles) < 2:
                        continue
                    cf_bar = coerce_bar_time(cf_candles[-2].get("time"))
                    await analyst.ensure_for_bar(
                        sym,
                        cf_candles,
                        cf_bar,
                        timeframe=cf_tf,
                        force_llm=False,
                    )
        except RuntimeError:
            pass

    async def _evaluate_bar_close_bots(self, symbol: str, timeframe: str, ohlcv_data: list):
        running = [
            (bot_id, bot)
            for bot_id, bot in list(self.active_bots.items())
            if bot["symbol"] == symbol
            and bot.get("status") == "RUNNING"
            and bot.get("execution_mode", "BAR_CLOSE") != "TICK"
            and bot.get("strategy_instance")
            and _bot_bar_timeframe(bot) == timeframe
        ]
        if not running:
            return

        screener_groups: dict[tuple, list[tuple]] = {}
        for bot_id, bot in running:
            bot_strategy = bot["strategy"]
            bot_config = bot.get("config", {})
            strat_key = normalize_strategy_name(bot_strategy)
            key = (strat_key, config_cache_key(strat_key, bot_config))
            screener_groups.setdefault(key, []).append((bot_id, bot))

        for (strat_key, _), bot_list in screener_groups.items():
            sample_bot = bot_list[0][1]
            bot_config = sample_bot.get("config", {})
            df = await asyncio.to_thread(
                self.screener.process_candles,
                symbol,
                ohlcv_data,
                bot_config,
                strat_key,
            )
            if df.empty or len(df) < 2:
                continue

            for bot_id, bot in bot_list:
                bot_strategy = bot["strategy"]
                bot_config = bot.get("config", {})
                strat = bot.get("strategy_instance")
                if not strat:
                    continue

                bot_df = prepare_strategy_df(df.copy(), bot_strategy, bot_config)
                filter_name = str((bot_config or {}).get("filter_strategy") or "").strip()
                if filter_name:
                    filt_key = normalize_strategy_name(filter_name)
                    filt_cfg = merge_strategy_config(
                        filt_key,
                        (bot_config or {}).get("filter_config") or {},
                    )
                    bot_df = prepare_strategy_df(bot_df, filt_key, filt_cfg)
                eval_row = bot_df.iloc[-2].to_dict()
                bar_time = eval_row.get("time")
                eval_price = eval_row.get("close")

                # Inject current position side for exit signal detection (3.2-A)
                bot_pos = self._get_bot_position(bot_id, symbol)
                pos_size = float(bot_pos.get("size") or 0.0)
                eval_row["_current_side"] = "BUY" if pos_size > 0 else ("SELL" if pos_size < 0 else "NONE")

                # ── Time Stop Check ──
                signal = None
                signal_data = {}
                time_stop_bars = int((bot_config or {}).get("time_stop_bars") or 0)
                if pos_size != 0 and time_stop_bars > 0:
                    opened_at = float(bot_pos.get("opened_at") or 0.0)
                    if opened_at > 0 and bar_time is not None:
                        from app.services.market.timeframes import timeframe_to_ms
                        tf_ms = timeframe_to_ms(timeframe)
                        if tf_ms > 0:
                            bars_elapsed = (bar_time - opened_at) / tf_ms
                            if bars_elapsed >= time_stop_bars:
                                signal = "CLOSE"
                                signal_data = {"signal": "CLOSE", "reasons": [f"Time stop reached ({time_stop_bars} bars)"]}
                                await self.log_bot_event(bot_id, "INFO", f"Time stop reached ({time_stop_bars} bars), forcing CLOSE")
                
                if not signal:
                    signal_data = strat.evaluate(eval_row)
                    signal = signal_data.get("signal")
                if strat_key == "CHART_AGENT" and signal not in ("BUY", "SELL", "CLOSE"):
                    reject = signal_data.get("reject_reason")
                    if reject:
                        inc("bot_orders_blocked_total", labels={"strategy": "CHART_AGENT", "reason": "filter"})
                        await self.log_bot_event(
                            bot_id,
                            "WARN",
                            f"CHART_AGENT skipped: {reject}",
                            meta={
                                "event_type": "chart_agent_skip",
                                "bar_time": bar_time,
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "reject_reason": reject,
                            },
                        )
                if signal not in ("BUY", "SELL", "CLOSE"):
                    continue

                if bar_time is not None and bot.get("last_signal_bar_time") == bar_time:
                    continue

                # ── Multi-timeframe confirmation gate ──
                confirm_tf = (bot_config or {}).get("confirm_timeframe", "").strip()
                if confirm_tf and signal in ("BUY", "SELL"):
                    htf_bias = await self._get_htf_bias(symbol, confirm_tf)
                    if signal == "BUY" and htf_bias == "BEAR":
                        inc("bot_orders_blocked_total", labels={"strategy": strat_key, "reason": "htf_gate"})
                        await self.log_bot_event(
                            bot_id, "WARN",
                            f"HTF gate blocked BUY — {confirm_tf} bias is BEAR",
                        )
                        continue
                    if signal == "SELL" and htf_bias == "BULL":
                        inc("bot_orders_blocked_total", labels={"strategy": strat_key, "reason": "htf_gate"})
                        await self.log_bot_event(
                            bot_id, "WARN",
                            f"HTF gate blocked SELL — {confirm_tf} bias is BULL",
                        )
                        continue

                # ── Strategy composition / filter gate ──
                strat_filter = _build_filter(bot_config)
                if strat_filter and signal in ("BUY", "SELL"):
                    allowed, reason = strat_filter.evaluate_gate(eval_row, signal)
                    if not allowed:
                        inc("bot_orders_blocked_total", labels={"strategy": strat_key, "reason": "filter_gate"})
                        await self.log_bot_event(
                            bot_id, "WARN",
                            f"Filter gate blocked {signal}: {reason}",
                        )
                        continue

                # Event gates apply to new entries only — never block exit signals.
                if signal in ("BUY", "SELL") and pos_size == 0:
                    from app.services.altdata.event_policy import check_entry_gates

                    gate_ok, gate_reason, gate_kind = check_entry_gates(
                        symbol, bar_time, bot_config, is_exit=False,
                    )
                    if not gate_ok and gate_reason:
                        inc(
                            "bot_orders_blocked_total",
                            labels={"strategy": strat_key, "reason": gate_kind or "event"},
                        )
                        await self.log_bot_event(
                            bot_id, "WARN",
                            f"Event gate blocked {signal}: {gate_reason}",
                            meta={"event_type": "event_gate", "gate": gate_kind},
                        )
                        continue

                await self._handle_signal(bot, signal, signal_data, eval_price, bar_time)

    async def _get_htf_bias(self, symbol: str, confirm_tf: str) -> str:
        """Compute higher-timeframe trend bias for multi-TF confirmation.

        Returns 'BULL', 'BEAR', or 'NEUTRAL'.
        Uses SuperTrend direction if available, otherwise falls back to EMA slope.
        """
        try:
            from app.services.market.timeframes import normalize_timeframe

            cf_tf = normalize_timeframe(confirm_tf)
            feed = getattr(self.oms, "feed", None)

            if feed is not None:
                ohlcv = get_bot_candles(symbol, feed, timeframe=cf_tf)
            else:
                return "NEUTRAL"

            if not ohlcv or len(ohlcv) < 3:
                return "NEUTRAL"

            # Quick bias: compare last 2 closed bars' close prices + simple EMA direction
            bar_prev = ohlcv[-3]  # 2 bars ago (closed)
            bar_last = ohlcv[-2]  # last closed bar

            close_last = float(bar_last.get("close", 0))
            close_prev = float(bar_prev.get("close", 0))

            if close_last <= 0 or close_prev <= 0:
                return "NEUTRAL"

            # Simple trend: higher highs/higher lows = BULL, lower/lower = BEAR
            high_last = float(bar_last.get("high", 0))
            low_last = float(bar_last.get("low", 0))
            high_prev = float(bar_prev.get("high", 0))
            low_prev = float(bar_prev.get("low", 0))

            if close_last > close_prev and low_last >= low_prev:
                return "BULL"
            if close_last < close_prev and high_last <= high_prev:
                return "BEAR"
            return "NEUTRAL"

        except Exception:
            return "NEUTRAL"

    async def process_price_tick(self, symbol: str, price: float, time_ms: int):
        """Evaluate tick-mode bots on each price update (separate from bar-close path)."""
        if TERMINAL_MODE != "SIMULATED" and not ALLOW_LIVE_BOTS:
            return
        if system_state.is_safe_mode_active():
            return
        if not self.active_bots or price <= 0:
            return

        self._tick_screener.record(symbol, price, time_ms)

        for bot_id, bot in list(self.active_bots.items()):
            if bot.get("execution_mode") != "TICK":
                continue
            if bot["symbol"] != symbol or bot.get("status") != "RUNNING":
                continue

            strat = bot.get("tick_strategy_instance")
            if not strat:
                continue

            cfg = merge_tick_config(bot["strategy"], bot.get("config", {}))
            cooldown_ms = int(float(cfg.get("tick_cooldown_sec", 10)) * 1000)
            last_at = int(bot.get("last_tick_signal_at") or 0)
            if last_at and (time_ms - last_at) < cooldown_ms:
                continue

            lookback = int(cfg.get("lookback_ticks", 20))
            ctx = self._tick_screener.context(symbol, price, time_ms, lookback)
            if ctx is None:
                continue

            bot_pos = self._get_bot_position(bot_id, symbol)
            pos_size = float(bot_pos.get("size") or 0.0)
            
            signal = None
            signal_data = {}
            time_stop_sec = int(cfg.get("time_stop_sec", 0))
            if pos_size != 0 and time_stop_sec > 0:
                opened_at = float(bot_pos.get("opened_at") or 0.0)
                if opened_at > 0:
                    elapsed_sec = (time_ms - opened_at) / 1000.0
                    if elapsed_sec >= time_stop_sec:
                        signal = "CLOSE"
                        signal_data = {"signal": "CLOSE", "reasons": [f"Time stop reached ({time_stop_sec}s)"]}
                        await self.log_bot_event(bot_id, "INFO", f"Time stop reached ({time_stop_sec}s), forcing CLOSE")

            if not signal:
                signal_data = strat.evaluate(ctx, price)
                signal = signal_data.get("signal")
            if signal not in ("BUY", "SELL", "CLOSE"):
                continue

            tick_bucket = time_ms // 1000
            await self._handle_signal(bot, signal, signal_data, price, tick_bucket)
            bot["last_tick_signal_at"] = time_ms

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
            if pos_size < 0:
                # Close existing short first
                await self._execute_order(
                    bot, "BUY", abs(pos_size), eval_price, signal_data,
                    is_exit=True, bar_time=bar_time,
                    entry_price=float(bot_pos.get("avg_price") or eval_price),
                )
            else:
                await self._execute_order(
                    bot, "BUY", None, eval_price, signal_data,
                    is_exit=False, bar_time=bar_time,
                )
            return

        if signal == "SELL":
            if pos_size < 0:
                return
            if pos_size > 0:
                # Close existing long first
                await self._execute_order(
                    bot, "SELL", abs(pos_size), eval_price, signal_data,
                    is_exit=True, bar_time=bar_time,
                    entry_price=float(bot_pos.get("avg_price") or eval_price),
                )
            else:
                # Open new short (risk_gate enforces direction_mode)
                await self._execute_order(
                    bot, "SELL", None, eval_price, signal_data,
                    is_exit=False, bar_time=bar_time,
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

        if not signal_ledger.claim_signal(signal_id, bot_id, bar_time, signal_kind):
            return

        strat_key = normalize_strategy_name(bot.get("strategy", ""))
        inc("bot_signals_total", labels={"strategy": strat_key, "signal": signal_kind})

        if not is_exit:
            account_balance = self.get_account_balance()
            risk_amount = account_balance * RISK_PCT

            stop_loss_price = signal_data.get("stop_loss_price")
            if not stop_loss_price:
                sl_dist = signal_data.get("stop_loss_distance", current_price * 0.02)
                stop_loss_price = (
                    current_price - sl_dist if side == "BUY" else current_price + sl_dist
                )

            price_diff = abs(current_price - stop_loss_price)
            if price_diff <= 0:
                signal_ledger.release_signal(signal_id)
                await self.log_bot_event(bot_id, "ERROR", "Stop loss distance is 0. Aborting trade.")
                _record_order_blocked(bot, "Stop loss distance is 0")
                return

            quantity = risk_amount / price_diff
            bot_cfg = merge_strategy_config(bot.get("strategy", ""), bot.get("config", {}))
            if bot_cfg.get("use_vol_sizing", True):
                size_factor = float(signal_data.get("size_factor") or 1.0)
                if size_factor > 0 and size_factor != 1.0:
                    quantity *= size_factor

            # 3.3-A: Confidence-scaled sizing — bet proportionally to signal conviction.
            # Scale range [0.7, 1.3] centred at confidence=0.75. Opt-out via use_confidence_sizing=false.
            if bot_cfg.get("use_confidence_sizing", True):
                conf = float(signal_data.get("confidence") or 0.55)
                # Linear interpolation: conf 0.55 → 0.76×, 0.75 → 1.00×, 1.00 → 1.30×
                conf_scale = 0.7 + (conf * 0.6)
                conf_scale = max(0.5, min(1.5, conf_scale))
                quantity *= conf_scale

            if bot_cfg.get("use_meta_label_sizing"):
                snap = signal_data.get("insight_snapshot") or {
                    "score": signal_data.get("score"),
                    "confidence": signal_data.get("confidence"),
                    "sub_reports": signal_data.get("sub_reports"),
                }
                from app.services.bots.meta_label_model import predict_meta_label_prob

                prob = predict_meta_label_prob(
                    bot_id,
                    snap,
                    symbol=symbol,
                    side=side,
                    timeframe=str(bot_cfg.get("timeframe") or bot.get("timeframe") or "1m"),
                    bar_time=bar_time,
                )
                if prob is not None:
                    ml_scale = 0.7 + (0.6 * prob)
                    ml_scale = max(0.5, min(1.5, ml_scale))
                    quantity *= ml_scale

            # 3.4-A: Regime-Adaptive Sizing (Kelly scaling during drawdowns)
            if bot_cfg.get("use_regime_sizing", True):
                from app.services.bots.positions import get_recent_closed_trades_pnl
                recent_pnls = get_recent_closed_trades_pnl(bot_id, limit=3)
                if len(recent_pnls) == 3 and all(pnl < 0 for pnl in recent_pnls):
                    quantity *= 0.5
                    await self.log_bot_event(bot_id, "INFO", "Bot in drawdown (3 consecutive losses). Halving allocation size.")

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
            signal_ledger.release_signal(signal_id)
            await self.log_bot_event(bot_id, "WARN", f"Risk blocked: {decision.reason}")
            _record_order_blocked(bot, decision.reason)
            if "Daily loss limit" in decision.reason:
                await self._halt_bot(bot_id, decision.reason)
            elif "Consecutive-loss streak" in decision.reason and bot.get("status") == "RUNNING":
                await self._set_bot_status(bot_id, "PAUSED")
                await self.log_bot_event(
                    bot_id,
                    "WARN",
                    f"Auto-paused after loss streak — {decision.reason}",
                )
            if (
                "Consecutive-loss streak" in decision.reason
                or "Cooling-off" in decision.reason
            ):
                await publish_bots_update(self.broadcast_cb, self.list_bots_public())
            return

        quantity = decision.quantity if decision.quantity is not None else quantity
        if decision.reason not in ("OK",):
            await self.log_bot_event(bot_id, "INFO", decision.reason)

        if not is_exit:
            bot_cfg = bot.get("config") or {}
            if isinstance(bot_cfg, str):
                try:
                    bot_cfg = json.loads(bot_cfg) if bot_cfg else {}
                except json.JSONDecodeError:
                    bot_cfg = {}
            entry_leverage = float(bot_cfg.get("leverage") or 1)
            port_decision = self._risk_gate.validate_portfolio(
                self.oms,
                symbol,
                side,
                quantity,
                current_price,
                is_exit=False,
                entry_leverage=entry_leverage,
            )
            if not port_decision.allowed:
                signal_ledger.release_signal(signal_id)
                await self.log_bot_event(bot_id, "WARN", f"Portfolio risk blocked: {port_decision.reason}")
                _record_order_blocked(bot, f"Portfolio risk blocked: {port_decision.reason}")
                return
            if port_decision.quantity is not None and port_decision.quantity < quantity:
                quantity = port_decision.quantity
                if port_decision.reason not in ("OK",):
                    await self.log_bot_event(bot_id, "INFO", port_decision.reason)

        if quantity <= 0:
            signal_ledger.release_signal(signal_id)
            _record_order_blocked(bot, "quantity too small")
            return

        if quantity < 0.001:
            signal_ledger.release_signal(signal_id)
            await self.log_bot_event(bot_id, "INFO", "Signal ignored: quantity too small.")
            _record_order_blocked(bot, "quantity too small")
            return

        action = "Exit" if is_exit else "Entry"
        bot["last_signal_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        timeframe = bot.get("timeframe", "1m")
        sym_key = str(symbol or "").upper()
        insight_snapshot = signal_data.get("insight_snapshot") if not is_exit else None
        reasons = signal_data.get("reasons") or []
        confidence = signal_data.get("confidence")
        top_reason = reasons[0] if reasons else None
        if not is_exit and confidence is not None:
            conf_pct = int(round(float(confidence) * 100))
            reason_bit = f" — {top_reason}" if top_reason else ""
            log_msg = (
                f"{action} {side} signal @ {current_price:.2f}, qty {quantity:.4f} "
                f"({conf_pct}% conf{reason_bit})"
            )
        else:
            log_msg = f"{action} {side} signal @ {current_price:.2f}, qty {quantity:.4f}"
        signal_meta = {
            "event_type": "signal",
            "bar_time": bar_time,
            "signal_id": signal_id,
            "insight_id": signal_data.get("insight_id") or (
                f"{sym_key}:{timeframe}:{bar_time}" if bar_time is not None else None
            ),
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "is_exit": is_exit,
            "strategy": bot.get("strategy"),
        }
        if not is_exit:
            if confidence is not None:
                signal_meta["confidence"] = confidence
            if signal_data.get("score") is not None:
                signal_meta["score"] = signal_data.get("score")
            if reasons:
                signal_meta["reasons"] = reasons[:5]
            summary = signal_data.get("sub_reports_summary")
            if summary:
                signal_meta["sub_reports"] = summary
        await self.log_bot_event(
            bot_id,
            "INFO",
            log_msg,
            meta=signal_meta,
        )

        tp_pct = None
        tp_price = None
        if not is_exit:
            bot_cfg = merge_tp_config(bot.get("strategy", ""), bot.get("config", {}))
            tp_pct, tp_price = resolve_take_profit(bot_cfg, signal_data, side, current_price)

        order_req = {
            "symbol": symbol,
            "type": "MARKET",
            "side": side,
            "quantity": quantity,
            "stop_loss_percent": (
                None if is_exit
                else bot.get("config", {}).get("trailing_stop_percent")
                or bot.get("config", {}).get("stop_loss_percent")
            ),
            "take_profit_percent": None if is_exit else tp_pct,
            "take_profit_price": None if is_exit else tp_price,
            "bot_id": bot_id,
            "signal_id": signal_id,
        }

        if not uses_paper_oms():
            signal_ledger.mark_signal_submitted(
                signal_id,
                broker=TERMINAL_MODE,
                payload={
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "bot_id": bot_id,
                },
            )

        try:
            result = await self.oms.place_order(order_req)

            if result.get("status") == "success":
                if bar_time is not None:
                    bot["last_signal_bar_time"] = bar_time
                order_id = result.get("order_id")
                fill_price = float(result.get("average_fill_price") or current_price)
                filled_qty = float(result.get("filled_quantity") or quantity or 0)
                live_submitted = not uses_paper_oms() and result.get("average_fill_price") is None

                if live_submitted:
                    signal_ledger.mark_signal_submitted(
                        signal_id,
                        order_id=order_id,
                        broker=TERMINAL_MODE,
                        payload={
                            "symbol": symbol,
                            "side": side,
                            "quantity": quantity,
                            "bot_id": bot_id,
                        },
                    )
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
                        insight_snapshot=insight_snapshot,
                    )
                    await self.log_bot_event(
                        bot_id,
                        "SUCCESS",
                        f"Submitted {side} {filled_qty:.4f} @ ~{current_price:.4f} (order {order_id}). Awaiting broker fill.",
                    )
                else:
                    fill_msg = f"Filled {side} {filled_qty:.4f} @ {fill_price:.4f} (order {order_id})."
                    if not is_exit and (tp_pct is not None or tp_price is not None):
                        fill_msg += f" {format_tp_summary(tp_pct, tp_price)}."
                    await self.log_bot_event(bot_id, "SUCCESS", fill_msg)

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
                        insight_snapshot=insight_snapshot,
                    )
                    if is_exit:
                        from app.services.bots.calibration import get_calibration_store

                        get_calibration_store().invalidate(bot_id)

                        # Track meta-label prediction accuracy for staleness monitoring
                        try:
                            from app.services.bots.meta_label_operational import record_prediction_outcome

                            entry_snap = insight_snapshot or signal_data.get("insight_snapshot")
                            if entry_snap and trade_pnl is not None:
                                pred_prob = entry_snap.get("meta_label_prob")
                                if pred_prob is not None:
                                    record_prediction_outcome(
                                        bot_id,
                                        predicted_prob=float(pred_prob),
                                        actual_win=(trade_pnl > 0),
                                    )
                        except Exception:
                            pass
                    self.record_snapshot_for_bot(bot_id)
                    signal_ledger.mark_signal_filled(signal_id, order_id=order_id)

                await publish_post_trade_bundle(
                    self.broadcast_cb,
                    self.oms.get_account_data(),
                    self.oms.get_trade_history(),
                )
                await publish_bots_update(self.broadcast_cb, self.list_bots_public())
                detail = self.get_bot_detail(bot_id)
                if detail:
                    await publish_bot_detail(self.broadcast_cb, detail)
                self._risk_gate.invalidate_portfolio_cache()
                log_event(
                    logger,
                    "bot_order_filled",
                    bot_id=bot_id,
                    symbol=symbol,
                    action="bot_order",
                    insight_id=signal_data.get("insight_id"),
                )
            else:
                msg = result.get("message", "Unknown error")
                status = result.get("status", "error")
                if status == "ambiguous":
                    _record_ambiguous_outcome(
                        order_req,
                        signal_id,
                        msg,
                        bot_id=bot_id,
                        order_id=result.get("order_id"),
                    )
                else:
                    signal_ledger.mark_signal_failed(signal_id, msg)
                _record_order_blocked(bot, msg)
                if status == "ambiguous":
                    await self.log_bot_event(
                        bot_id,
                        "WARN",
                        f"Ambiguous order outcome: {msg}",
                    )
                else:
                    await self.log_bot_event(bot_id, "ERROR", f"Order failed: {msg}")
                if TERMINAL_MODE != "SIMULATED" and status != "ambiguous":
                    await self.log_bot_event(
                        bot_id,
                        "WARN",
                        "Live order not retried (at-most-once). Reconcile manually if needed.",
                    )
        except Exception as e:
            if not uses_paper_oms():
                _record_ambiguous_outcome(
                    order_req,
                    signal_id,
                    str(e),
                    bot_id=bot_id,
                )
            else:
                signal_ledger.release_signal(signal_id)
            await self.log_bot_event(bot_id, "ERROR", f"Order exception: {str(e)}")
            if not uses_paper_oms():
                await self.log_bot_event(
                    bot_id,
                    "WARN",
                    "Ambiguous live outcome — do not resend; reconcile via broker.",
                )

    async def create_bot(
        self,
        strategy: str,
        symbol: str,
        timeframe: str,
        allocation: float,
        config: dict,
        execution_mode: str = "BAR_CLOSE",
    ):
        if TERMINAL_MODE != "SIMULATED" and not ALLOW_LIVE_BOTS:
            raise ValueError(
                "Live bot trading is disabled. Set ALLOW_LIVE_BOTS=true in .env to enable."
            )

        decision = self._risk_gate.validate_create(len(self.active_bots))
        if not decision.allowed:
            raise ValueError(decision.reason)

        strategy = normalize_strategy_name(strategy)
        mode = (execution_mode or "BAR_CLOSE").upper()
        if is_tick_strategy(strategy):
            mode = "TICK"
        if mode == "TICK":
            tf = "tick"
        else:
            if not is_valid_timeframe(timeframe or "1m"):
                raise ValueError(f"Unsupported bot timeframe: {timeframe}")
            tf = normalize_timeframe(timeframe or "1m")
        if strategy == "CHART_AGENT":
            config = {**(config or {}), "symbol": symbol, "timeframe": tf}
        config, _ = sanitize_bot_config(config or {})

        bot_id = str(uuid.uuid4())
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config, execution_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (bot_id, strategy, symbol, tf, "RUNNING", allocation, json.dumps(config), mode),
        )
        conn.commit()
        conn.close()

        self.active_bots[bot_id] = {
            "id": bot_id,
            "strategy": strategy,
            "symbol": symbol,
            "timeframe": tf,
            "status": "RUNNING",
            "allocation": allocation,
            "config": config,
            "execution_mode": mode,
            "last_signal_bar_time": None,
            "last_signal_at": None,
            "last_tick_signal_at": 0,
        }
        if mode == "TICK":
            self.active_bots[bot_id]["tick_strategy_instance"] = get_tick_strategy(
                strategy, _strategy_runtime_config(bot_id, self.active_bots[bot_id])
            )
            self.active_bots[bot_id]["strategy_instance"] = None
        else:
            self.active_bots[bot_id]["strategy_instance"] = get_strategy(
                strategy, _strategy_runtime_config(bot_id, self.active_bots[bot_id])
            )
            self.active_bots[bot_id]["tick_strategy_instance"] = None

        await self.log_bot_event(
            bot_id,
            "INFO",
            f"Bot created ({mode}) for {symbol} using {strategy}.",
        )
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

    def _mark_price(self, symbol: str, fallback: float = 0.0) -> float:
        feed = getattr(self.oms, "feed", None)
        if feed and hasattr(feed, "_symbols") and symbol in feed._symbols:
            return float(feed._symbols[symbol].get("price") or fallback)
        return fallback

    def _get_bot_dict(self, bot_id: str) -> dict | None:
        if bot_id in self.active_bots:
            return self.active_bots[bot_id]
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM bots WHERE id = ?", (bot_id,))
            row = cursor.fetchone()
            if not row:
                return None
            bot = dict(row)
            bot["config"] = json.loads(bot["config"]) if bot.get("config") else {}
            return bot
        finally:
            conn.close()

    async def flatten_weekend_non_crypto_positions(self) -> int:
        """Close open bot positions for non-crypto symbols during the weekend window."""
        from app.services.bots.time_windows import (
            in_weekend_flatten_window,
            should_flatten_symbol,
            weekend_flatten_bar_time,
        )

        if not in_weekend_flatten_window():
            return 0

        bar_time = weekend_flatten_bar_time()
        closed = 0
        for symbol, owners in bot_positions.list_owners_grouped().items():
            if not should_flatten_symbol(symbol):
                continue
            for owner in owners:
                size = float(owner.get("size") or 0)
                if abs(size) < 1e-8:
                    continue
                bot_id = owner["bot_id"]
                bot = self._get_bot_dict(bot_id)
                if not bot:
                    continue
                avg = float(owner.get("avg_price") or 0)
                price = self._mark_price(symbol, avg or 1.0)
                side = "SELL" if size > 0 else "BUY"
                signal_data = {
                    "signal": "CLOSE",
                    "reasons": ["Weekend flatten (non-crypto)"],
                }
                await self._execute_order(
                    bot,
                    side,
                    abs(size),
                    price,
                    signal_data,
                    is_exit=True,
                    bar_time=bar_time,
                    entry_price=avg or price,
                )
                closed += 1
        return closed

    async def close_stale_positions(self) -> int:
        """Auto-close bot positions that exceed max_position_hours."""
        from app.services.bots.position_duration import (
            duration_close_bar_time,
            is_position_stale,
            resolve_max_position_hours,
        )

        closed = 0
        now = time.time()
        for symbol, owners in bot_positions.list_owners_grouped().items():
            for owner in owners:
                size = float(owner.get("size") or 0)
                if abs(size) < 1e-8:
                    continue
                bot_id = owner["bot_id"]
                bot_config = owner.get("bot_config") or {}
                if resolve_max_position_hours(bot_config) is None:
                    continue

                opened_at = owner.get("opened_at")
                if opened_at is None:
                    opened_at = bot_positions.ensure_opened_at(bot_id, symbol)
                stale, reason, limit = is_position_stale(opened_at, bot_config, now=now)
                if not stale or limit is None or opened_at is None:
                    continue

                bot = self._get_bot_dict(bot_id)
                if not bot:
                    continue
                avg = float(owner.get("avg_price") or 0)
                price = self._mark_price(symbol, avg or 1.0)
                side = "SELL" if size > 0 else "BUY"
                await self._execute_order(
                    bot,
                    side,
                    abs(size),
                    price,
                    {"signal": "CLOSE", "reasons": [reason]},
                    is_exit=True,
                    bar_time=duration_close_bar_time(opened_at, limit),
                    entry_price=avg or price,
                )
                closed += 1
        return closed

    async def stop_all_bots(self):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM bots WHERE status != 'STOPPED'")
        db_ids = [
            row["id"] if isinstance(row, dict) else row[0]
            for row in cursor.fetchall()
        ]
        conn.close()

        all_ids = list(dict.fromkeys([*self.active_bots.keys(), *db_ids]))
        for bot_id in all_ids:
            await self.stop_bot(bot_id)
        return len(all_ids)

    def _public_bot_row(self, bot: dict, stats: dict) -> dict:
        bot_id = bot["id"]
        total_pnl = float(stats.get("total_pnl") or 0)
        streak = bot_analytics.get_recent_consecutive_losses(bot_id)
        row = {
            "id": bot_id,
            "strategy": bot["strategy"],
            "symbol": bot["symbol"],
            "timeframe": bot["timeframe"],
            "status": bot["status"],
            "allocation": bot["allocation"],
            "config": bot.get("config", {}),
            "execution_mode": bot.get("execution_mode", "BAR_CLOSE"),
            "daily_pnl": stats["daily_pnl"],
            "total_pnl": total_pnl,
            "trade_count": stats["trade_count"],
            "win_rate": stats["win_rate"],
            "last_signal_at": bot.get("last_signal_at"),
            "consecutive_losses": streak,
        }
        hold = get_bot_entry_hold({**bot, "total_pnl": total_pnl})
        if hold:
            row["risk_hold"] = hold
        return row

    def list_bots_public(self) -> list:
        bot_ids = [bot["id"] for bot in self.active_bots.values()]
        stats_map = bot_analytics.get_all_bot_stats(bot_ids)
        out = []
        for bot in self.active_bots.values():
            bot_id = bot["id"]
            stats = stats_map.get(bot_id, bot_analytics.get_bot_stats(bot_id))
            out.append(self._public_bot_row(bot, stats))
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

        bot_ids = [row["id"] for row in rows]
        stats_map = bot_analytics.get_all_bot_stats(bot_ids)

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
            stats = stats_map.get(bot_id, bot_analytics.get_bot_stats(bot_id))
            public = self._public_bot_row(bot, stats)
            public["exit_count"] = stats.get("exit_count")
            public["created_at"] = row.get("created_at")
            out.append(public)
        return out

    async def handle_sl_tp_exits(self, bot_exits: list[dict]):
        """Record SIM stop-loss / take-profit exits in bot analytics."""
        if not bot_exits:
            return

        def _sync_record() -> set[str]:
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
                from app.services.bots.calibration import get_calibration_store

                get_calibration_store().invalidate(bot_id)
                self.record_snapshot_for_bot(bot_id)
                touched.add(bot_id)
            return touched

        touched = await run_db(_sync_record)

        for exit_info in bot_exits:
            bot_id = exit_info["bot_id"]
            if bot_id not in touched:
                continue
            side = exit_info["side"]
            qty = float(exit_info["quantity"])
            price = float(exit_info["price"])
            entry_price = float(exit_info.get("entry_price") or price)
            trigger = exit_info.get("trigger_type", "SL/TP")
            trade_pnl = self._calc_exit_pnl(side, qty, price, entry_price)
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
        if uses_paper_oms():
            return 0
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
                insight_snapshot=p.get("insight_snapshot"),
            )
            bot_positions.apply_fill(p["bot_id"], p["symbol"], p["side"], filled_qty, fill_price, feed=getattr(self.oms, "feed", None))
            bot_analytics.delete_pending_fill(p["id"])
            if p.get("signal_id"):
                signal_ledger.mark_signal_filled(p["signal_id"], order_id=resolved_order_id)
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
