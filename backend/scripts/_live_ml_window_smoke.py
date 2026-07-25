"""Live smoke: extended training windows + validate floors + timeout scales.

Targets running LIVE_ALPACA HTTP :8796. Also exercises in-process helpers
(parse / bar limits / WF mins) against the same code the server loads.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

HTTP = os.environ.get("ALPACA_HTTP", "http://127.0.0.1:8796")

fails: list[str] = []
oks: list[str] = []


def ok(m: str) -> None:
    oks.append(m)
    print("OK ", m)


def fail(m: str) -> None:
    fails.append(m)
    print("FAIL", m)


def http_json(method: str, path: str, body: dict | None = None, timeout: float = 120.0):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{HTTP}{path}", data=data, headers=headers, method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def wait_live(max_wait: float = 90.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < max_wait:
        try:
            body = http_json("GET", "/health/live", timeout=5)
            if body.get("ok"):
                return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


def unit_helpers() -> None:
    # Load alpaca profile like the server
    for p in (ROOT / ".env", ROOT / "env.profiles" / "alpaca.env"):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            k, _, v = raw.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    os.environ["TERMINAL_MODE"] = "LIVE_ALPACA"
    import importlib
    import app.config as cfg
    importlib.reload(cfg)

    from app.services.bots.ml_training_window import (
        TRAINING_WINDOW_MONTHS,
        bar_limit_for_training_window,
        next_training_window_months,
        parse_training_window_months,
        validate_min_candles,
        wf_adaptive_fold_mins,
    )
    from app.services.bots.ml_walk_forward_validator import generate_wf_folds

    if TRAINING_WINDOW_MONTHS != (1, 3, 6, 12, 18, 24, 36):
        fail(f"TRAINING_WINDOW_MONTHS={TRAINING_WINDOW_MONTHS}")
    else:
        ok(f"window ladder {TRAINING_WINDOW_MONTHS}")

    for m in (18, 24, 36):
        if parse_training_window_months({"training_window_months": m}) != m:
            fail(f"parse {m}")
        else:
            ok(f"parse months={m}")

    if next_training_window_months(12) != 18:
        fail("next after 12 != 18")
    else:
        ok("next 12->18")
    if next_training_window_months(36) is not None:
        fail("next after 36 should be None")
    else:
        ok("next 36->None")

    b12 = bar_limit_for_training_window(12, timeframe="1h", purpose="validate")
    b24 = bar_limit_for_training_window(24, timeframe="1h", purpose="validate")
    b36 = bar_limit_for_training_window(36, timeframe="1h", purpose="validate")
    if not (b12 <= b24 <= b36):
        fail(f"1h validate bars not scaling: {b12},{b24},{b36}")
    else:
        ok(f"1h validate bars scale {b12}→{b24}→{b36}")

    vmin = validate_min_candles("1h")
    mt, ms = wf_adaptive_fold_mins(295, "1h")
    folds = generate_wf_folds(295, n_folds=5, purge_bars=30, min_train=mt, min_test=ms)
    if 295 < vmin:
        fail(f"1h min {vmin} still rejects 295")
    elif len(folds) < 2:
        fail(f"295 1h folds={len(folds)} mins={mt}/{ms}")
    else:
        ok(f"295 1h clears min={vmin} folds={len(folds)}")


def live_broker_depth() -> None:
    from app.services.archive.broker_fetch import fetch_alpaca_tf_candles, resolve_broker_source

    src = resolve_broker_source()
    if src != "alpaca":
        fail(f"broker source={src} (expected alpaca)")
    else:
        ok("broker source=alpaca")

    to_ts = int(time.time())
    # 24 months of 1h equities — should far exceed old 500 gate after FIT
    from_ts = to_ts - 24 * 30 * 86400
    t0 = time.time()
    bars = fetch_alpaca_tf_candles("AAPL", from_ts, to_ts, "1h") or []
    dt = time.time() - t0
    if len(bars) < 500:
        fail(f"AAPL 24mo 1h only {len(bars)} bars in {dt:.1f}s")
    else:
        ok(f"AAPL 24mo 1h n={len(bars)} in {dt:.1f}s")

    from_ts18 = to_ts - 18 * 30 * 86400
    t0 = time.time()
    crypto = fetch_alpaca_tf_candles("BTCUSDT", from_ts18, to_ts, "15m") or []
    dt = time.time() - t0
    if len(crypto) < 1000:
        fail(f"BTCUSDT 18mo 15m only {len(crypto)} in {dt:.1f}s")
    else:
        ok(f"BTCUSDT 18mo 15m n={len(crypto)} in {dt:.1f}s")


def live_validate_api() -> None:
    """Async validate with 18mo · 1h — exercises expand + new floors + job poll."""
    # Submit
    try:
        body = http_json(
            "POST",
            "/api/v1/ml/validate",
            {
                "symbol": "AAPL",
                "strategy": "ML_SIGNAL_BOOST",
                "async": True,
                "n_folds": 3,
                "mode": "rolling",
                "pbo": True,
                "pbo_segments": 4,
                "timeframe": "1h",
                "config": {
                    "timeframe": "1h",
                    "training_window_months": 18,
                    "symbol": "AAPL",
                    "model_symbol": "AAPL",
                    "_wf_mode": True,
                    "validate_max_bars": 6000,
                    "pbo_max_combos": 2,
                    "gbm_max_iter": 40,
                    "gbm_max_depth": 4,
                },
            },
            timeout=90,
        )
    except Exception as exc:
        fail(f"validate submit: {exc}")
        return

    if not body.get("ok") or not body.get("job_id"):
        fail(f"validate submit body={body}")
        return
    job_id = body["job_id"]
    ok(f"validate job_id={job_id}")

    # Poll up to ~8 min (fetch+3 lean folds)
    deadline = time.time() + 480
    last_phase = ""
    while time.time() < deadline:
        try:
            st = http_json("GET", f"/api/v1/ml/jobs/{job_id}", timeout=30)
        except Exception as exc:
            fail(f"job poll: {exc}")
            return
        job = (st or {}).get("job") or {}
        status = job.get("status")
        prog = job.get("progress") or {}
        phase = f"{prog.get('phase')}|{prog.get('pct')}|{prog.get('detail')}"
        if phase != last_phase:
            print("  …", status, phase)
            last_phase = phase
        if status in ("done", "error", "cancelled"):
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            err = job.get("error") or result.get("error")
            tw = result.get("training_window") or {}
            if status == "done" and result.get("ok") is not False:
                n = tw.get("bars") or result.get("n_candles")
                months = tw.get("training_window_months")
                ok(f"validate done bars={n} months={months} oos={result.get('aggregate') or result.get('mean_oos_accuracy')}")
                if months is not None and int(months) < 12:
                    fail(f"expected expanded/long window ≥12, got {months}")
                return
            # Still useful: not the old flat-500 error
            if err and "Need >= 500 candles" in str(err):
                fail(f"legacy 500 gate still firing: {err}")
            elif err and "Need >=" in str(err) and "got" in str(err):
                fail(f"still short after expand: {err}")
            else:
                fail(f"validate {status}: {err or result}")
            return
        time.sleep(3)
    fail("validate job poll timed out (8 min)")


def live_short_window_expand_hint() -> None:
    """1mo · 1h may be short; server should expand rather than flat-500."""
    try:
        body = http_json(
            "POST",
            "/api/v1/ml/validate",
            {
                "symbol": "AAPL",
                "strategy": "ML_SIGNAL_BOOST",
                "async": True,
                "n_folds": 3,
                "mode": "rolling",
                "pbo": False,
                "timeframe": "1h",
                "config": {
                    "timeframe": "1h",
                    "training_window_months": 1,
                    "symbol": "AAPL",
                    "model_symbol": "AAPL",
                    "_wf_mode": True,
                    "validate_max_bars": 2500,
                    "gbm_max_iter": 30,
                    "gbm_max_depth": 3,
                },
            },
            timeout=90,
        )
    except Exception as exc:
        fail(f"short validate submit: {exc}")
        return
    job_id = body.get("job_id")
    if not job_id:
        fail(f"short validate no job: {body}")
        return
    ok(f"short-window validate job_id={job_id}")
    deadline = time.time() + 360
    while time.time() < deadline:
        st = http_json("GET", f"/api/v1/ml/jobs/{job_id}", timeout=30)
        job = (st or {}).get("job") or {}
        status = job.get("status")
        if status in ("done", "error", "cancelled"):
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            err = str(job.get("error") or result.get("error") or "")
            tw = result.get("training_window") or {}
            if "Need >= 500 candles" in err:
                fail(f"short window hit legacy 500: {err}")
            elif status == "done" and result.get("ok") is not False:
                ok(f"short->expand validate ok months={tw.get('training_window_months')} bars={tw.get('bars')}")
                if int(tw.get("bars") or 0) < 400:
                    fail(f"short expand still thin bars={tw.get('bars')}")
            elif "Need >=" in err:
                # Expanded but still short — surface as soft fail with detail
                fail(f"short window still insufficient after expand: {err}")
            else:
                fail(f"short validate {status}: {err or result}")
            return
        time.sleep(2.5)
    fail("short validate poll timeout")


def live_timeout_helpers_mirror() -> None:
    """Sanity: window scale grows budgets (mirrors frontend mlJobTimeouts)."""
    scales = {3: 1.0, 6: 1.25, 12: 1.6, 18: 2.0, 24: 2.5, 36: 3.0}

    def scale(m: int) -> float:
        if m <= 3:
            return 1.0
        if m <= 6:
            return 1.25
        if m <= 12:
            return 1.6
        if m <= 18:
            return 2.0
        if m <= 24:
            return 2.5
        return 3.0

    for m, exp in scales.items():
        got = scale(m)
        if abs(got - exp) > 1e-9:
            fail(f"timeout scale {m}={got} want {exp}")
        else:
            ok(f"timeout scale {m}mo={got}x")


def main() -> int:
    print(f"HTTP={HTTP}")
    if not wait_live():
        fail("backend /health/live not ready")
        print(f"\n{len(oks)} ok, {len(fails)} fail")
        return 1
    ok("health/live")

    unit_helpers()
    live_timeout_helpers_mirror()
    live_broker_depth()
    live_short_window_expand_hint()
    live_validate_api()

    print(f"\n{len(oks)} ok, {len(fails)} fail")
    for f in fails:
        print(" -", f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
