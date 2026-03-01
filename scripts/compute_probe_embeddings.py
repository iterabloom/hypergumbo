#!/usr/bin/env python3
"""Compute probe embeddings for sketch_embeddings.py.

This script generates base64-encoded float16 embedding vectors for the probe
patterns used in README description extraction, config file analysis, and
additional files ordering.

Usage:
    python scripts/compute_probe_embeddings.py

Output:
    Writes src/hypergumbo/_embedding_data.py with updated embeddings.
"""
import base64
import logging
import os
import re
from pathlib import Path

import numpy as np

# Suppress sentence-transformers warnings
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)

# Work around httpx bug with IPv6 CIDR in NO_PROXY
_IPV6_CIDR_PATTERN = re.compile(r"[0-9a-fA-F:]*::[0-9a-fA-F:]*/\d+")
for _var in ("NO_PROXY", "no_proxy"):
    _value = os.environ.get(_var)
    if _value:
        _filtered = [e.strip() for e in _value.split(",")
                     if not _IPV6_CIDR_PATTERN.match(e.strip())]
        os.environ[_var] = ",".join(_filtered)

from sentence_transformers import SentenceTransformer

# Models used for embeddings
UNIXCODER_MODEL_NAME = "microsoft/unixcoder-base"
MODERNBERT_MODEL_NAME = "nomic-ai/modernbert-embed-base"
MODERNBERT_TRUNCATE_DIM = 256


def encode_probes_b64(model: SentenceTransformer, probes: list[str]) -> str:
    """Encode probes to base64 float16 string.

    Args:
        model: SentenceTransformer model.
        probes: List of probe strings.

    Returns:
        Base64-encoded float16 embeddings.
    """
    # Encode probes
    embeddings = model.encode(probes, convert_to_numpy=True)

    # Normalize
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    normalized = embeddings / (norms + 1e-8)

    # Convert to float16 and base64
    fp16 = normalized.astype(np.float16)
    b64 = base64.b64encode(fp16.tobytes()).decode("ascii")

    return b64


def main():
    # Import probes from the source file
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "packages" / "hypergumbo-core" / "src"))

    from hypergumbo_core.sketch_embeddings import (
        README_DESCRIPTION_PROBES,
        ANSWER_PATTERNS,
        BIG_PICTURE_QUESTIONS,
        ADDITIONAL_FILES_PROBES,
        NEGATIVE_PATTERNS,
    )

    # Part 1: UnixCoder embeddings (768 dim)
    print(f"Loading model: {UNIXCODER_MODEL_NAME}")
    unixcoder = SentenceTransformer(UNIXCODER_MODEL_NAME)

    print(f"Encoding README probes ({len(README_DESCRIPTION_PROBES)})...")
    readme_b64 = encode_probes_b64(unixcoder, README_DESCRIPTION_PROBES)

    print(f"Encoding answer patterns ({len(ANSWER_PATTERNS)})...")
    answer_b64 = encode_probes_b64(unixcoder, ANSWER_PATTERNS)

    print(f"Encoding big picture questions ({len(BIG_PICTURE_QUESTIONS)})...")
    bigpic_b64 = encode_probes_b64(unixcoder, BIG_PICTURE_QUESTIONS)

    print(f"Encoding negative patterns ({len(NEGATIVE_PATTERNS)})...")
    negative_b64 = encode_probes_b64(unixcoder, NEGATIVE_PATTERNS)

    # Part 2: ModernBERT embeddings (256 dim truncated) for additional files
    print(f"\nLoading model: {MODERNBERT_MODEL_NAME}")
    modernbert = SentenceTransformer(
        MODERNBERT_MODEL_NAME,
        truncate_dim=MODERNBERT_TRUNCATE_DIM
    )

    print(f"Encoding 5W1H probes ({len(ADDITIONAL_FILES_PROBES)})...")
    additional_files_embeddings = modernbert.encode(
        ADDITIONAL_FILES_PROBES, convert_to_numpy=True
    )
    # Normalize
    norms = np.linalg.norm(additional_files_embeddings, axis=1, keepdims=True)
    additional_files_normalized = additional_files_embeddings / (norms + 1e-8)
    # Convert to float16 and base64
    additional_files_fp16 = additional_files_normalized.astype(np.float16)
    additional_files_b64 = base64.b64encode(
        additional_files_fp16.tobytes()
    ).decode("ascii")

    # Write output file
    output_path = Path(__file__).parent.parent / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core" / "_embedding_data.py"

    content = f'''"""Pre-computed probe embeddings for sketch_embeddings.py.

This module contains base64-encoded float16 embedding vectors for the probe
patterns used in README description extraction, config file analysis, and
additional files ordering.

Pre-computing these avoids the ~2-3 second startup cost of encoding probes
at runtime.

Generated with:
- UnixCoder (microsoft/unixcoder-base) for README/config probes (768 dim)
- ModernBERT (nomic-ai/modernbert-embed-base) for 5W1H probes (256 dim)

To regenerate: python scripts/compute_probe_embeddings.py
"""

# README description probes: {len(README_DESCRIPTION_PROBES)} probes, 768 dimensions
# Used in extract_readme_description_embedding()
README_PROBES_B64 = (
    "{readme_b64}"
)

# Answer patterns: {len(ANSWER_PATTERNS)} probes, 768 dimensions
# Used in extract_config_embedding()
ANSWER_PROBES_B64 = (
    "{answer_b64}"
)

# Big picture questions: {len(BIG_PICTURE_QUESTIONS)} probes, 768 dimensions
# Used in extract_config_embedding()
BIGPIC_PROBES_B64 = (
    "{bigpic_b64}"
)

# Negative patterns: {len(NEGATIVE_PATTERNS)} probes, 768 dimensions
# Used in extract_config_embedding() to penalize non-config content
NEGATIVE_PROBES_B64 = (
    "{negative_b64}"
)

# 5W1H probes for Additional Files ordering: {len(ADDITIONAL_FILES_PROBES)} probes, {MODERNBERT_TRUNCATE_DIM} dimensions
# Used in _format_additional_files() for semantic ranking
ADDITIONAL_FILES_PROBES_B64 = (
    "{additional_files_b64}"
)
'''

    output_path.write_text(content)
    print(f"Wrote {output_path}")

    # Verify by decoding
    print("\nVerifying...")

    def verify(name: str, b64: str, expected_count: int, dims: int = 768):
        raw = base64.b64decode(b64)
        arr = np.frombuffer(raw, dtype=np.float16).reshape(expected_count, dims)
        fp32 = arr.astype(np.float32)
        print(f"  {name}: shape={fp32.shape}, norms={np.linalg.norm(fp32, axis=1)[:3]}...")

    verify("README", readme_b64, len(README_DESCRIPTION_PROBES))
    verify("ANSWER", answer_b64, len(ANSWER_PATTERNS))
    verify("BIGPIC", bigpic_b64, len(BIG_PICTURE_QUESTIONS))
    verify("NEGATIVE", negative_b64, len(NEGATIVE_PATTERNS))
    verify("5W1H", additional_files_b64, len(ADDITIONAL_FILES_PROBES), dims=MODERNBERT_TRUNCATE_DIM)

    print("\nDone!")


if __name__ == "__main__":
    main()
