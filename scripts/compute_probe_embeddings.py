#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compute probe embeddings for sketch_embeddings.py.

This script generates base64-encoded float16 embedding vectors for the probe
patterns used in README description extraction and config file analysis, and
a digest of what each was computed from (the model name and the probe texts).
``sketch_embeddings.probe_embeddings`` uses an array only when its digest
matches the texts and model in the tree, so RE-RUN THIS after editing any
probe list in sketch_embeddings.py or changing the model:
``test_probe_embeddings.py`` fails until you do.

Usage:
    python scripts/compute_probe_embeddings.py

Output:
    Writes packages/hypergumbo-core/src/hypergumbo_core/_embedding_data.py.
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

from sentence_transformers import SentenceTransformer  # noqa: E402 - after the NO_PROXY fix above


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

    from hypergumbo_core.sketch_embeddings import _EMBEDDING_MODEL, PROBE_SETS, probe_digest

    # UnixCoder embeddings (768 dim)
    print(f"Loading model: {_EMBEDDING_MODEL}")
    model = SentenceTransformer(_EMBEDDING_MODEL)

    b64: dict[str, str] = {}
    digests: dict[str, str] = {}
    for name, (probes, _attr) in PROBE_SETS.items():
        print(f"Encoding {name} probes ({len(probes)})...")
        b64[name] = encode_probes_b64(model, probes)
        digests[name] = probe_digest(_EMBEDDING_MODEL, probes)
    count = {name: len(probes) for name, (probes, _attr) in PROBE_SETS.items()}

    # Write output file
    output_path = Path(__file__).parent.parent / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core" / "_embedding_data.py"

    content = f'''# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pre-computed probe embeddings for sketch_embeddings.py.

This module contains base64-encoded float16 embedding vectors for the probe
patterns used in README description extraction and config file analysis.
The probe texts never change between runs, so they are embedded once, here,
and decoded at runtime: encoding them takes the model about a second per
sketch.

``sketch_embeddings.probe_embeddings`` is the one reader. It uses an array
only when ``PROBE_DIGESTS`` says the array was computed from the probe texts
and the model now in the tree; otherwise it encodes the probes itself.

Generated with UnixCoder ({_EMBEDDING_MODEL}), 768 dimensions.

GENERATED FILE. To regenerate: python scripts/compute_probe_embeddings.py
"""

# The model every array below was computed with.
PROBE_MODEL = "{_EMBEDDING_MODEL}"

# Per array: sha256 over the model name and the probe texts it was computed
# from, in order (sketch_embeddings.probe_digest).
PROBE_DIGESTS = {{
    "readme": "{digests["readme"]}",
    "answer": "{digests["answer"]}",
    "bigpic": "{digests["bigpic"]}",
    "negative": "{digests["negative"]}",
}}

# README description probes: {count["readme"]} probes, 768 dimensions
# Read by extract_readme_description_embedding()
README_PROBES_B64 = (
    "{b64["readme"]}"
)

# Answer patterns: {count["answer"]} probes, 768 dimensions
# Read by sketch._extract_config_embedding()
ANSWER_PROBES_B64 = (
    "{b64["answer"]}"
)

# Big picture questions: {count["bigpic"]} probes, 768 dimensions
# Read by sketch._extract_config_embedding()
BIGPIC_PROBES_B64 = (
    "{b64["bigpic"]}"
)

# Negative patterns: {count["negative"]} probes, 768 dimensions
# Read by sketch._extract_config_embedding() to penalize non-config content
NEGATIVE_PROBES_B64 = (
    "{b64["negative"]}"
)
'''

    output_path.write_text(content)
    print(f"Wrote {output_path}")

    # Verify by decoding
    print("\nVerifying...")

    def verify(name: str, dims: int = 768):
        raw = base64.b64decode(b64[name])
        arr = np.frombuffer(raw, dtype=np.float16).reshape(count[name], dims)
        fp32 = arr.astype(np.float32)
        print(f"  {name}: shape={fp32.shape}, norms={np.linalg.norm(fp32, axis=1)[:3]}...")

    for name in PROBE_SETS:
        verify(name)

    print("\nDone!")


if __name__ == "__main__":
    main()
