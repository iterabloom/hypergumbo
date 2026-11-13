#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
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

# Model used for embeddings
UNIXCODER_MODEL_NAME = "microsoft/unixcoder-base"


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
        NEGATIVE_PATTERNS,
    )

    # UnixCoder embeddings (768 dim)
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

    # Write output file
    output_path = Path(__file__).parent.parent / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core" / "_embedding_data.py"

    content = f'''"""Pre-computed probe embeddings for sketch_embeddings.py.

This module contains base64-encoded float16 embedding vectors for the probe
patterns used in README description extraction and config file analysis.

README_PROBES_B64 (README description extraction) is decoded at runtime
instead of re-encoding the probes, which avoids the ~2-3 second startup cost
of encoding them. ANSWER/BIGPIC/NEGATIVE_PROBES_B64 are generated
for config analysis, but as of 2026-10 nothing reads NEGATIVE_PROBES_B64 and
the ANSWER/BIGPIC getters in sketch_embeddings.py have no caller:
sketch._extract_config_embedding encodes those three probe sets live on every
call.

Generated with UnixCoder (microsoft/unixcoder-base), 768 dimensions.

To regenerate: python scripts/compute_probe_embeddings.py
"""

# README description probes: {len(README_DESCRIPTION_PROBES)} probes, 768 dimensions
# Used in extract_readme_description_embedding()
README_PROBES_B64 = (
    "{readme_b64}"
)

# Answer patterns: {len(ANSWER_PATTERNS)} probes, 768 dimensions
# Intended for sketch._extract_config_embedding(); no live reader as of 2026-10
# (that function encodes these probes live)
ANSWER_PROBES_B64 = (
    "{answer_b64}"
)

# Big picture questions: {len(BIG_PICTURE_QUESTIONS)} probes, 768 dimensions
# Intended for sketch._extract_config_embedding(); no live reader as of 2026-10
# (that function encodes these probes live)
BIGPIC_PROBES_B64 = (
    "{bigpic_b64}"
)

# Negative patterns: {len(NEGATIVE_PATTERNS)} probes, 768 dimensions
# Intended for sketch._extract_config_embedding() to penalize non-config
# content; no reader as of 2026-10 (that function encodes these probes live)
NEGATIVE_PROBES_B64 = (
    "{negative_b64}"
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

    print("\nDone!")


if __name__ == "__main__":
    main()
