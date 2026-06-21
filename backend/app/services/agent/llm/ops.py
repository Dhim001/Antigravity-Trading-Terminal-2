"""Optional Ollama CLI ops — pull models and health checks (not on inference hot path)."""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

import httpx

from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_MODEL_DEEP, OLLAMA_MODEL_NARRATOR

logger = logging.getLogger(__name__)

PULL_TIMEOUT_SEC = 600.0


def ollama_cli_available() -> bool:
    return shutil.which("ollama") is not None


async def ollama_http_health() -> dict[str, Any]:
    base = OLLAMA_BASE_URL.rstrip("/")
    out: dict[str, Any] = {"base_url": base, "reachable": False, "models": []}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base}/api/tags")
            if resp.status_code == 200:
                out["reachable"] = True
                body = resp.json()
                out["models"] = [
                    str(item["name"])
                    for item in (body.get("models") or [])
                    if item.get("name")
                ]
    except Exception as exc:
        out["error"] = str(exc)
    return out


async def ollama_ops_status() -> dict[str, Any]:
    """Combined HTTP + CLI availability for operator tooling."""
    http = await ollama_http_health()
    cli = ollama_cli_available()
    installed = set(http.get("models") or [])
    tiers = {
        "narrator": OLLAMA_MODEL_NARRATOR or OLLAMA_MODEL,
        "deep": OLLAMA_MODEL_DEEP or OLLAMA_MODEL,
        "default": OLLAMA_MODEL,
    }
    tier_status = {
        name: {"configured": model, "installed": model in installed if model else False}
        for name, model in tiers.items()
        if model
    }
    return {
        "ok": True,
        "cli_available": cli,
        "http": http,
        "tier_models": tier_status,
    }


async def pull_ollama_model(model: str, *, timeout_sec: float = PULL_TIMEOUT_SEC) -> dict[str, Any]:
    """Run `ollama pull <model>` — operator-only, not used during inference."""
    name = (model or "").strip()
    if not name:
        return {"ok": False, "error": "model is required"}
    if not ollama_cli_available():
        return {"ok": False, "error": "ollama CLI not found on PATH"}

    try:
        proc = await asyncio.create_subprocess_exec(
            "ollama",
            "pull",
            name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"pull timed out after {int(timeout_sec)}s", "model": name}
    except Exception as exc:
        logger.warning("ollama pull failed: %s", exc)
        return {"ok": False, "error": str(exc), "model": name}

    if proc.returncode != 0:
        err = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
        return {"ok": False, "error": err or f"ollama pull exited {proc.returncode}", "model": name}

    http = await ollama_http_health()
    return {
        "ok": True,
        "model": name,
        "installed": name in (http.get("models") or []),
        "message": f"Pulled {name}",
    }
