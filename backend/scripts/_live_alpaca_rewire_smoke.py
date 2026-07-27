"""Live smoke for LIVE_ALPACA rewire — run against recycled Alpaca backend env.

Usage (from backend/ with Alpaca profile env already loaded by the process):
  set TERMINAL_MODE=LIVE_ALPACA
  python scripts/_live_alpaca_rewire_smoke.py

Or invoke via:
  powershell -File scripts/start-backend.ps1 style env then this script in-process.

This script loads repo-root .env + env.profiles/alpaca.env then exercises
broker/news/correlation/bot candle paths without starting a second feed.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, val = raw.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        # Profile overrides root for TERMINAL_MODE / ports; secrets keep root if set.
        if key in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "MASSIVE_API_KEY", "FINNHUB_API_KEY"):
            if key not in os.environ or not os.environ.get(key):
                os.environ[key] = val
        else:
            os.environ[key] = val


def main() -> int:
    _load_env_file(ROOT / ".env")
    _load_env_file(ROOT / "env.profiles" / "alpaca.env")
    os.environ["TERMINAL_MODE"] = "LIVE_ALPACA"

    failures: list[str] = []
    oks: list[str] = []

    def ok(msg: str) -> None:
        oks.append(msg)
        print(f"OK  {msg}")

    def fail(msg: str) -> None:
        failures.append(msg)
        print(f"FAIL {msg}")

    # Re-import config after env
    import importlib
    import app.config as cfg

    importlib.reload(cfg)
    print(
        f"mode={cfg.TERMINAL_MODE} alpaca_key={bool(cfg.ALPACA_API_KEY)} "
        f"massive_key={bool(cfg.MASSIVE_API_KEY)} finnhub={bool(cfg.FINNHUB_API_KEY)}"
    )
    if cfg.TERMINAL_MODE != "LIVE_ALPACA":
        fail(f"TERMINAL_MODE is {cfg.TERMINAL_MODE}, expected LIVE_ALPACA")
        return 1
    if not cfg.ALPACA_API_KEY or not cfg.ALPACA_SECRET_KEY:
        fail("Alpaca keys missing")
        return 1

    # --- broker source dual-key ---
    import app.services.archive.broker_fetch as bf

    importlib.reload(bf)
    src = bf.resolve_broker_source()
    if src == "alpaca":
        ok(f"resolve_broker_source={src} (Massive key present={bool(cfg.MASSIVE_API_KEY)})")
    else:
        fail(f"resolve_broker_source={src} expected alpaca")

    if bf._prefer_massive_for_mode("alpaca"):
        fail("_prefer_massive_for_mode True on LIVE_ALPACA")
    else:
        ok("_prefer_massive_for_mode False")

    to_ts = int(time.time())
    from_ts = to_ts - 5 * 86400

    # Equity HT
    eq = bf.fetch_alpaca_tf_candles("AAPL", from_ts, to_ts, "1h")
    if len(eq) >= 20:
        ok(f"Alpaca AAPL 1h bars={len(eq)} first={eq[0]['time']} last={eq[-1]['time']}")
    else:
        fail(f"Alpaca AAPL 1h bars={len(eq)} (need >=20)")

    # Crypto HT
    cr = bf.fetch_alpaca_tf_candles("BTCUSDT", from_ts, to_ts, "15m")
    if len(cr) >= 50:
        ok(f"Alpaca BTCUSDT 15m bars={len(cr)}")
    else:
        fail(f"Alpaca BTCUSDT 15m bars={len(cr)} (need >=50)")

    # 1m equity via fetch_alpaca_1m_bars
    eq1 = bf.fetch_alpaca_1m_bars("AAPL", to_ts - 2 * 86400, to_ts)
    if len(eq1) >= 50:
        ok(f"Alpaca AAPL 1m db-rows={len(eq1)} source={eq1[0].get('source')}")
    else:
        fail(f"Alpaca AAPL 1m db-rows={len(eq1)}")

    # 1m crypto
    cr1 = bf.fetch_alpaca_1m_bars("BTCUSDT", to_ts - 86400, to_ts)
    if len(cr1) >= 50:
        ok(f"Alpaca BTCUSDT 1m db-rows={len(cr1)} source={cr1[0].get('source')}")
    else:
        fail(f"Alpaca BTCUSDT 1m db-rows={len(cr1)}")

    # iter pages must not call Massive
    massive_calls = {"n": 0}

    def _boom(*_a, **_k):
        massive_calls["n"] += 1
        raise AssertionError("Massive should not be called on LIVE_ALPACA")

    bf.iter_massive_tf_candle_pages = _boom  # type: ignore
    bf.fetch_massive_1m_bars = _boom  # type: ignore
    pages = list(bf.iter_broker_tf_candle_pages("AAPL", from_ts, to_ts, "15m"))
    if pages and len(pages[0]) >= 10 and massive_calls["n"] == 0:
        ok(f"iter_broker_tf_candle_pages AAPL 15m={len(pages[0])} (no Massive)")
    else:
        fail(
            f"iter_broker pages={len(pages)} bars={len(pages[0]) if pages else 0} "
            f"massive_calls={massive_calls['n']}"
        )

    # broker 1m chain must stay on Alpaca even if Massive would succeed
    rows_1m = bf.fetch_broker_1m_bars("AAPL", to_ts - 86400, to_ts)
    if rows_1m and rows_1m[0].get("source") == "ALPACA_REST" and massive_calls["n"] == 0:
        ok(f"fetch_broker_1m_bars source={rows_1m[0].get('source')} n={len(rows_1m)}")
    elif rows_1m:
        fail(f"fetch_broker_1m_bars unexpected source={rows_1m[0].get('source')}")
    else:
        fail("fetch_broker_1m_bars empty")

    # --- news sources ---
    from app.services.altdata import news_provider as np

    importlib.reload(np)
    sources = np.available_news_sources()
    if "alpaca_news" in sources and "news" not in sources and "yfinance_news" not in sources:
        ok(f"news sources={sources}")
    else:
        fail(f"news sources unexpected: {sources}")

    aapl_news = np.fetch_symbol_news("AAPL")
    src_set = {r.get("source") for r in aapl_news}
    if aapl_news and "alpaca_news" in src_set and "news" not in src_set and "yfinance_news" not in src_set:
        ok(f"fetch AAPL news n={len(aapl_news)} sources={sorted(src_set)}")
    else:
        fail(f"fetch AAPL news n={len(aapl_news)} sources={sorted(src_set)}")

    # --- sentiment ---
    from app.services.altdata import sentiment_provider as sp

    importlib.reload(sp)
    sent = sp.fetch_symbol_sentiment("AAPL")
    sent_src = {r.get("source") for r in sent}
    if "news" in sent_src or "yfinance_news" in sent_src:
        fail(f"sentiment leaked Massive/Yahoo sources={sorted(sent_src)}")
    else:
        ok(f"sentiment n={len(sent)} sources={sorted(sent_src)}")

    # --- correlation ---
    from app.services.bots import correlation as corr

    importlib.reload(corr)
    daily, src_c = corr.fetch_daily_closes("AAPL", 60)
    if src_c == "alpaca" and len(daily) >= 20:
        ok(f"correlation AAPL source={src_c} days={len(daily)}")
    else:
        fail(f"correlation AAPL source={src_c} days={len(daily)}")

    # --- benchmarks ---
    from app.services.analytics import benchmarks as bm

    importlib.reload(bm)
    series = bm.get_benchmark_series("SPY", period="1mo")
    if len(series) >= 10:
        ok(f"benchmark SPY points={len(series)}")
    else:
        fail(f"benchmark SPY points={len(series)}")

    # --- altdata provider selection ---
    from app.services.altdata import loop as alt_loop

    importlib.reload(alt_loop)
    # Don't full-refresh (slow); just confirm branch via source module
    if cfg.TERMINAL_MODE == "LIVE_ALPACA":
        from app.services.altdata import alpaca_provider as ap

        ok(f"altdata alpaca_provider importable refresh={hasattr(ap, 'refresh_altdata')}")

    # --- execution mode ---
    from app.services.bots import execution_mode as em

    importlib.reload(em)
    if em.runs_live_feed_bot_ticks() and em.is_live_alpaca():
        if cfg.ALPACA_OMS_ENABLED:
            if not em.uses_paper_oms() and em.execution_mode_label() == "broker":
                ok("execution_mode ticks=True paper=False label=broker")
            else:
                fail(
                    f"execution_mode ticks={em.runs_live_feed_bot_ticks()} "
                    f"paper={em.uses_paper_oms()} label={em.execution_mode_label()}"
                )
        else:
            if em.uses_paper_oms() and em.execution_mode_label() == "paper":
                ok("execution_mode ticks=True paper=True label=paper (ALPACA_OMS_ENABLED=false)")
            else:
                fail(
                    f"execution_mode ticks={em.runs_live_feed_bot_ticks()} "
                    f"paper={em.uses_paper_oms()} label={em.execution_mode_label()}"
                )
    elif em.runs_live_feed_bot_ticks() and not em.uses_paper_oms() and em.execution_mode_label() == "broker":
        ok("execution_mode ticks=True paper=False label=broker")
    else:
        fail(
            f"execution_mode ticks={em.runs_live_feed_bot_ticks()} "
            f"paper={em.uses_paper_oms()} label={em.execution_mode_label()}"
        )

    # --- HT limits used by feed ---
    from app.services.massive_ht_limits import massive_ht_limit

    chart_cap = massive_ht_limit("15m", purpose="chart")
    analysis_cap = massive_ht_limit("15m", purpose="analysis")
    if analysis_cap > chart_cap:
        ok(f"HT limits 15m chart={chart_cap} analysis={analysis_cap}")
    else:
        fail(f"HT limits unexpected chart={chart_cap} analysis={analysis_cap}")

    print()
    print(f"Passed {len(oks)}  Failed {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
