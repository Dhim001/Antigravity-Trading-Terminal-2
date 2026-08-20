"""LoRA fine-tune of a local embedding model for copilot intent routing.

AI-FT-PTL-001 §3.5 (P3 #14). Three parts:

1. **Data collection** — ``log_copilot_turn`` persists
   ``(user_query, intent_label, tool_call)`` tuples from copilot traffic to a
   JSONL training log.
2. **Training** — ``train_intent_lora`` fine-tunes low-rank (LoRA) adapters
   on top of a *frozen* sentence embedder using the logged pairs, and saves
   the adapter weights + label map under ``data/copilot_intent/``.
3. **Routing** — ``predict_intent`` embeds a query with the tuned model and
   returns the intent label when confidence clears
   ``COPILOT_LORA_MIN_CONFIDENCE``; the caller falls back to rule-based
   classification otherwise. Only narration invokes the LLM.

Base embedder: ``sentence-transformers/all-MiniLM-L6-v2`` when the
``sentence_transformers`` package is installed; otherwise a deterministic
hashing embedder (frozen random projection over token n-grams) with the same
output dimension. The LoRA mechanics — frozen base, trainable low-rank
``A``/``B`` matrices on the projection — are identical either way.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_INTENTS = ("analysis", "explain", "action", "help")


# ── Paths ─────────────────────────────────────────────────────────────────


def _data_dir() -> str:
    from app.config import DATA_DIR

    root = os.path.join(DATA_DIR, "copilot_intent")
    os.makedirs(root, exist_ok=True)
    return root


def _training_log_path() -> str:
    return os.path.join(_data_dir(), "training_pairs.jsonl")


def _adapter_path() -> str:
    return os.path.join(_data_dir(), "lora_adapter.npz")


def _meta_path() -> str:
    return os.path.join(_data_dir(), "lora_meta.json")


# ── 1. Data collection ────────────────────────────────────────────────────


def log_copilot_turn(
    user_query: str,
    intent_label: str,
    tool_call: str | None = None,
) -> None:
    """Append a (query, intent, tool_call) tuple to the training log."""
    try:
        from app.config import COPILOT_LORA_LOG_ENABLED

        if not COPILOT_LORA_LOG_ENABLED:
            return
        query = str(user_query or "").strip()
        intent = str(intent_label or "").strip().lower()
        if not query or intent not in _INTENTS:
            return
        rec = {
            "query": query[:500],
            "intent": intent,
            "tool_call": str(tool_call or "")[:120],
            "ts": time.time(),
        }
        with open(_training_log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        logger.debug("copilot intent log skipped", exc_info=True)


def load_training_pairs(*, min_samples: int | None = None) -> list[dict[str, Any]]:
    """Load logged pairs; returns [] when below the >1000-sample gate."""
    from app.config import COPILOT_LORA_MIN_TRAIN_SAMPLES

    path = _training_log_path()
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("intent") in _INTENTS and rec.get("query"):
                    rows.append(rec)
    except FileNotFoundError:
        return []

    threshold = int(min_samples if min_samples is not None else COPILOT_LORA_MIN_TRAIN_SAMPLES)
    if len(rows) < max(1, threshold):
        return []
    return rows


def training_pair_count() -> int:
    """Total logged pairs (regardless of the min-samples gate)."""
    try:
        with open(_training_log_path(), encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except FileNotFoundError:
        return 0


# ── 2. Frozen base embedder ───────────────────────────────────────────────

_st_model = None
_st_checked = False


def _sentence_transformer():
    """all-MiniLM-L6-v2 when sentence-transformers is installed, else None."""
    global _st_model, _st_checked
    if _st_checked:
        return _st_model
    _st_checked = True
    try:
        from sentence_transformers import SentenceTransformer

        _st_model = SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:
        _st_model = None
    return _st_model


def _token_ngrams(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", str(text or "").lower())
    grams = list(tokens)
    grams.extend(f"{tokens[i]}_{tokens[i + 1]}" for i in range(len(tokens) - 1))
    return grams


def _hash_embed(texts: list[str], dim: int) -> np.ndarray:
    """Deterministic frozen hashing embedder → (n, dim) L2-normalized rows.

    Each n-gram maps to a sparse random ±1 vector via SHA-256; the sentence
    embedding is the mean. No training, no downloads — the frozen base that
    the LoRA adapters adapt.
    """
    out = np.zeros((len(texts), dim), dtype=np.float64)
    for row, text in enumerate(texts):
        grams = _token_ngrams(text)
        if not grams:
            continue
        vec = np.zeros(dim, dtype=np.float64)
        for gram in grams:
            digest = hashlib.sha256(gram.encode("utf-8")).digest()
            # 16 dims per hash block, cycled across the digest bytes.
            for i in range(0, len(digest), 2):
                idx = int.from_bytes(digest[i:i + 2], "big") % dim
                sign = 1.0 if digest[(i // 2 + 13) % len(digest)] & 1 else -1.0
                vec[idx] += sign
        vec /= max(1, len(grams))
        out[row] = vec
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.maximum(norms, 1e-12)


def embed_texts(texts: list[str], *, dim: int | None = None) -> np.ndarray:
    """Embed with the frozen base model → (n, dim) float array."""
    from app.config import COPILOT_LORA_EMBED_DIM

    d = int(dim or COPILOT_LORA_EMBED_DIM)
    st = _sentence_transformer()
    if st is not None:
        try:
            emb = st.encode(list(texts), convert_to_numpy=True, normalize_embeddings=True)
            emb = np.asarray(emb, dtype=np.float64)
            if emb.ndim == 2 and emb.shape[1] == d:
                return emb
        except Exception:
            logger.debug("sentence-transformer encode failed; using hash embedder", exc_info=True)
    return _hash_embed(texts, d)


# ── 3. LoRA adapter model ─────────────────────────────────────────────────
#
# Tuned projection W = W0 + (alpha / r) · B·A with W0 frozen (identity here —
# the base embedder output is already usable), A ∈ R^{r×d}, B ∈ R^{d×r}
# trainable, plus a linear intent classifier head on the adapted embedding.


def _init_adapter(dim: int, rank: int, n_classes: int, seed: int = 42) -> dict[str, np.ndarray]:
    rng = np.random.RandomState(seed)
    return {
        "A": rng.randn(rank, dim) * 0.01,
        "B": np.zeros((dim, rank)),  # zero-init B → adapter is identity at start
        "Wc": rng.randn(n_classes, dim) * 0.01,
        "bc": np.zeros(n_classes),
    }


def _forward(
    emb: np.ndarray, params: dict[str, np.ndarray], alpha: float
) -> np.ndarray:
    """Adapted embedding → class logits. emb: (n, d)."""
    rank = params["A"].shape[0]
    adapted = emb + (alpha / rank) * (emb @ params["A"].T) @ params["B"].T
    return adapted @ params["Wc"].T + params["bc"]


def _softmax(logits: np.ndarray) -> np.ndarray:
    z = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / np.maximum(e.sum(axis=1, keepdims=True), 1e-12)


def train_intent_lora(
    *,
    min_samples: int | None = None,
    epochs: int | None = None,
    lr: float | None = None,
    rank: int | None = None,
) -> dict[str, Any]:
    """Fine-tune LoRA adapters on the logged (query, intent) pairs.

    Full-batch gradient descent on cross-entropy over the frozen base
    embeddings; only A, B, Wc, bc are updated. Persists the adapter +
    label map and returns training metrics. Never raises.
    """
    try:
        from app.config import (
            COPILOT_LORA_EMBED_DIM,
            COPILOT_LORA_EPOCHS,
            COPILOT_LORA_LR,
            COPILOT_LORA_RANK,
        )

        rows = load_training_pairs(min_samples=min_samples)
        if not rows:
            return {
                "ok": False,
                "error": "insufficient training pairs",
                "sample_count": training_pair_count(),
            }

        dim = int(COPILOT_LORA_EMBED_DIM)
        r = max(1, int(rank or COPILOT_LORA_RANK))
        n_epochs = max(1, int(epochs or COPILOT_LORA_EPOCHS))
        eta = float(lr or COPILOT_LORA_LR)
        alpha = float(r)  # standard LoRA scaling: alpha = r → scale 1.0

        labels = sorted({str(rec["intent"]) for rec in rows})
        label_to_idx = {lab: i for i, lab in enumerate(labels)}
        n_classes = len(labels)
        if n_classes < 2:
            return {"ok": False, "error": "need ≥2 intent classes", "sample_count": len(rows)}

        texts = [str(rec["query"]) for rec in rows]
        X = embed_texts(texts, dim=dim)
        y = np.array([label_to_idx[str(rec["intent"])] for rec in rows], dtype=np.int64)
        n = len(rows)

        params = _init_adapter(dim, r, n_classes)
        one_hot = np.zeros((n, n_classes))
        one_hot[np.arange(n), y] = 1.0

        final_loss = 0.0
        for _ in range(n_epochs):
            logits = _forward(X, params, alpha)
            probs = _softmax(logits)
            final_loss = float(-np.log(np.maximum(probs[np.arange(n), y], 1e-12)).mean())

            # Gradients through the classifier head.
            dlogits = (probs - one_hot) / n
            adapted = X + (alpha / r) * (X @ params["A"].T) @ params["B"].T
            params["Wc"] -= eta * (dlogits.T @ adapted)
            params["bc"] -= eta * dlogits.sum(axis=0)

            # Backprop into the LoRA adapter (B·A path).
            dadapted = dlogits @ params["Wc"]
            scale = alpha / r
            dB = scale * (dadapted.T @ (X @ params["A"].T))
            dA = scale * ((dadapted @ params["B"]).T @ X)
            params["B"] -= eta * dB
            params["A"] -= eta * dA

        logits = _forward(X, params, alpha)
        preds = _softmax(logits).argmax(axis=1)
        acc = float((preds == y).mean())

        np.savez(
            _adapter_path(),
            A=params["A"], B=params["B"], Wc=params["Wc"], bc=params["bc"],
        )
        meta = {
            "labels": labels,
            "dim": dim,
            "rank": r,
            "alpha": alpha,
            "trained_at": time.time(),
            "sample_count": n,
            "train_accuracy": round(acc, 4),
            "final_loss": round(final_loss, 6),
            "base_model": (
                "sentence-transformers/all-MiniLM-L6-v2"
                if _sentence_transformer() is not None
                else "hashing-ngram-v1"
            ),
        }
        with open(_meta_path(), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)

        _invalidate_router_cache()
        logger.info(
            "Copilot intent LoRA trained: n=%d classes=%d acc=%.3f loss=%.4f",
            n, n_classes, acc, final_loss,
        )
        return {"ok": True, **meta}
    except Exception as exc:
        logger.warning("train_intent_lora failed: %s", exc, exc_info=True)
        return {"ok": False, "error": str(exc)}


# ── 4. Inference / routing ────────────────────────────────────────────────

_router_cache: dict[str, Any] | None = None


def _invalidate_router_cache() -> None:
    global _router_cache
    _router_cache = None


def _load_router() -> dict[str, Any] | None:
    global _router_cache
    if _router_cache is not None:
        return _router_cache
    try:
        with open(_meta_path(), encoding="utf-8") as fh:
            meta = json.load(fh)
        blob = np.load(_adapter_path())
        _router_cache = {
            "labels": list(meta.get("labels") or []),
            "alpha": float(meta.get("alpha") or 1.0),
            "A": blob["A"], "B": blob["B"], "Wc": blob["Wc"], "bc": blob["bc"],
        }
    except FileNotFoundError:
        _router_cache = None
    except Exception as exc:
        logger.debug("intent router load failed: %s", exc)
        _router_cache = None
    return _router_cache


def predict_intent(query: str) -> tuple[str, float] | None:
    """Route a query via the tuned embeddings.

    Returns ``(intent, confidence)`` when a trained adapter exists and the
    top-class softmax clears ``COPILOT_LORA_MIN_CONFIDENCE``; ``None``
    otherwise (caller keeps rule-based classification).
    """
    try:
        from app.config import COPILOT_LORA_ENABLED, COPILOT_LORA_MIN_CONFIDENCE

        if not COPILOT_LORA_ENABLED:
            return None
        router = _load_router()
        if router is None or len(router["labels"]) < 2:
            return None
        emb = embed_texts([str(query or "")], dim=int(router["A"].shape[1]))
        logits = _forward(emb, router, router["alpha"])
        probs = _softmax(logits)[0]
        idx = int(np.argmax(probs))
        conf = float(probs[idx])
        if conf < float(COPILOT_LORA_MIN_CONFIDENCE):
            return None
        return router["labels"][idx], round(conf, 4)
    except Exception:
        logger.debug("predict_intent skipped", exc_info=True)
        return None


def intent_router_status() -> dict[str, Any]:
    """Status snapshot for diagnostics endpoints."""
    router = _load_router()
    return {
        "trained": router is not None,
        "labels": list(router["labels"]) if router else [],
        "training_pairs": training_pair_count(),
        "adapter_path": _adapter_path() if router else None,
    }
