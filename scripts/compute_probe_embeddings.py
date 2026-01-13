#!/usr/bin/env python3
"""Compute probe embeddings for sketch_embeddings.py.

This script generates base64-encoded float16 embedding vectors for the probe
patterns used in README description extraction and config file analysis.

Usage:
    python scripts/compute_probe_embeddings.py

Output:
    Writes src/hypergumbo/_embedding_data.py with updated embeddings.
"""
import base64
import logging
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

# Suppress sentence-transformers warnings
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)

# Model used for embeddings
MODEL_NAME = "microsoft/unixcoder-base"


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
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

    from hypergumbo.sketch_embeddings import (
        README_DESCRIPTION_PROBES,
        ANSWER_PATTERNS,
        BIG_PICTURE_QUESTIONS,
    )

    print(f"Loading model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)

    print(f"Encoding README probes ({len(README_DESCRIPTION_PROBES)})...")
    readme_b64 = encode_probes_b64(model, README_DESCRIPTION_PROBES)

    print(f"Encoding answer patterns ({len(ANSWER_PATTERNS)})...")
    answer_b64 = encode_probes_b64(model, ANSWER_PATTERNS)

    print(f"Encoding big picture questions ({len(BIG_PICTURE_QUESTIONS)})...")
    bigpic_b64 = encode_probes_b64(model, BIG_PICTURE_QUESTIONS)

    # Write output file
    output_path = Path(__file__).parent.parent / "src" / "hypergumbo" / "_embedding_data.py"

    content = f'''"""Pre-computed probe embeddings for sketch_embeddings.py.

This module contains base64-encoded float16 embedding vectors for the probe
patterns used in README description extraction and config file analysis.
Pre-computing these avoids the ~2-3 second startup cost of encoding probes
at runtime.

Generated with UnixCoder (microsoft/unixcoder-base).
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
'''

    output_path.write_text(content)
    print(f"Wrote {output_path}")

    # Verify by decoding
    print("\nVerifying...")

    def verify(name: str, b64: str, expected_count: int):
        raw = base64.b64decode(b64)
        arr = np.frombuffer(raw, dtype=np.float16).reshape(expected_count, 768)
        fp32 = arr.astype(np.float32)
        print(f"  {name}: shape={fp32.shape}, norms={np.linalg.norm(fp32, axis=1)[:3]}...")

    verify("README", readme_b64, len(README_DESCRIPTION_PROBES))
    verify("ANSWER", answer_b64, len(ANSWER_PATTERNS))
    verify("BIGPIC", bigpic_b64, len(BIG_PICTURE_QUESTIONS))

    print("\nDone!")


if __name__ == "__main__":
    main()
