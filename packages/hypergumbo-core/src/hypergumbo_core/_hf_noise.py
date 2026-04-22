# SPDX-License-Identifier: AGPL-3.0-or-later
"""HuggingFace noise suppression — env-var setup that must run early.

UAT-2026-04-13 UX-02 (WI-gatot) reported that the first analysis per session
dumps ~199 weight-loading progress bars and an authentication-prompt warning
to stderr, polluting piped output and rendering ``--no-progress`` misleading.

The fix is a one-liner per env var, but the *timing* matters: most
HuggingFace libraries (``huggingface_hub``, ``transformers``, ``tokenizers``,
``safetensors``) read these env vars at their own import time and cache the
values. Setting them after sentence_transformers has been imported is too
late.

This module exists as a separate file (rather than inline in
``sketch_embeddings.py``) so:
1. It can be imported and called at the very top of the module — before
   the conditional ``from sentence_transformers import ...`` lines further
   down.
2. It can be coverage-tested without pulling sketch_embeddings.py (which
   is intentionally omitted from coverage because its main bodies require
   the heavy ML deps).

The function uses ``setdefault`` so explicit user overrides (e.g.
``TRANSFORMERS_VERBOSITY=info`` for debugging) are preserved.
"""
from __future__ import annotations

import os

# Mapping of env var name → default value when not already set.
# Edit by adding a row; tests assert each row is honored at import.
_HF_NOISE_DEFAULTS: tuple[tuple[str, str], ...] = (
    ("HF_HUB_DISABLE_PROGRESS_BARS", "1"),
    ("TRANSFORMERS_VERBOSITY", "error"),
    ("HF_HUB_DISABLE_SYMLINKS_WARNING", "1"),
    ("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1"),
)


def suppress_hf_noise() -> None:
    """Apply HuggingFace progress-bar/warning-suppression env defaults.

    Idempotent: re-running has no effect after the first call. User-set
    values (e.g., from the shell environment or an earlier explicit
    ``os.environ[...] = ...``) are preserved by ``setdefault``.
    """
    for name, value in _HF_NOISE_DEFAULTS:
        os.environ.setdefault(name, value)
