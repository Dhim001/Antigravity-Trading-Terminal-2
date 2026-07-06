"""Validate and normalize bot config fields before persist."""

from __future__ import annotations

from app.services.market.timeframes import TIMEFRAME_SECS, is_valid_timeframe, normalize_timeframe

_SUPPORTED_CONFIRM_TFS = ", ".join(TIMEFRAME_SECS.keys())


def normalize_confirm_timeframe(value: str | None) -> str:
    """Return canonical confirm_timeframe or empty string. Raises ValueError if invalid."""
    raw = (value or "").strip()
    if not raw:
        return ""

    candidates = [raw]
    if raw.isdigit():
        candidates.append(f"{raw}m")

    for candidate in candidates:
        try:
            return normalize_timeframe(candidate)
        except ValueError:
            continue

    hint = f' Did you mean "{raw}m"?' if raw.isdigit() else ""
    raise ValueError(
        f'Invalid confirm_timeframe "{raw}".{hint} '
        f"Use a supported bar timeframe ({_SUPPORTED_CONFIRM_TFS}) or leave empty to disable."
    )


def validate_config_patch(
    patch: dict,
    *,
    bot_timeframe: str | None = None,
) -> dict:
    """Return a sanitized copy of patch with normalized confirm_timeframe."""
    if not isinstance(patch, dict):
        raise ValueError("config patch must be an object")
    return normalize_bot_config(patch, bot_timeframe=bot_timeframe)


def normalize_bot_config(
    config: dict | None,
    *,
    bot_timeframe: str | None = None,
) -> dict:
    """Normalize known config fields; raises ValueError on invalid confirm_timeframe."""
    cfg = dict(config or {})
    if "confirm_timeframe" not in cfg:
        return cfg

    cfg["confirm_timeframe"] = normalize_confirm_timeframe(cfg.get("confirm_timeframe"))

    if cfg["confirm_timeframe"] and bot_timeframe:
        try:
            bot_tf = normalize_timeframe(bot_timeframe)
        except ValueError:
            bot_tf = None
        if bot_tf and cfg["confirm_timeframe"] == bot_tf:
            raise ValueError(
                f"confirm_timeframe must differ from bot timeframe ({bot_tf})"
            )

    return cfg


def sanitize_bot_config(config: dict | None) -> tuple[dict, list[str]]:
    """Normalize config in-place copy; return (config, warnings). Auto-fix bare minute values."""
    cfg = dict(config or {})
    warnings: list[str] = []
    raw = (cfg.get("confirm_timeframe") or "").strip()
    if not raw:
        return cfg, warnings

    try:
        normalized = normalize_confirm_timeframe(raw)
    except ValueError:
        warnings.append(
            f'Cleared invalid confirm_timeframe "{raw}". '
            f"Supported: {_SUPPORTED_CONFIRM_TFS}."
        )
        cfg["confirm_timeframe"] = ""
        return cfg, warnings

    if normalized != raw:
        warnings.append(f'Auto-corrected confirm_timeframe "{raw}" → "{normalized}".')
    cfg["confirm_timeframe"] = normalized
    return cfg, warnings
