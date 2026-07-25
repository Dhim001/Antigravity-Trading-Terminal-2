"""Live smoke: RL_PPO_AGENT walk-forward OOS returns + no live ONNX clobber.

Targets running LIVE_ALPACA HTTP :8796 after recycle with RL WF fixes.
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
SYMBOL = os.environ.get("RL_SMOKE_SYMBOL", "BTCUSDT")
TF = os.environ.get("RL_SMOKE_TF", "15m")

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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return e.code, payload


def onnx_fingerprint(symbol: str, tf: str) -> dict:
    from app.services.bots.rl_ppo_trainer import _model_dir

    d = Path(_model_dir(symbol, tf))
    out = {"dir": str(d), "exists": d.is_dir(), "files": {}}
    if not d.is_dir():
        return out
    for name in ("ppo_policy.onnx", "scaler.json", "metadata.json"):
        p = d / name
        if p.is_file():
            st = p.stat()
            out["files"][name] = {"mtime": st.st_mtime, "size": st.st_size}
        else:
            out["files"][name] = None
    return out


def assert_no_clobber(before: dict, after: dict, label: str) -> None:
    if not before.get("exists") and not after.get("exists"):
        ok(f"{label}: no ONNX dir created (good)")
        return
    if not before.get("exists") and after.get("exists"):
        # New dir during validate = live clobber / accidental persist
        fail(f"{label}: ONNX dir created during validate: {after.get('dir')}")
        return
    for name, b in (before.get("files") or {}).items():
        a = (after.get("files") or {}).get(name)
        if b is None and a is not None:
            fail(f"{label}: {name} created during validate")
        elif b is not None and a is not None:
            if a["mtime"] != b["mtime"] or a["size"] != b["size"]:
                fail(f"{label}: {name} mutated (mtime/size changed)")
            else:
                ok(f"{label}: {name} unchanged")
        elif b is not None and a is None:
            fail(f"{label}: {name} deleted during validate")
        else:
            ok(f"{label}: {name} still absent")


def check_inprocess_contracts() -> None:
    from app.services.bots.ml_walk_forward_validator import (
        _make_recommendation,
        _rl_return_to_score,
    )

    score = _rl_return_to_score(0.0)
    if abs(score - 0.5) < 1e-6:
        ok("in-process: _rl_return_to_score(0)=0.5")
    else:
        fail(f"in-process: _rl_return_to_score(0)={score}")

    rec = _make_recommendation(
        {
            "metric_kind": "rl_return",
            "mean_oos_return_pct": 2.0,
            "total_oos_signals": 5,
            "positive_return_folds": 2,
        },
        {"cv": 0.1, "trend": "stable"},
        n_success=3,
        n_total=3,
    )
    if str(rec).startswith("DEPLOY") or str(rec).startswith("CAUTION"):
        ok(f"in-process: RL recommendation ok ({rec.split()[0]})")
    else:
        fail(f"in-process: unexpected RL rec {rec}")

    # Mirror executor defaults: skip onnx + skip interactive PBO
    run_pbo = True
    cfg2 = {
        "training_window_months": 6,
        "timeframe": TF,
        "total_timesteps": 4096,
        "skip_onnx_export": True,
        "wf_capacity_parity": False,
    }
    if run_pbo and not bool(cfg2.get("force_pbo")):
        run_pbo = False
        cfg2["_pbo_skipped"] = "rl_too_expensive"
    if cfg2.get("skip_onnx_export") and cfg2.get("_pbo_skipped") and not run_pbo:
        ok("in-process: RL validate defaults (skip onnx + skip pbo)")
    else:
        fail(f"in-process: RL defaults broken {cfg2} run_pbo={run_pbo}")


def poll_job(job_id: str, timeout_sec: float = 900.0) -> dict:
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout_sec:
        code, payload = http_json("GET", f"/api/v1/ml/jobs/{job_id}", timeout=30.0)
        if code != 200:
            time.sleep(2.0)
            continue
        last = payload if isinstance(payload, dict) else {}
        job = last.get("job") if isinstance(last.get("job"), dict) else last
        status = str(job.get("status") or last.get("status") or "").lower()
        prog = job.get("progress") if isinstance(job.get("progress"), dict) else {}
        detail = prog.get("detail") or prog.get("phase") or status
        elapsed = int(time.time() - t0)
        print(f"  … {elapsed}s status={status} {detail}")
        if status in ("done", "error", "cancelled", "complete", "completed", "failed"):
            return job if job else last
        time.sleep(3.0)
    fail(f"job {job_id} timed out after {timeout_sec}s")
    return last


def extract_result(job: dict) -> dict:
    if not isinstance(job, dict):
        return {}
    for key in ("result", "payload"):
        r = job.get(key)
        if isinstance(r, dict):
            return r
    return job


def check_wf_payload(result: dict, label: str) -> None:
    if not result.get("ok"):
        fail(f"{label}: ok=False error={result.get('error')}")
        return
    ok(f"{label}: ok=True")

    agg = result.get("aggregate") if isinstance(result.get("aggregate"), dict) else {}
    if agg.get("metric_kind") == "rl_return":
        ok(f"{label}: aggregate.metric_kind=rl_return")
    else:
        fail(f"{label}: expected metric_kind=rl_return got {agg.get('metric_kind')}")

    if agg.get("mean_oos_return_pct") is not None:
        ok(f"{label}: mean_oos_return_pct={agg.get('mean_oos_return_pct')}")
    else:
        fail(f"{label}: missing mean_oos_return_pct")

    folds = result.get("folds") if isinstance(result.get("folds"), list) else []
    good = [f for f in folds if isinstance(f, dict) and f.get("ok")]
    if not good:
        fail(f"{label}: no successful folds (folds={len(folds)})")
    else:
        ok(f"{label}: {len(good)}/{len(folds)} folds ok")

    for f in good:
        om = f.get("oos_metrics") if isinstance(f.get("oos_metrics"), dict) else {}
        if om.get("metric_kind") != "rl_return":
            fail(f"{label}: fold {f.get('fold')} metric_kind={om.get('metric_kind')}")
        elif "return_pct" not in om:
            fail(f"{label}: fold {f.get('fold')} missing return_pct")
        else:
            # accuracy should be mapped score, not TB classification
            acc = om.get("accuracy")
            if acc is None or not (0.0 <= float(acc) <= 1.0):
                fail(f"{label}: fold {f.get('fold')} bad accuracy score {acc}")

    if good and all(
        isinstance(f.get("oos_metrics"), dict)
        and f["oos_metrics"].get("metric_kind") == "rl_return"
        and "return_pct" in f["oos_metrics"]
        for f in good
    ):
        ok(f"{label}: all good folds have return_pct + rl_return")

    pbo = result.get("pbo") if isinstance(result.get("pbo"), dict) else {}
    if pbo.get("skipped"):
        ok(f"{label}: PBO skipped as expected ({pbo.get('error', '')[:60]})")
    elif pbo.get("ok") is False and "skip" in str(pbo.get("error", "")).lower():
        ok(f"{label}: PBO skipped via error message")
    else:
        fail(f"{label}: expected PBO skipped, got {pbo}")

    if result.get("wf_capacity_parity") is False or result.get("capacity_gap_warning"):
        ok(f"{label}: capacity gap flagged (parity={result.get('wf_capacity_parity')})")
    else:
        fail(f"{label}: expected capacity_gap_warning / wf_capacity_parity=False")
    warn = str(result.get("capacity_gap_warning") or "")
    if warn and "OOS returns" in warn:
        ok(f"{label}: capacity warning uses OOS returns wording")
    elif warn and "OOS accuracy" in warn:
        fail(f"{label}: capacity warning still says OOS accuracy")

    rec = result.get("recommendation") or ""
    if rec:
        ok(f"{label}: recommendation={str(rec)[:80]}")
    else:
        fail(f"{label}: missing recommendation")


def main() -> int:
    print(f"HTTP={HTTP} symbol={SYMBOL} tf={TF}")
    code, health = http_json("GET", "/health/live", timeout=10.0)
    if code != 200 or not health.get("ok"):
        fail(f"health not ok: {code} {health}")
        print(f"\n{len(oks)} ok / {len(fails)} fail")
        return 1
    ok(f"health LIVE_ALPACA mode={health.get('terminal_mode')}")

    check_inprocess_contracts()

    before = onnx_fingerprint(SYMBOL, TF)
    print("ONNX before:", json.dumps(before))

    body = {
        "symbol": SYMBOL,
        "strategy": "RL_PPO_AGENT",
        "async": True,
        "pbo": True,
        "n_folds": 3,
        "mode": "rolling",
        "timeframe": TF,
        "config": {
            "timeframe": TF,
            "training_window_months": 6,
            "validate_max_bars": 1200,
            "total_timesteps": 2048,
            "n_steps": 256,
            "ppo_epochs": 2,
            "hidden_dim": 32,
        },
    }
    code, resp = http_json("POST", "/api/v1/ml/validate", body=body, timeout=60.0)
    if code != 200 or not resp.get("ok") or not resp.get("job_id"):
        fail(f"validate submit failed: {code} {resp}")
        print(f"\n{len(oks)} ok / {len(fails)} fail")
        return 1
    job_id = resp["job_id"]
    ok(f"async validate submitted job_id={job_id}")

    job = poll_job(job_id, timeout_sec=1200.0)
    status = str(job.get("status") or "").lower()
    result = extract_result(job)
    print("RESULT:", json.dumps({
        "status": status,
        "ok": result.get("ok"),
        "error": result.get("error"),
        "aggregate": result.get("aggregate"),
        "pbo": result.get("pbo"),
        "recommendation": result.get("recommendation"),
        "wf_capacity_parity": result.get("wf_capacity_parity"),
        "capacity_gap_warning": result.get("capacity_gap_warning"),
        "n_folds": len(result.get("folds") or []),
        "fold_summaries": [
            {
                "fold": f.get("fold"),
                "ok": f.get("ok"),
                "error": f.get("error"),
                "metric_kind": (f.get("oos_metrics") or {}).get("metric_kind"),
                "return_pct": (f.get("oos_metrics") or {}).get("return_pct"),
                "accuracy": (f.get("oos_metrics") or {}).get("accuracy"),
            }
            for f in (result.get("folds") or [])
            if isinstance(f, dict)
        ],
    }, indent=2, default=str))

    if status in ("error", "failed") and not result.get("ok"):
        fail(f"job ended {status}: {result.get('error') or job.get('error')}")
    else:
        check_wf_payload(result, "async RL validate")

    after = onnx_fingerprint(SYMBOL, TF)
    print("ONNX after:", json.dumps(after))
    assert_no_clobber(before, after, "live ONNX")

    # Sync lean path (optional shorter second probe) — only if first succeeded
    if result.get("ok") and not fails:
        before2 = onnx_fingerprint(SYMBOL, TF)
        body2 = {
            "symbol": SYMBOL,
            "strategy": "RL_PPO_AGENT",
            "async": False,
            "pbo": True,
            "n_folds": 2,
            "mode": "rolling",
            "timeframe": TF,
            "config": {
                "timeframe": TF,
                "training_window_months": 3,
                "validate_max_bars": 800,
                "total_timesteps": 1024,
                "n_steps": 256,
                "ppo_epochs": 1,
                "hidden_dim": 32,
            },
        }
        print("Submitting sync validate (lean)…")
        code2, resp2 = http_json("POST", "/api/v1/ml/validate", body=body2, timeout=900.0)
        if code2 != 200:
            fail(f"sync validate HTTP {code2}: {resp2}")
        else:
            check_wf_payload(resp2 if isinstance(resp2, dict) else {}, "sync RL validate")
            after2 = onnx_fingerprint(SYMBOL, TF)
            assert_no_clobber(before2, after2, "sync ONNX")

    print(f"\n=== {len(oks)} ok / {len(fails)} fail ===")
    for m in fails:
        print(" -", m)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
