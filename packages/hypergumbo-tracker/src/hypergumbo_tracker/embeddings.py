# SPDX-License-Identifier: MPL-2.0
"""Tier 2 embedding-based near-duplicate detection for the hypergumbo tracker.

Uses dense embeddings from nomic-ai/modernbert-embed-base (ONNX, q4f16 quantized,
~140 MB) to discriminate items that share vocabulary but address different topics.
This complements Tier 1 SimHash detection (fast, lexical) with semantic similarity.

Architecture:
- Model download: `urllib.request` fetches ONNX model and tokenizer to
  $XDG_CACHE_HOME/hypergumbo-tracker/models/modernbert-embed-base/.
- Inference: `onnxruntime.InferenceSession` + `tokenizers.Tokenizer` compute
  768-dim embeddings via mean-pooling over token outputs, L2-normalized.
- Semantic tags: Predefined taxonomy of ~10 probe phrases covering tracker domains.
  Each item gets tags where cosine similarity to probe embedding exceeds threshold.
- Duplicate detection: Cosine similarity > 0.85 between item embeddings triggers
  a warning, with shared semantic tags for interpretability.

Dependencies: onnxruntime (~20 MB) + tokenizers (~5 MB), declared as
optional `dedup` group in pyproject.toml.

See ADR-0013 for the full design specification.
"""

from __future__ import annotations

import math
import os
import urllib.request
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hypergumbo_tracker.models import CompiledItem


# ---------------------------------------------------------------------------
# Dependency detection
# ---------------------------------------------------------------------------


def is_dedup_available() -> bool:
    """Check whether onnxruntime and tokenizers are importable."""
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401

        return True
    except ImportError:  # pragma: no cover
        return False


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------

_MODEL_REPO = "nomic-ai/modernbert-embed-base"
_MODEL_FILENAME = "model_q4f16.onnx"
_TOKENIZER_FILENAME = "tokenizer.json"


def _get_model_dir() -> Path:
    """Return the directory for storing the downloaded model.

    Uses $XDG_CACHE_HOME/hypergumbo-tracker/models/modernbert-embed-base/
    (global, not per-repo).
    """
    xdg_cache = os.environ.get("XDG_CACHE_HOME", "")
    if xdg_cache:
        base = Path(xdg_cache)
    else:
        base = Path.home() / ".cache"
    return base / "hypergumbo-tracker" / "models" / "modernbert-embed-base"


def is_model_downloaded(model_dir: Path | None = None) -> bool:
    """Check whether both model and tokenizer files exist on disk."""
    d = model_dir or _get_model_dir()
    return (d / _MODEL_FILENAME).is_file() and (d / _TOKENIZER_FILENAME).is_file()


def download_model(*, force: bool = False, model_dir: Path | None = None) -> Path:
    """Download the ONNX model and tokenizer from Hugging Face.

    Downloads to a .tmp file first, then does an atomic rename to prevent
    partial files. Returns the model directory path.

    Args:
        force: Re-download even if files already exist.
        model_dir: Override directory (default: _get_model_dir()).

    Returns:
        Path to the model directory.
    """
    d = model_dir or _get_model_dir()
    d.mkdir(parents=True, exist_ok=True)

    base_url = f"https://huggingface.co/{_MODEL_REPO}/resolve/main"
    files = [
        (f"{base_url}/onnx/{_MODEL_FILENAME}", d / _MODEL_FILENAME),
        (f"{base_url}/{_TOKENIZER_FILENAME}", d / _TOKENIZER_FILENAME),
    ]

    for url, dest in files:
        if dest.is_file() and not force:
            continue
        tmp = dest.with_suffix(".tmp")
        urllib.request.urlretrieve(url, str(tmp))  # noqa: S310 — URL is hardcoded to huggingface.co  # nosec B310
        tmp.rename(dest)

    return d


# ---------------------------------------------------------------------------
# Embedding computation
# ---------------------------------------------------------------------------


class EmbeddingModel:
    """Lazy-loaded ONNX embedding model with tokenizer.

    Loads the model and tokenizer on first call to embed(). Produces
    768-dim L2-normalized embeddings via mean-pooling over token outputs.
    """

    def __init__(self, model_dir: Path | None = None) -> None:
        self._model_dir = model_dir
        self._session: Any = None
        self._tokenizer: Any = None

    def _load(self) -> None:
        """Lazily initialize the ONNX session and tokenizer."""
        if self._session is not None:
            return

        import onnxruntime
        import tokenizers

        d = self._model_dir or _get_model_dir()
        model_path = d / _MODEL_FILENAME
        tokenizer_path = d / _TOKENIZER_FILENAME

        if not model_path.is_file() or not tokenizer_path.is_file():
            raise FileNotFoundError(
                f"Model files not found in {d}. "
                "Run download_model() first or install with dedup extras."
            )

        self._session = onnxruntime.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = tokenizers.Tokenizer.from_file(str(tokenizer_path))

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Compute embeddings for a list of texts.

        Returns 768-dim L2-normalized vectors (unit vectors).
        """
        self._load()

        import numpy as np

        results: list[list[float]] = []
        for text in texts:
            encoding = self._tokenizer.encode(text)
            input_ids = np.array([encoding.ids], dtype=np.int64)
            attention_mask = np.array([encoding.attention_mask], dtype=np.int64)

            outputs = self._session.run(
                None,
                {"input_ids": input_ids, "attention_mask": attention_mask},
            )

            # Mean-pool over token dimension (axis=1), respecting attention mask
            token_embeddings = outputs[0]  # shape: (1, seq_len, hidden_dim)
            mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
            summed = np.sum(token_embeddings * mask_expanded, axis=1)
            count = np.sum(mask_expanded, axis=1)
            mean_pooled = summed / np.maximum(count, 1e-9)

            # L2-normalize
            norm = np.linalg.norm(mean_pooled, axis=1, keepdims=True)
            normalized = mean_pooled / np.maximum(norm, 1e-9)

            results.append(normalized[0].tolist())

        return results


# ---------------------------------------------------------------------------
# Semantic tags
# ---------------------------------------------------------------------------

SEMANTIC_PROBES: dict[str, str] = {
    "call_graph": "function call graph dependencies and invocations",
    "symbol_resolution": "symbol resolution name lookup and disambiguation",
    "edge_attribution": "edge attribution evidence confidence and linking",
    "framework_detection": "web framework route handler middleware detection",
    "parser_correctness": "parser tree-sitter grammar extraction correctness",
    "cross_language": "cross-language polyglot interop FFI bridge linking",
    "test_coverage": "test coverage assertion verification validation",
    "configuration": "configuration setup initialization build dependencies",
    "performance": "performance optimization speed memory efficiency",
    "documentation": "documentation changelog specification architecture",
}

_TAG_SIMILARITY_THRESHOLD = 0.3


def derive_semantic_tags(
    embedding: list[float],
    probe_embeddings: dict[str, list[float]],
) -> list[str]:
    """Derive semantic tags for an item embedding based on probe similarity.

    Returns sorted list of tag names where cosine similarity to the probe
    exceeds _TAG_SIMILARITY_THRESHOLD.

    Args:
        embedding: 768-dim unit vector for the item.
        probe_embeddings: Mapping of tag name to 768-dim unit vector.

    Returns:
        Sorted list of tag names above threshold.
    """
    tags: list[str] = []
    for tag_name, probe_vec in probe_embeddings.items():
        sim = _cosine_similarity(embedding, probe_vec)
        if sim >= _TAG_SIMILARITY_THRESHOLD:
            tags.append(tag_name)
    return sorted(tags)


# ---------------------------------------------------------------------------
# Similarity computation
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Pure Python implementation — no numpy required. Returns 0.0 for zero
    vectors.
    """
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a < 1e-9 or norm_b < 1e-9:
        return 0.0
    return dot / (norm_a * norm_b)


def _item_text_for_embedding(item: CompiledItem) -> str:
    """Extract text from a CompiledItem for embedding computation.

    Mirrors the logic in _item_text_for_simhash but operates on CompiledItem
    rather than raw create op data dicts.
    """
    parts = [item.title, item.description]
    if isinstance(item.fields, dict):
        for v in item.fields.values():
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                parts.extend(str(x) for x in v)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

_COSINE_SIMILARITY_THRESHOLD = 0.85


@dataclass
class EmbeddingDuplicateResult:
    """Result of an embedding-based duplicate check between two items."""

    id_a: str
    id_b: str
    cosine_sim: float
    shared_tags: list[str]


def check_embedding_duplicates(
    items: dict[str, tuple[str, Any]],
    *,
    model: EmbeddingModel | None = None,
) -> list[EmbeddingDuplicateResult]:
    """Check for embedding-based near-duplicates across all items.

    Computes dense embeddings for each item, then checks all pairs for
    cosine similarity above threshold. Skips pairs already in
    duplicate_of or not_duplicate_of.

    Args:
        items: Mapping of item_id to (tier_name, CompiledItem).
        model: Optional pre-initialized EmbeddingModel.

    Returns:
        List of EmbeddingDuplicateResult for pairs above threshold.
        Returns empty list with warning if dependencies are unavailable.
    """
    if not is_dedup_available():
        warnings.warn(
            "Embedding-based dedup unavailable: install with "
            "pip install hypergumbo-tracker[dedup]",
            UserWarning,
            stacklevel=2,
        )
        return []

    if not items:
        return []

    if model is None:
        model_dir = _get_model_dir()
        if not is_model_downloaded(model_dir):
            download_model(model_dir=model_dir)
        model = EmbeddingModel(model_dir)

    # Compute embeddings for all items
    item_ids = sorted(items.keys())
    texts = [_item_text_for_embedding(items[iid][1]) for iid in item_ids]
    embeddings = model.embed(texts)
    id_to_embedding = dict(zip(item_ids, embeddings, strict=True))

    # Compute probe embeddings for semantic tags
    probe_texts = list(SEMANTIC_PROBES.values())
    probe_names = list(SEMANTIC_PROBES.keys())
    probe_embeddings_list = model.embed(probe_texts)
    probe_embeddings = dict(zip(probe_names, probe_embeddings_list, strict=True))

    # Compare all pairs
    results: list[EmbeddingDuplicateResult] = []
    for i, id_a in enumerate(item_ids):
        _, item_a = items[id_a]
        for id_b in item_ids[i + 1:]:
            _, item_b = items[id_b]

            # Skip if already marked
            if id_b in item_a.not_duplicate_of or id_a in item_b.not_duplicate_of:
                continue
            if id_b in item_a.duplicate_of or id_a in item_b.duplicate_of:
                continue

            sim = _cosine_similarity(id_to_embedding[id_a], id_to_embedding[id_b])
            if sim >= _COSINE_SIMILARITY_THRESHOLD:
                tags_a = derive_semantic_tags(id_to_embedding[id_a], probe_embeddings)
                tags_b = derive_semantic_tags(id_to_embedding[id_b], probe_embeddings)
                shared = sorted(set(tags_a) & set(tags_b))
                results.append(EmbeddingDuplicateResult(
                    id_a=id_a,
                    id_b=id_b,
                    cosine_sim=sim,
                    shared_tags=shared,
                ))

    return results
