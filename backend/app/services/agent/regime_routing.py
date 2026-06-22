"""ATR regime routing — apply stricter entry thresholds per vol bucket."""

from __future__ import annotations

from typing import Any

DEFAULT_REGIME_OVERRIDES: dict[str, dict[str, Any]] = {
    "elevated": {"min_confidence": 0.65, "min_score": 3},
    "compressed": {"min_confidence": 0.55},
    "normal": {},
}


def current_atr_regime(insight: dict) -> str:
    sub = insight.get("sub_reports") or {}
    return str((sub.get("risk") or {}).get("atr_regime") or "normal")


def resolve_regime_config(cfg: dict, insight: dict) -> tuple[dict, str | None]:
    """Merge regime-specific thresholds into effective bot config."""
    if not cfg.get("regime_routing_enabled"):
        return cfg, None

    regime = current_atr_regime(insight)
    effective = dict(cfg)

    overrides = cfg.get("regime_overrides")
    if isinstance(overrides, dict):
        patch = overrides.get(regime) or DEFAULT_REGIME_OVERRIDES.get(regime) or {}
    else:
        patch = DEFAULT_REGIME_OVERRIDES.get(regime) or {}
    if patch:
        effective = {**effective, **patch}

    if regime == "elevated":
        elev_conf = cfg.get("elevated_min_confidence")
        if elev_conf is not None and elev_conf != "":
            try:
                floor = float(elev_conf)
                cur = float(effective.get("min_confidence", 0.55))
                effective["min_confidence"] = max(cur, floor)
            except (TypeError, ValueError):
                pass
        elev_score = cfg.get("elevated_min_score")
        if elev_score is not None and elev_score != "":
            try:
                floor = int(elev_score)
                cur = effective.get("min_score")
                effective["min_score"] = max(int(cur) if cur is not None else 0, floor)
            except (TypeError, ValueError):
                pass
        if cfg.get("elevated_block_entries"):
            effective["block_elevated_vol"] = True

    if regime == "compressed":
        comp_conf = cfg.get("compressed_min_confidence")
        if comp_conf is not None and comp_conf != "":
            try:
                floor = float(comp_conf)
                cur = float(effective.get("min_confidence", 0.55))
                effective["min_confidence"] = max(cur, floor)
            except (TypeError, ValueError):
                pass

    return effective, regime
