"""On-disk ML model versioning helpers.

Layout (per strategy subdir + symbol[, timeframe])::

    data/{subdir}/{SYMBOL}/                 # "current" / latest for 1m (back-compat)
    data/{subdir}/{SYMBOL}__15M/            # HTF models (e.g. 15m execution)
      metadata.json
      *.onnx | model.joblib | scaler.json …
      versions/
        index.json                          # [{version_id, trained_at, …}]
        .current                            # active version_id (retrain replaces this)
        {version_id}/
          metadata.json
          …artifacts…

Identity contract
-----------------
- ``version_id`` — filesystem key under ``versions/`` (from ``version_id_from_iso``).
- ``trained_at`` — ISO timestamp for display / Lab pin; also accepted as ``model_version``.
- API may pass ``model_version``, ``version_id``, or ``trained_at``; resolve via
  ``find_version_entry`` (matches either id or ISO).

Training still writes to the current root first, then ``snapshot_current_version``
copies artifacts into ``versions/<id>/``. By default a retrain **replaces** the
active version in place (same ``version_id`` / Keep / rename) instead of
appending a new history row — set ``replace_current=False`` to append.
Inference loaders can resolve a pinned ``model_version`` (ISO ``trained_at`` or
``version_id``) via ``resolve_model_dir``.

Strategy → subdir / artifact maps live in ``ml_registry`` (re-exported here).
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
from datetime import datetime, timezone
from typing import Any

from app.config import BASE_DIR
from app.services.bots.ml_registry import MODEL_SUBDIRS, STRATEGY_ARTIFACTS

logger = logging.getLogger(__name__)

MAX_KEPT_VERSIONS = 10
_CURRENT_POINTER_NAME = ".current"

# Re-export for back-compat (prefer importing from ml_registry)
__all__ = [
    "MODEL_SUBDIRS",
    "STRATEGY_ARTIFACTS",
    "MAX_KEPT_VERSIONS",
]


def safe_symbol_key(symbol: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(symbol or "").upper())


def normalize_model_timeframe(tf: str | None) -> str:
    """Canonical bar timeframe for model storage / lookup (default ``1m``)."""
    raw = (tf or "1m").strip()
    if not raw or raw.lower() == "tick":
        return "1m"
    try:
        from app.services.market.timeframes import normalize_timeframe

        key = normalize_timeframe(raw)
        return "1m" if key == "tick" else key
    except Exception:
        return "1m"


def model_storage_key(symbol: str, timeframe: str | None = None) -> str:
    """Filesystem folder under ``data/<strategy_subdir>/``.

    ``1m`` keeps legacy ``ETHUSDT`` paths. Higher TFs use ``ETHUSDT__15M`` so
    Lab can train separate models per execution timeframe without clobbering.
    """
    sym = safe_symbol_key(symbol)
    tf = normalize_model_timeframe(timeframe)
    if tf == "1m":
        return sym
    return f"{sym}__{safe_symbol_key(tf)}"


def version_id_from_iso(trained_at: str | None) -> str:
    """Normalize ISO timestamp into a filesystem-safe version id.

    Fractional seconds are dropped (not concatenated) so::

        2026-08-04T06:55:56.292031Z → 20260804T065556Z

    Legacy mangled ids (e.g. ``20260804T0655562``) remain resolvable via
    ``find_version_entry`` matching on ``trained_at``.
    """
    raw = (trained_at or "").strip()
    if not raw:
        raw = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    raw = raw.replace("+00:00", "Z")
    # Drop fractional seconds before stripping punctuation
    if "." in raw:
        head, _, frac = raw.partition(".")
        # Keep trailing Z if present on the fractional part
        suffix = "Z" if frac.upper().endswith("Z") or head.upper().endswith("Z") else ""
        if head.upper().endswith("Z"):
            head = head[:-1]
        raw = f"{head}{suffix}" if suffix else head
    cleaned = raw.replace("-", "").replace(":", "")
    if "T" in cleaned:
        date, _, rest = cleaned.partition("T")
        alnum = "".join(c for c in rest if c.isalnum())
        # HHMMSS + optional Z
        if alnum.upper().endswith("Z"):
            time_part = alnum[:6] + "Z"
        else:
            time_part = alnum[:6]
            if raw.upper().endswith("Z"):
                time_part += "Z"
        return f"{date}T{time_part}" if time_part else date
    return "".join(c for c in cleaned if c.isalnum())[:24] or "unknown"


def model_root_for(
    strategy: str,
    symbol: str,
    timeframe: str | None = None,
) -> str | None:
    sub = MODEL_SUBDIRS.get(str(strategy or "").upper())
    if not sub:
        return None
    return os.path.join(BASE_DIR, "data", sub, model_storage_key(symbol, timeframe))


def versions_dir(model_root: str) -> str:
    return os.path.join(model_root, "versions")


def version_index_path(model_root: str) -> str:
    return os.path.join(versions_dir(model_root), "index.json")


def _current_pointer_path(model_root: str) -> str:
    return os.path.join(versions_dir(model_root), _CURRENT_POINTER_NAME)


def read_current_version_pointer(model_root: str | None) -> str | None:
    """Return the active version_id tracked for in-place retrain replace."""
    if not model_root:
        return None
    path = _current_pointer_path(model_root)
    if not os.path.isfile(path):
        return None
    try:
        raw = open(path, encoding="utf-8").read().strip()
        if not raw:
            return None
        if raw.startswith("{"):
            data = json.loads(raw)
            vid = str((data or {}).get("version_id") or "").strip()
            return vid or None
        return raw
    except Exception:
        return None


def write_current_version_pointer(model_root: str | None, version_id: str | None) -> None:
    """Persist which version_id Retrain should replace (also set on Activate)."""
    if not model_root or not version_id:
        return
    vroot = versions_dir(model_root)
    try:
        os.makedirs(vroot, exist_ok=True)
        with open(_current_pointer_path(model_root), "w", encoding="utf-8") as fh:
            json.dump({"version_id": str(version_id)}, fh)
    except OSError as exc:
        logger.debug("Failed to write current version pointer: %s", exc)


def json_safe_value(value: Any) -> Any:
    """Recursively replace non-finite floats so JSON dumps / Starlette never 500.

    PPO can emit ``best_mean_return=-inf`` when no episode completes; Python's
    ``json.dump`` accepts that, but HTTP ``JSONResponse`` does not.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_safe_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_value(v) for v in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return json_safe_value(item())
        except Exception:
            return str(value)
    return value


def _read_index(model_root: str) -> list[dict[str, Any]]:
    path = version_index_path(model_root)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return [json_safe_value(e) if isinstance(e, dict) else e for e in data]
    except Exception:
        return []


def _write_index(model_root: str, entries: list[dict[str, Any]]) -> None:
    os.makedirs(versions_dir(model_root), exist_ok=True)
    path = version_index_path(model_root)
    safe = json_safe_value(entries)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(safe, f, indent=2, allow_nan=False)


def list_model_versions(model_root: str | None) -> list[dict[str, Any]]:
    """Return version index entries (newest first), with is_current flag."""
    if not model_root or not os.path.isdir(model_root):
        return []
    entries = _read_index(model_root)
    # Fallback: discover directories if index missing
    vroot = versions_dir(model_root)
    if not entries and os.path.isdir(vroot):
        for name in sorted(os.listdir(vroot), reverse=True):
            if name == "index.json":
                continue
            vdir = os.path.join(vroot, name)
            if not os.path.isdir(vdir):
                continue
            meta_path = os.path.join(vdir, "metadata.json")
            trained_at = None
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path, encoding="utf-8") as f:
                        trained_at = json.load(f).get("trained_at")
                except Exception:
                    pass
            entries.append({
                "version_id": name,
                "trained_at": trained_at,
                "path": f"versions/{name}",
            })
        if entries:
            _write_index(model_root, entries)

    current_meta = os.path.join(model_root, "metadata.json")
    current_at = None
    current_vid = None
    if os.path.isfile(current_meta):
        try:
            with open(current_meta, encoding="utf-8") as f:
                live = json.load(f)
            if isinstance(live, dict):
                current_at = live.get("trained_at")
                current_vid = str(live.get("version_id") or "").strip() or None
        except Exception:
            pass
    if not current_vid:
        current_vid = read_current_version_pointer(model_root)

    out = []
    for e in entries:
        row = dict(e)
        eid = str(row.get("version_id") or "")
        row["is_current"] = bool(
            (current_vid and eid == current_vid)
            or (
                current_at
                and row.get("trained_at")
                and str(row["trained_at"]) == str(current_at)
            )
        )
        out.append(row)
    out.sort(key=lambda r: str(r.get("trained_at") or r.get("version_id") or ""), reverse=True)
    return out


def resolve_model_dir(model_root: str, model_version: str | None = None) -> str:
    """Return directory to load artifacts from (versioned or current)."""
    if not model_root:
        return model_root
    if not model_version:
        return model_root
    vid = version_id_from_iso(str(model_version))
    # Exact dir (new clean ids)
    candidate = os.path.join(versions_dir(model_root), vid)
    if os.path.isdir(candidate):
        return candidate
    # Legacy mangled id folders (fractional digit glued on, e.g. …T0655562)
    legacy = _legacy_version_dir(model_root, vid, str(model_version))
    if legacy:
        return legacy
    # Match by trained_at in index
    for entry in list_model_versions(model_root):
        if str(entry.get("trained_at")) == str(model_version) or entry.get("version_id") == vid:
            path = os.path.join(model_root, entry.get("path") or f"versions/{entry['version_id']}")
            if os.path.isdir(path):
                return path
    # Fallback: current
    logger.warning(
        "Pinned model_version %s not found under %s — using current",
        model_version,
        model_root,
    )
    return model_root


def _legacy_version_dir(model_root: str, vid: str, needle: str) -> str | None:
    """Find versions/<id> for clean vid or legacy mangled folder names.

    Prefer exact matches; only use prefix when a single candidate exists so
    coexisting ``…T065556Z`` and ``…T0655562`` do not resolve ambiguously.
    """
    vroot = versions_dir(model_root)
    if not os.path.isdir(vroot):
        return None
    for key in (needle, vid):
        if not key:
            continue
        direct = os.path.join(vroot, key)
        if os.path.isdir(direct):
            return direct
    stem = vid.rstrip("Zz")
    if len(stem) < 15:  # YYYYMMDDTHHMMSS
        return None
    matches: list[str] = []
    for name in os.listdir(vroot):
        path = os.path.join(vroot, name)
        if name == "index.json" or not os.path.isdir(path):
            continue
        if name == vid or name.startswith(stem):
            matches.append(path)
    if len(matches) == 1:
        return matches[0]
    return None


def find_version_entry(model_root: str, model_version: str | None) -> dict[str, Any] | None:
    """Locate a version index row by trained_at or version_id (incl. legacy ids)."""
    if not model_root or not model_version:
        return None
    needle = str(model_version).strip()
    vid = version_id_from_iso(needle)
    stem = vid.rstrip("Zz") if vid else ""
    prefix_hits: list[dict[str, Any]] = []
    for entry in list_model_versions(model_root):
        eid = str(entry.get("version_id") or "")
        if str(entry.get("trained_at") or "") == needle or eid in (needle, vid):
            return entry
        # Pins taken before an in-place retrain still resolve via first_trained_at.
        if str(entry.get("first_trained_at") or "") == needle:
            return entry
        # Legacy: index still has mangled id, pin uses clean id — only if unique
        if stem and len(stem) >= 15 and eid.startswith(stem):
            prefix_hits.append(entry)
    if len(prefix_hits) == 1:
        return prefix_hits[0]
    legacy = _legacy_version_dir(model_root, vid, needle)
    if legacy:
        name = os.path.basename(legacy)
        meta_path = os.path.join(legacy, "metadata.json")
        trained_at = None
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    trained_at = json.load(f).get("trained_at")
            except Exception:
                pass
        return {
            "version_id": name,
            "trained_at": trained_at or needle,
            "path": f"versions/{name}",
        }
    return None


def champion_sync_info(model_root: str | None) -> dict[str, Any]:
    """Compare live root version vs newest index entry (no auto-activate)."""
    empty = {
        "root_version_id": None,
        "root_trained_at": None,
        "newest_version_id": None,
        "newest_trained_at": None,
        "desynced": False,
    }
    if not model_root or not os.path.isdir(model_root):
        return empty
    root_vid = None
    root_at = None
    meta_path = os.path.join(model_root, "metadata.json")
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            root_at = meta.get("trained_at")
            root_vid = meta.get("version_id") or (
                version_id_from_iso(str(root_at)) if root_at else None
            )
        except Exception:
            pass
    versions = list_model_versions(model_root)
    newest = versions[0] if versions else None
    newest_vid = newest.get("version_id") if newest else None
    newest_at = newest.get("trained_at") if newest else None
    desynced = False
    if root_at and newest_at and str(root_at) != str(newest_at):
        desynced = True
    elif root_vid and newest_vid and str(root_vid) != str(newest_vid):
        # Same trained_at edge case unlikely; still flag id mismatch
        if not root_at or not newest_at:
            desynced = True
    return {
        "root_version_id": root_vid,
        "root_trained_at": root_at,
        "newest_version_id": newest_vid,
        "newest_trained_at": newest_at,
        "desynced": desynced,
    }


def migrate_bare_base_model_dirs(
    *,
    data_root: str | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Archive bare crypto bases (e.g. BTC/) when BTCUSDT/ already exists.

    Moves ``data/{subdir}/BTC`` → ``data/_orphans/{subdir}/BTC`` so Lab only
    sees the normalized USDT tree. Returns a list of move records.
    """
    from app.services.bots.ml_registry import MODEL_SUBDIRS

    base = data_root or os.path.join(BASE_DIR, "data")
    moved: list[dict[str, Any]] = []
    # Common bare bases that normalize to *USDT for Lab
    bare_to_usdt = {
        "BTC": "BTCUSDT",
        "ETH": "ETHUSDT",
        "BNB": "BNBUSDT",
        "SOL": "SOLUSDT",
        "XRP": "XRPUSDT",
        "ADA": "ADAUSDT",
        "DOGE": "DOGEUSDT",
        "AVAX": "AVAXUSDT",
        "BCH": "BCHUSDT",
    }
    for subdir in sorted(set(MODEL_SUBDIRS.values())):
        sub_path = os.path.join(base, subdir)
        if not os.path.isdir(sub_path):
            continue
        for bare, usdt in bare_to_usdt.items():
            bare_path = os.path.join(sub_path, bare)
            usdt_path = os.path.join(sub_path, usdt)
            if not os.path.isdir(bare_path):
                continue
            if not os.path.isdir(usdt_path):
                # Prefer renaming bare → USDT when target missing
                dest = usdt_path
                action = "rename_to_usdt"
            else:
                dest = os.path.join(base, "_orphans", subdir, bare)
                action = "archive_orphan"
            rec = {
                "subdir": subdir,
                "bare": bare,
                "usdt": usdt,
                "src": bare_path,
                "dest": dest,
                "action": action,
            }
            if dry_run:
                moved.append(rec)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.exists(dest):
                # Avoid clobber — suffix with timestamp
                dest = f"{dest}__{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
                rec["dest"] = dest
            shutil.move(bare_path, dest)
            logger.info("Migrated bare model dir %s → %s (%s)", bare_path, dest, action)
            moved.append(rec)
    return moved


def ensure_validation_sidecar(model_root: str | None) -> bool:
    """Write validation.json from embedded metadata fields when sidecar missing.

    Always stamps ``trained_at`` / ``version_id`` so Activate / retrain cannot
    leak an older WF/PBO stamp onto a different champion (see
    ``read_validation_sidecar`` fingerprint checks).
    """
    if not model_root or not os.path.isdir(model_root):
        return False
    side = os.path.join(model_root, "validation.json")
    if os.path.isfile(side):
        return False
    meta_path = os.path.join(model_root, "metadata.json")
    if not os.path.isfile(meta_path):
        return False
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        return False
    if not isinstance(meta, dict):
        return False
    has_wf = isinstance(meta.get("walk_forward"), dict) and bool(meta.get("walk_forward"))
    has_pbo = isinstance(meta.get("pbo"), dict) and bool(meta.get("pbo"))
    if not has_wf and not has_pbo and not meta.get("validated_at"):
        return False
    trained_at = meta.get("trained_at")
    version_id = meta.get("version_id") or (
        version_id_from_iso(str(trained_at)) if trained_at else None
    )
    payload = {
        "validated_at": meta.get("validated_at"),
        "walk_forward": meta.get("walk_forward"),
        "pbo": meta.get("pbo"),
        "validation_fingerprint": meta.get("validation_fingerprint"),
        "trained_at": trained_at,
        "version_id": version_id,
    }
    try:
        with open(side, "w", encoding="utf-8") as f:
            json.dump(json_safe_value(payload), f, indent=2, allow_nan=False)
        return True
    except Exception:
        logger.exception("Failed to write validation sidecar under %s", model_root)
        return False


def activate_model_version(
    strategy: str,
    symbol: str,
    model_version: str,
    timeframe: str | None = None,
) -> dict[str, Any]:
    """
    Promote a historical snapshot to the live model root (current).

    Copies artifacts from ``versions/<id>/`` over the current root so unpinned
    bots / backtests load that checkpoint. Does not delete other versions.
    """
    strat = str(strategy or "").upper()
    sym = str(symbol or "").upper()
    root = model_root_for(strat, sym, timeframe)
    if not root or not os.path.isdir(root):
        return {"ok": False, "error": f"No model directory for {strat}/{sym}"}

    entry = find_version_entry(root, model_version)
    if not entry:
        return {"ok": False, "error": f"Version not found: {model_version}"}

    src_dir = os.path.join(root, entry.get("path") or f"versions/{entry['version_id']}")
    if not os.path.isdir(src_dir):
        return {"ok": False, "error": f"Version directory missing: {entry.get('version_id')}"}

    copied: list[str] = []
    for fname in os.listdir(src_dir):
        src = os.path.join(src_dir, fname)
        if not os.path.isfile(src):
            continue
        if fname == "versions":
            continue
        dst = os.path.join(root, fname)
        shutil.copy2(src, dst)
        copied.append(fname)

    if "metadata.json" not in copied:
        return {"ok": False, "error": "Version has no metadata.json"}

    # Ensure current metadata reflects this version id / trained_at so UI
    # is_current matching (trained_at equality) updates after Activate.
    meta_path = os.path.join(root, "metadata.json")
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    meta["version_id"] = entry.get("version_id")
    if entry.get("trained_at"):
        meta["trained_at"] = entry["trained_at"]
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    write_current_version_pointer(root, entry.get("version_id"))

    # Invalidate root validation.json — it may stamp a previous champion.
    # Embedded WF/PBO on the activated snapshot (if any) can be re-materialized
    # by ensure_validation_sidecar on the next status read.
    try:
        side = validation_sidecar_path(root)
        if os.path.isfile(side):
            os.remove(side)
    except Exception:
        logger.debug("Failed to clear validation sidecar on activate", exc_info=True)

    # Keep index entry; refresh is_current via list_model_versions
    logger.info(
        "Activated model version %s for %s/%s (%d files)",
        entry.get("version_id"),
        strat,
        sym,
        len(copied),
    )
    return {
        "ok": True,
        "strategy": strat,
        "symbol": sym,
        "version_id": entry.get("version_id"),
        "trained_at": meta.get("trained_at") or entry.get("trained_at"),
        "model_version": meta.get("trained_at") or entry.get("trained_at"),
        "artifacts": copied,
        "versions": list_model_versions(root),
    }


def delete_model_version(
    strategy: str,
    symbol: str,
    model_version: str,
    timeframe: str | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """
    Remove a historical snapshot from disk and the version index.

    Refuses to delete the currently active version — activate another checkpoint
    first. Never deletes the live model root files. Protected favorites also
    refuse delete unless ``force=True``.
    """
    strat = str(strategy or "").upper()
    sym = str(symbol or "").upper()
    root = model_root_for(strat, sym, timeframe)
    if not root or not os.path.isdir(root):
        return {"ok": False, "error": f"No model directory for {strat}/{sym}"}

    entry = find_version_entry(root, model_version)
    if not entry:
        return {"ok": False, "error": f"Version not found: {model_version}"}

    if entry.get("protected") and not force:
        return {
            "ok": False,
            "error": (
                "Cannot delete a protected (favorite) version. "
                "Unpin/unprotect it first, then delete."
            ),
        }

    vid = str(entry.get("version_id") or "")
    for row in list_model_versions(root):
        if row.get("is_current") and (
            str(row.get("version_id") or "") == vid
            or str(row.get("trained_at") or "") == str(entry.get("trained_at") or "")
        ):
            return {
                "ok": False,
                "error": (
                    "Cannot delete the active version. "
                    "Activate another version first, then delete this one."
                ),
            }

    rel = entry.get("path") or (f"versions/{vid}" if vid else None)
    if not rel:
        return {"ok": False, "error": "Version path missing"}
    vdir = os.path.join(root, rel)
    # Safety: only delete under versions/
    versions_root = os.path.realpath(versions_dir(root))
    real_vdir = os.path.realpath(vdir)
    if not real_vdir.startswith(versions_root + os.sep) and real_vdir != versions_root:
        return {"ok": False, "error": "Refusing to delete path outside versions/"}

    if os.path.isdir(vdir):
        shutil.rmtree(vdir)
    elif not os.path.exists(vdir):
        # Still drop stale index row
        pass
    else:
        return {"ok": False, "error": f"Not a version directory: {vid}"}

    entries = _read_index(root)
    trained_at = entry.get("trained_at")
    entries = [
        e
        for e in entries
        if str(e.get("version_id") or "") != vid
        and (not trained_at or str(e.get("trained_at") or "") != str(trained_at))
    ]
    _write_index(root, entries)

    logger.info("Deleted model version %s for %s/%s", vid, strat, sym)
    return {
        "ok": True,
        "strategy": strat,
        "symbol": sym,
        "deleted_version_id": vid,
        "deleted_trained_at": trained_at,
        "versions": list_model_versions(root),
    }


def invalidate_strategy_model_caches(strategy: str, symbol: str | None = None) -> None:
    """Drop in-memory ONNX/joblib caches after activate/retrain."""
    strat = str(strategy or "").upper()
    sym = (symbol or "").upper() or None
    try:
        if strat == "ML_SIGNAL_BOOST":
            from app.services.bots.strategies_ml import get_ml_signal_store
            get_ml_signal_store().invalidate(sym)
        elif strat == "LSTM_DIRECTION":
            from app.services.bots.strategies_lstm import get_lstm_store
            get_lstm_store().invalidate(sym)
        elif strat == "RL_PPO_AGENT":
            from app.services.bots.rl_ppo_trainer import get_ppo_store
            get_ppo_store().invalidate(sym)
        elif strat == "TCN_MULTI_HORIZON":
            from app.services.bots.ml_tcn_trainer import get_tcn_store
            get_tcn_store().invalidate(sym)
        elif strat == "VAE_REGIME_DETECTOR":
            from app.services.bots.ml_vae_regime import get_vae_store
            get_vae_store().invalidate(sym)
        elif strat == "TRANSFORMER_SIGNAL":
            from app.services.bots.ml_transformer_trainer import get_transformer_store
            get_transformer_store().invalidate(sym)
        elif strat == "GNN_CROSS_ASSET":
            from app.services.bots.ml_gnn_trainer import get_gnn_store
            get_gnn_store().invalidate(sym)
    except Exception as exc:
        logger.warning("Failed to invalidate model cache for %s/%s: %s", strat, sym, exc)


def snapshot_current_version(
    model_root: str,
    *,
    strategy: str | None = None,
    artifact_names: list[str] | None = None,
    max_kept: int = MAX_KEPT_VERSIONS,
    replace_current: bool = True,
    replace_version_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Copy current-root artifacts into versions/<id>/ and update index.json.

    Call after training has written metadata.json (+ model files) to model_root.

    By default (``replace_current=True``) a retrain overwrites the active
    version folder / index row in place — same ``version_id``, Keep star, and
    display name — instead of appending a duplicate history entry. Pass
    ``replace_current=False`` to append a new timestamped checkpoint (tests /
    explicit history capture).
    """
    if not model_root or not os.path.isdir(model_root):
        return None
    meta_path = os.path.join(model_root, "metadata.json")
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        return None

    trained_at = meta.get("trained_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    prev_index = _read_index(model_root)

    reuse_vid: str | None = None
    if replace_version_id:
        reuse_vid = str(replace_version_id).strip() or None
    elif replace_current:
        reuse_vid = str(meta.get("version_id") or "").strip() or None
        if not reuse_vid:
            reuse_vid = read_current_version_pointer(model_root)
        if not reuse_vid and prev_index:
            # Newest-first index — first row is the previous champion snapshot.
            # (Activate updates the .current pointer so Activate→Retrain hits
            # the promoted row, not merely the newest timestamp.)
            reuse_vid = str(prev_index[0].get("version_id") or "").strip() or None
        # Only reuse if that folder (or index row) actually exists.
        if reuse_vid:
            known = {str(e.get("version_id") or "") for e in prev_index}
            vdir_exists = os.path.isdir(os.path.join(versions_dir(model_root), reuse_vid))
            if reuse_vid not in known and not vdir_exists:
                reuse_vid = None

    vid = reuse_vid or version_id_from_iso(trained_at)
    vdir = os.path.join(versions_dir(model_root), vid)
    os.makedirs(vdir, exist_ok=True)

    names = list(artifact_names or [])
    if not names and strategy:
        names = list(STRATEGY_ARTIFACTS.get(str(strategy).upper()) or ["metadata.json"])
    if "metadata.json" not in names:
        names.append("metadata.json")

    # Also pick up any loose scaler/joblib/onnx/pt checkpoint in root if not listed
    for fname in os.listdir(model_root):
        if fname in ("versions",):
            continue
        fpath = os.path.join(model_root, fname)
        if os.path.isfile(fpath) and (
            fname.endswith((".onnx", ".joblib", ".json", ".pt")) or fname in names
        ):
            if fname not in names:
                names.append(fname)

    copied = []
    for fname in names:
        src = os.path.join(model_root, fname)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(vdir, fname)
        shutil.copy2(src, dst)
        copied.append(fname)

    # Enrich version metadata copy
    version_meta = dict(meta)
    version_meta["version_id"] = vid
    version_meta["version_path"] = f"versions/{vid}"
    with open(os.path.join(vdir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(json_safe_value(version_meta), f, indent=2, allow_nan=False)

    entry = {
        "version_id": vid,
        "trained_at": trained_at,
        "path": f"versions/{vid}",
        "artifacts": copied,
        "metrics": meta.get("metrics") if isinstance(meta.get("metrics"), dict) else {},
        "sample_count": meta.get("sample_count"),
        "label_distribution": meta.get("label_distribution"),
        "feature_names": meta.get("feature_names"),
        "model_type": meta.get("model_type"),
    }

    prev_same = next((e for e in prev_index if e.get("version_id") == vid), None)
    if isinstance(prev_same, dict):
        # Preserve user labels / favorites across in-place retrains.
        if prev_same.get("display_name"):
            entry["display_name"] = str(prev_same["display_name"])[:80]
        if prev_same.get("protected"):
            entry["protected"] = True
        # Keep the original train time for pin resilience; surface latest as trained_at.
        first_at = prev_same.get("first_trained_at") or prev_same.get("trained_at")
        if first_at:
            entry["first_trained_at"] = first_at
        if reuse_vid and str(prev_same.get("trained_at") or "") != str(trained_at):
            entry["retrained_at"] = trained_at

    index = [e for e in prev_index if e.get("version_id") != vid]
    index.insert(0, json_safe_value(entry))

    # Prune oldest unprotected versions only — favorites are retained.
    while len(index) > max_kept:
        prune_at = None
        for i in range(len(index) - 1, -1, -1):
            if not index[i].get("protected"):
                prune_at = i
                break
        if prune_at is None:
            break
        old = index.pop(prune_at)
        old_dir = os.path.join(model_root, old.get("path") or f"versions/{old.get('version_id')}")
        if os.path.isdir(old_dir):
            try:
                shutil.rmtree(old_dir)
            except OSError as exc:
                logger.warning("Failed to prune model version %s: %s", old_dir, exc)

    _write_index(model_root, index)
    write_current_version_pointer(model_root, vid)

    # Stamp current metadata with version_id
    meta["version_id"] = vid
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    logger.info(
        "Snapshotted model version %s under %s (%d files%s)",
        vid,
        model_root,
        len(copied),
        ", replaced in place" if reuse_vid else "",
    )
    return entry


VALIDATION_SIDECAR = "validation.json"


def validation_sidecar_path(model_root: str) -> str:
    return os.path.join(model_root, VALIDATION_SIDECAR)


def clear_ml_validation_stamp(model_root: str) -> None:
    """Drop deploy-gate validation after a fresh train (new artifact fingerprint)."""
    if not model_root:
        return
    unlink_quiet(validation_sidecar_path(model_root))
    meta_path = os.path.join(model_root, "metadata.json")
    if not os.path.isfile(meta_path):
        return
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, dict):
            return
        changed = False
        for key in ("validated_at", "walk_forward", "pbo", "pbo_audit"):
            if key in meta:
                meta.pop(key, None)
                changed = True
        if changed:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, default=str)
    except Exception:
        logger.debug("clear_ml_validation_stamp failed for %s", model_root, exc_info=True)


def read_validation_sidecar(model_root: str) -> dict[str, Any] | None:
    """Load validation.json if present and fingerprint matches live trained_at."""
    if not model_root:
        return None
    path = validation_sidecar_path(model_root)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return None
    except Exception:
        return None

    meta_path = os.path.join(model_root, "metadata.json")
    trained_at = None
    version_id = None
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            if isinstance(meta, dict):
                trained_at = meta.get("trained_at")
                version_id = meta.get("version_id")
        except Exception:
            pass

    # Sidecar must match the live champion artifact — Activate / retrain invalidate it.
    if payload.get("trained_at") and trained_at and payload.get("trained_at") != trained_at:
        return None
    if payload.get("version_id") and version_id and payload.get("version_id") != version_id:
        return None
    return payload


def apply_validation_sidecar(meta: dict[str, Any] | None, model_root: str | None) -> dict[str, Any]:
    """Merge sidecar WF/PBO into metadata dict for deploy-gate / model-status."""
    out = dict(meta) if isinstance(meta, dict) else {}
    side = read_validation_sidecar(model_root or "")
    if not side:
        return out
    if side.get("validated_at"):
        out["validated_at"] = side.get("validated_at")
    if isinstance(side.get("walk_forward"), dict):
        out["walk_forward"] = side["walk_forward"]
    if "pbo" in side:
        out["pbo"] = side.get("pbo")
    if isinstance(side.get("pbo_audit"), dict):
        out["pbo_audit"] = side["pbo_audit"]
    return out


def persist_ml_validation_metadata(
    strategy: str,
    symbol: str,
    wf_result: dict[str, Any] | None,
    pbo_result: dict[str, Any] | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    """Merge walk-forward (+ optional PBO) into live metadata + validation.json.

    Deploy gate reads ``validated_at`` / ``walk_forward`` / ``pbo``. The sidecar
    survives Activate restoring a version snapshot that lacks those keys, as long
    as ``trained_at`` / ``version_id`` still match the validated champion.
    """
    root = model_root_for(strategy, symbol, timeframe)
    if not root:
        return {"ok": False, "error": f"unknown strategy {strategy}"}
    meta_path = os.path.join(root, "metadata.json")
    if not os.path.isfile(meta_path):
        return {"ok": False, "error": "No trained model metadata to update"}

    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, dict):
            meta = {}
    except Exception as exc:
        return {"ok": False, "error": f"Failed to read metadata: {exc}"}

    wf = wf_result if isinstance(wf_result, dict) else {}

    # Reject stamping when champion FIT ends after the WF candle window
    # (retrain race / clock skew) — WF would not validate the live weights.
    try:
        from app.services.bots.ml_data_calendar import load_data_calendar_from_metadata

        cal = load_data_calendar_from_metadata(meta)
        fit_end = int((cal or {}).get("fit_end_ts") or meta.get("fit_end_ts") or 0)
        wf_to = wf.get("to_ts") or wf.get("candle_to_ts")
        tw = wf.get("training_window") if isinstance(wf.get("training_window"), dict) else {}
        if wf_to is None and tw:
            wf_to = tw.get("to_ts")
        if fit_end and wf_to:
            if int(fit_end) > int(wf_to) + 3600:
                return {
                    "ok": False,
                    "error": (
                        "validation window ends before champion fit_end_ts — "
                        "re-run Validate on the current FIT slice"
                    ),
                    "fit_end_ts": fit_end,
                    "wf_to_ts": int(wf_to),
                }
    except Exception:
        pass

    validated_at = wf.get("validated_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    agg = wf.get("aggregate") if isinstance(wf.get("aggregate"), dict) else {}
    walk_forward = {
        "ok": bool(wf.get("ok")),
        "mode": wf.get("mode"),
        "n_folds": wf.get("n_folds"),
        "successful_folds": wf.get("successful_folds"),
        "mean_oos_accuracy": agg.get("mean_oos_accuracy"),
        "mean_oos_return_pct": agg.get("mean_oos_return_pct"),
        "metric_kind": agg.get("metric_kind"),
        "recommendation": wf.get("recommendation"),
        "validated_at": validated_at,
        "stability": wf.get("stability") if isinstance(wf.get("stability"), dict) else None,
        "capacity_gap_warning": wf.get("capacity_gap_warning"),
        "wf_capacity_parity": wf.get("wf_capacity_parity"),
    }
    if meta.get("fit_end_ts"):
        walk_forward["fit_end_ts"] = meta.get("fit_end_ts")
    meta["validated_at"] = validated_at
    meta["walk_forward"] = walk_forward

    pbo = pbo_result if isinstance(pbo_result, dict) else None
    pbo_audit: dict[str, Any] | None = None
    if pbo and pbo.get("ok") and pbo.get("pbo") is not None:
        try:
            meta["pbo"] = float(pbo["pbo"])
        except (TypeError, ValueError):
            meta["pbo"] = pbo.get("pbo")
        pbo_audit = {
            "pbo": meta.get("pbo"),
            "recommendation": pbo.get("recommendation"),
            "degradation": pbo.get("degradation"),
            "n_combos": pbo.get("n_combos"),
            "skipped": bool(pbo.get("skipped")),
        }
        meta["pbo_audit"] = pbo_audit
    elif pbo is not None:
        # Skip / fail must clear stale champion PBO so deploy gate doesn't
        # reuse the previous model's score against this validation.
        meta["pbo"] = None
        pbo_audit = {
            "skipped": bool(pbo.get("skipped")),
            "ok": False,
            "error": pbo.get("error"),
            "recommendation": pbo.get("recommendation"),
        }
        meta["pbo_audit"] = pbo_audit

    sidecar = {
        "trained_at": meta.get("trained_at"),
        "version_id": meta.get("version_id"),
        "validated_at": validated_at,
        "walk_forward": walk_forward,
        "pbo": meta.get("pbo"),
        "pbo_audit": pbo_audit,
        "strategy": str(strategy or "").upper(),
        "symbol": str(symbol or "").upper(),
        "timeframe": normalize_model_timeframe(timeframe),
    }

    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, default=str)
    except Exception as exc:
        return {"ok": False, "error": f"Failed to write metadata: {exc}"}

    side_path = validation_sidecar_path(root)
    try:
        with open(side_path, "w", encoding="utf-8") as f:
            json.dump(sidecar, f, indent=2, default=str)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Failed to write validation sidecar: {exc}",
            "path": meta_path,
        }

    # Keep version snapshot in sync so Activate does not resurrect a stamp-less meta.
    vid = meta.get("version_id")
    if vid:
        vmeta_path = os.path.join(root, "versions", str(vid), "metadata.json")
        if os.path.isfile(vmeta_path):
            try:
                with open(vmeta_path, encoding="utf-8") as f:
                    vmeta = json.load(f)
                if not isinstance(vmeta, dict):
                    vmeta = {}
                vmeta["validated_at"] = validated_at
                vmeta["walk_forward"] = walk_forward
                if "pbo" in meta:
                    vmeta["pbo"] = meta.get("pbo")
                if pbo_audit is not None:
                    vmeta["pbo_audit"] = pbo_audit
                with open(vmeta_path, "w", encoding="utf-8") as f:
                    json.dump(vmeta, f, indent=2, default=str)
            except Exception:
                logger.debug("Failed to stamp version metadata %s", vmeta_path, exc_info=True)

    return {
        "ok": True,
        "validated_at": validated_at,
        "path": meta_path,
        "sidecar": side_path,
        "trained_at": meta.get("trained_at"),
        "version_id": meta.get("version_id"),
    }


def dataset_summary_from_metadata(meta: dict | None) -> dict[str, Any] | None:
    """Compact dataset browser payload from metadata.json."""
    if not isinstance(meta, dict):
        return None
    metrics = meta.get("metrics") if isinstance(meta.get("metrics"), dict) else {}
    tw = meta.get("training_window") if isinstance(meta.get("training_window"), dict) else {}
    return {
        "sample_count": meta.get("sample_count") or metrics.get("train_samples"),
        "train_samples": metrics.get("train_samples"),
        "val_samples": metrics.get("val_samples"),
        "candle_bars": meta.get("candle_bars") if meta.get("candle_bars") is not None else tw.get("bars"),
        "bar_target": meta.get("bar_target") if meta.get("bar_target") is not None else tw.get("bar_limit"),
        "label_distribution": meta.get("label_distribution"),
        "feature_names": meta.get("feature_names"),
        "feature_schema_version": meta.get("feature_schema_version"),
        "top_features": meta.get("top_features"),
        "horizons": meta.get("horizons"),
        "lookback": (meta.get("config") or {}).get("lookback") if isinstance(meta.get("config"), dict) else meta.get("lookback"),
        "model_type": meta.get("model_type"),
        "trained_at": meta.get("trained_at"),
        "version_id": meta.get("version_id"),
        "config": meta.get("config") if isinstance(meta.get("config"), dict) else None,
    }


def validation_summary_from_metadata(meta: dict | None) -> dict[str, Any]:
    """Compact deploy-readiness fields for model-status (additive, UI-safe).

    Mirrors what ``deploy_gate`` reads from disk without changing gate logic.
    """
    if not isinstance(meta, dict):
        return {
            "validated_at": None,
            "walk_forward": None,
            "pbo": None,
        }

    wf_raw = meta.get("walk_forward") if isinstance(meta.get("walk_forward"), dict) else {}
    validated_at = meta.get("validated_at") or wf_raw.get("validated_at")
    walk_forward = None
    if wf_raw or validated_at:
        walk_forward = {
            "ok": bool(wf_raw.get("ok")),
            "mean_oos_accuracy": wf_raw.get("mean_oos_accuracy"),
            "mean_oos_return_pct": wf_raw.get("mean_oos_return_pct"),
            "metric_kind": wf_raw.get("metric_kind"),
            "n_folds": wf_raw.get("n_folds"),
            "successful_folds": wf_raw.get("successful_folds"),
            "recommendation": wf_raw.get("recommendation"),
            "mode": wf_raw.get("mode"),
            "validated_at": validated_at,
            "capacity_gap_warning": wf_raw.get("capacity_gap_warning"),
        }

    pbo_out = None
    pbo_val = meta.get("pbo")
    pbo_audit = meta.get("pbo_audit") if isinstance(meta.get("pbo_audit"), dict) else {}
    if pbo_val is not None or pbo_audit:
        skipped = bool(pbo_audit.get("skipped"))
        ok = False
        if pbo_val is not None and not skipped:
            from app.services.bots.pbo_policy import pbo_passes

            ok = pbo_passes(pbo_val)
        pbo_out = {
            "pbo": pbo_val,
            "ok": ok,
            "skipped": skipped,
            "error": pbo_audit.get("error"),
            "recommendation": pbo_audit.get("recommendation"),
        }

    return {
        "validated_at": validated_at,
        "walk_forward": walk_forward,
        "pbo": pbo_out,
    }


def unlink_quiet(path: str | None) -> None:
    """Best-effort file delete (Windows mmap / race tolerant)."""
    if not path:
        return
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def export_onnx_single_file(
    model: Any,
    args: Any,
    dest_path: str,
    *,
    input_names: list[str],
    output_names: list[str],
    dynamic_axes: dict | None = None,
    opset_version: int = 17,
    invalidate: Any | None = None,
) -> str:
    """Export a Torch module to a single-file ONNX (no ``*.onnx.data`` sidecar).

    Torch 2.9+ may emit external weight files. On Windows those sidecars stay
    memory-mapped by onnxruntime after OOS eval, so the next walk-forward fold
    fails with ``[Errno 22] Invalid argument`` when overwriting the live path.

    Strategy:
      1. Drop ORT sessions via ``invalidate()`` + ``gc.collect()``
      2. Unlink existing ``.onnx`` / ``.onnx.data``
      3. Export to a temp file with ``external_data=False`` when supported
      4. If a sidecar still appears, re-save via onnx as a single file
      5. Atomically replace into ``dest_path``
    """
    import gc
    import tempfile
    import uuid
    import warnings

    if invalidate is not None:
        try:
            invalidate()
        except Exception:
            logger.debug("ONNX export invalidate callback failed", exc_info=True)
    gc.collect()

    dest = os.path.abspath(dest_path)
    dest_data = dest + ".data"
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    unlink_quiet(dest_data)
    unlink_quiet(dest)

    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("PyTorch required for ONNX export") from exc

    # Torch 2.9+ calls into the ``onnx`` package during export (onnxscript helpers).
    try:
        import onnx  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "ONNX export requires the 'onnx' package. "
            "Install with: pip install onnx onnxscript"
        ) from exc

    # Train may run on CUDA; ONNX export + live ORT inference are CPU-only.
    from app.services.bots.ml_torch_device import module_cpu_copy

    export_model = module_cpu_copy(model)
    if isinstance(args, (tuple, list)):
        export_args = tuple(
            a.detach().cpu() if hasattr(a, "detach") else a for a in args
        )
        if len(export_args) == 1:
            export_args = export_args[0]
    elif hasattr(args, "detach"):
        export_args = args.detach().cpu()
    else:
        export_args = args

    export_kwargs: dict[str, Any] = {
        "input_names": list(input_names),
        "output_names": list(output_names),
        "opset_version": int(opset_version),
    }
    if dynamic_axes:
        export_kwargs["dynamic_axes"] = dynamic_axes

    tmp_dir = tempfile.mkdtemp(prefix="ml_onnx_")
    tmp_path = os.path.join(tmp_dir, f"model_{uuid.uuid4().hex}.onnx")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                torch.onnx.export(
                    export_model,
                    export_args,
                    tmp_path,
                    dynamo=False,
                    external_data=False,
                    **export_kwargs,
                )
            except TypeError:
                # Older torch: no dynamo / external_data kwargs.
                torch.onnx.export(export_model, export_args, tmp_path, **export_kwargs)

        tmp_data = tmp_path + ".data"
        if os.path.isfile(tmp_data):
            try:
                import onnx

                onnx_model = onnx.load(tmp_path, load_external_data=True)
                onnx.save_model(onnx_model, dest, save_as_external_data=False)
            finally:
                unlink_quiet(tmp_data)
                unlink_quiet(tmp_path)
        else:
            os.replace(tmp_path, dest)

        unlink_quiet(dest_data)
        return dest
    finally:
        unlink_quiet(tmp_path)
        unlink_quiet(tmp_path + ".data")
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


# ── Version status management (champion / challenger / retired) ──────────


def update_model_version_meta(
    strategy: str,
    symbol: str,
    model_version: str,
    *,
    display_name: str | None = None,
    clear_display_name: bool = False,
    protected: bool | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    """Rename and/or favorite (protect) a version index entry.

    ``version_id`` stays the immutable filesystem key; ``display_name`` is the
    user-facing label. Protected versions are skipped by snapshot prune and
    blocked from delete unless forced.
    """
    strat = str(strategy or "").upper()
    sym = str(symbol or "").upper()
    root = model_root_for(strat, sym, timeframe)
    if not root or not os.path.isdir(root):
        return {"ok": False, "error": f"No model directory for {strat}/{sym}"}

    entry = find_version_entry(root, model_version)
    if not entry:
        return {"ok": False, "error": f"Version not found: {model_version}"}

    vid = str(entry.get("version_id") or "")
    index = _read_index(root)
    updated = None
    for row in index:
        if str(row.get("version_id") or "") != vid and str(row.get("trained_at") or "") != str(
            entry.get("trained_at") or ""
        ):
            continue
        if clear_display_name:
            row.pop("display_name", None)
        elif display_name is not None:
            name = str(display_name).strip()[:80]
            if name:
                row["display_name"] = name
            else:
                row.pop("display_name", None)
        if protected is not None:
            if protected:
                row["protected"] = True
            else:
                row.pop("protected", None)
        updated = dict(row)
        break

    if updated is None:
        # Index miss but directory exists — append a row so rename still works.
        updated = dict(entry)
        if clear_display_name:
            updated.pop("display_name", None)
        elif display_name is not None:
            name = str(display_name).strip()[:80]
            if name:
                updated["display_name"] = name
        if protected is not None:
            if protected:
                updated["protected"] = True
            else:
                updated.pop("protected", None)
        index.insert(0, updated)

    _write_index(root, index)
    return {
        "ok": True,
        "strategy": strat,
        "symbol": sym,
        "version_id": vid,
        "display_name": updated.get("display_name"),
        "protected": bool(updated.get("protected")),
        "versions": list_model_versions(root),
    }


def update_version_status(
    strategy: str,
    symbol: str,
    version_id: str,
    status: str,
    timeframe: str | None = None,
) -> bool:
    """Update the status field of a model version in the index.

    Parameters
    ----------
    strategy : str
        Strategy ID.
    symbol : str
        Trading symbol.
    version_id : str
        Version ID to update.
    status : str
        New status: 'champion', 'challenger', or 'retired'.
    timeframe : str, optional
        Bar timeframe used when the model was trained (default 1m).

    Returns
    -------
    bool — True if the version was found and updated.
    """
    root = model_root_for(strategy, symbol, timeframe)
    if not root:
        return False

    vroot = versions_dir(root)
    index_path = os.path.join(vroot, "index.json")
    if not os.path.isfile(index_path):
        return False

    try:
        with open(index_path, encoding="utf-8") as fh:
            entries = json.load(fh)
    except Exception:
        return False

    updated = False
    for entry in entries:
        if entry.get("version_id") == version_id:
            entry["status"] = status
            updated = True
        elif status == "champion" and entry.get("status") == "champion":
            # Demote the old champion
            entry["status"] = "retired"

    if updated:
        try:
            with open(index_path, "w", encoding="utf-8") as fh:
                json.dump(entries, fh, indent=2)
            logger.info(
                "Updated version %s status to '%s' for %s/%s",
                version_id, status, strategy, symbol,
            )
        except Exception as exc:
            logger.error("Failed to write version index: %s", exc)
            return False

    return updated


