"""Agent automation pipeline — scanner deploy, walk-forward auto-deploy, OOS gates."""

from __future__ import annotations

import asyncio

from typing import Any

from app.services.bots.strategies import normalize_strategy_name
from app.services.market.timeframes import normalize_timeframe

ACTIVE_BOT_STATUSES = ("RUNNING", "PAUSED", "ERROR")


def active_bot_symbols(
    bot_manager,
    *,
    strategy: str = "CHART_AGENT",
    timeframe: str | None = None,
) -> set[str]:
    """Symbols that already have a live bot for strategy (+ optional TF)."""
    strat_key = normalize_strategy_name(strategy)
    tf_norm = normalize_timeframe(timeframe) if timeframe else None
    out: set[str] = set()
    for bot in bot_manager.active_bots.values():
        if bot.get("status") not in ACTIVE_BOT_STATUSES:
            continue
        if normalize_strategy_name(bot.get("strategy", "")) != strat_key:
            continue
        if tf_norm:
            raw_tf = (bot.get("timeframe") or "1m").strip()
            try:
                bot_tf = normalize_timeframe(raw_tf)
            except ValueError:
                bot_tf = "1m"
            if bot_tf != tf_norm:
                continue
        sym = str(bot.get("symbol") or "").upper()
        if sym:
            out.add(sym)
    return out


def pipeline_insight_deployed(bot_manager, insight_id: str) -> bool:
    """True when a bot was already deployed for this scanner insight id."""
    if not insight_id:
        return False
    for bot in bot_manager.active_bots.values():
        cfg = bot.get("config") or {}
        if cfg.get("scanner_insight_id") == insight_id:
            return True
    return False


def rank_scan_rows(
    rows: list[dict],
    *,
    signal_filter: str = "ACTIONABLE",
    min_confidence: float = 0.55,
    min_score: int = 2,
) -> list[dict]:
    """Filter and rank scanner rows for deployment."""
    filt = (signal_filter or "ACTIONABLE").upper()
    ranked: list[dict] = []
    for row in rows or []:
        signal = str(row.get("signal") or "NONE").upper()
        if filt == "ACTIONABLE" and signal not in ("BUY", "SELL"):
            continue
        if filt in ("BUY", "SELL") and signal != filt:
            continue
        try:
            conf = float(row.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        if conf < min_confidence:
            continue
        try:
            score = abs(int(row.get("score") or 0))
        except (TypeError, ValueError):
            score = 0
        if score < min_score:
            continue
        ranked.append({**row, "_rank_score": score * conf})

    ranked.sort(
        key=lambda r: (
            r.get("_rank_score") or 0,
            abs(int(r.get("score") or 0)),
            float(r.get("confidence") or 0),
        ),
        reverse=True,
    )
    return ranked


def build_scan_deploy_config(
    row: dict,
    base_config: dict | None,
    *,
    regime_routing: bool = True,
) -> dict:
    """Bot config for a scanner-ranked symbol."""
    cfg = dict(base_config or {})
    cfg.setdefault("min_confidence", 0.55)
    cfg.setdefault("symbol", row.get("symbol"))
    cfg.setdefault("timeframe", cfg.get("timeframe") or "1m")
    if regime_routing:
        cfg.setdefault("regime_routing_enabled", True)
        cfg.setdefault("elevated_min_confidence", 0.65)
        cfg.setdefault("elevated_min_score", 3)
    regime = row.get("atr_regime")
    if regime == "elevated":
        cfg.setdefault("block_elevated_vol", False)
    cfg["pipeline_source"] = "scanner"
    if row.get("insight_id"):
        cfg["scanner_insight_id"] = row["insight_id"]
    return cfg


async def deploy_from_scan(
    bot_manager,
    scanner,
    *,
    symbols: list[str],
    strategy: str = "CHART_AGENT",
    timeframe: str = "1m",
    allocation: float = 1000.0,
    max_deploy: int = 3,
    signal_filter: str = "ACTIONABLE",
    min_confidence: float = 0.6,
    min_score: int = 2,
    base_config: dict | None = None,
    skip_existing: bool = True,
    dry_run: bool = False,
    regime_routing: bool = True,
) -> dict[str, Any]:
    """Scan watchlist and deploy CHART_AGENT bots for top-ranked actionable symbols."""
    max_deploy = max(0, min(int(max_deploy), 20))
    scan = await scanner.scan(list(symbols), signal_filter="any", sort_by="score")
    rows = scan.get("rows") or []
    candidates = rank_scan_rows(
        rows,
        signal_filter=signal_filter,
        min_confidence=min_confidence,
        min_score=min_score,
    )

    existing = active_bot_symbols(bot_manager, strategy=strategy, timeframe=timeframe) if skip_existing else set()
    deployed: list[dict] = []
    skipped: list[dict] = []

    for row in candidates:
        if len(deployed) >= max_deploy:
            skipped.append({"symbol": row.get("symbol"), "reason": "max_deploy reached"})
            continue
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        if sym in existing:
            skipped.append({"symbol": sym, "reason": "bot already active for symbol/timeframe"})
            continue
        insight_id = row.get("insight_id")
        if insight_id and pipeline_insight_deployed(bot_manager, str(insight_id)):
            skipped.append({"symbol": sym, "reason": "pipeline insight already deployed"})
            continue
        deploy_cfg = build_scan_deploy_config(row, base_config, regime_routing=regime_routing)
        if dry_run:
            deployed.append({"symbol": sym, "dry_run": True, "config": deploy_cfg})
            existing.add(sym)
            continue
        bot_id = None
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                bot_id = await bot_manager.create_bot(
                    strategy,
                    sym,
                    timeframe,
                    float(allocation),
                    deploy_cfg,
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(0.5)
        if bot_id:
            deployed.append({"bot_id": bot_id, "symbol": sym, "signal": row.get("signal")})
            existing.add(sym)
        else:
            skipped.append({"symbol": sym, "reason": str(last_exc) if last_exc else "deploy failed"})

    return {
        "scanned_at": scan.get("scanned_at"),
        "scan_count": scan.get("count", len(rows)),
        "candidates": len(candidates),
        "deployed": deployed,
        "skipped": skipped,
        "dry_run": dry_run,
    }


def validate_walk_forward_oos(
    best_result: dict,
    *,
    min_oos_pnl: float = 0.0,
    min_oos_trades: int = 1,
) -> tuple[bool, str, dict[str, Any]]:
    """Return whether OOS metrics pass auto-deploy gates."""
    wf = best_result.get("walk_forward") or {}
    oos = wf.get("out_of_sample") or {}
    summary = best_result.get("summary") or {}

    oos_pnl = oos.get("total_pnl")
    if oos_pnl is None:
        oos_pnl = best_result.get("total_pnl")
    try:
        oos_pnl_f = float(oos_pnl or 0)
    except (TypeError, ValueError):
        oos_pnl_f = 0.0

    oos_trades = oos.get("total_trades")
    if oos_trades is None:
        oos_trades = summary.get("total_trades")
    if oos_trades is None:
        oos_trades = best_result.get("trade_count")
    try:
        oos_trades_i = int(oos_trades or 0)
    except (TypeError, ValueError):
        oos_trades_i = 0

    metrics = {
        "oos_pnl": round(oos_pnl_f, 4),
        "oos_trades": oos_trades_i,
        "mean_oos_objective": (wf.get("aggregate") or {}).get("mean_oos_objective"),
    }

    if oos_trades_i < max(0, int(min_oos_trades)):
        return False, f"OOS trades {oos_trades_i} below minimum {min_oos_trades}", metrics
    if oos_pnl_f < float(min_oos_pnl):
        return False, f"OOS PnL {oos_pnl_f:.2f} below minimum {min_oos_pnl:.2f}", metrics
    return True, "OK", metrics


async def auto_deploy_from_walk_forward(
    bot_manager,
    best_result: dict,
    *,
    symbol: str,
    strategy: str,
    timeframe: str,
    allocation: float,
    run_id: str | None = None,
    min_oos_pnl: float = 0.0,
    min_oos_trades: int = 1,
    skip_existing: bool = True,
    base_config: dict | None = None,
) -> dict[str, Any]:
    """Deploy bot when walk-forward OOS validation passes."""
    ok, reason, metrics = validate_walk_forward_oos(
        best_result,
        min_oos_pnl=min_oos_pnl,
        min_oos_trades=min_oos_trades,
    )
    if not ok:
        return {"deployed": False, "reason": reason, "metrics": metrics}

    sym = str(symbol or "").upper()
    if skip_existing and sym in active_bot_symbols(bot_manager, strategy=strategy, timeframe=timeframe):
        return {
            "deployed": False,
            "reason": f"Bot already active for {sym} ({timeframe})",
            "metrics": metrics,
        }

    wf = best_result.get("walk_forward") or {}
    best_cfg = dict(wf.get("best_config") or best_result.get("sweep", {}).get("best_config") or {})
    merged = {**(base_config or {}), **best_cfg}
    merged["backtest_run_id"] = run_id
    merged["walk_forward_deploy"] = True
    merged.setdefault("regime_routing_enabled", True)
    merged["pipeline_source"] = "walk_forward"

    try:
        bot_id = await bot_manager.create_bot(
            strategy,
            sym,
            timeframe,
            float(allocation),
            merged,
        )
    except Exception as exc:
        return {"deployed": False, "reason": str(exc), "metrics": metrics}

    return {
        "deployed": True,
        "bot_id": bot_id,
        "symbol": sym,
        "config": merged,
        "metrics": metrics,
    }
