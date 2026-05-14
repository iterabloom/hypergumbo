# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hypergumbo's internal write wrappers — declare each write's safety zone.

These thin wrappers exist for one purpose: give the verify-claims taint
analysis distinct callees per safety zone. The structural taint pass
matches sinks by callee name, not by argument value, so two
``path.write_text(...)`` call sites are indistinguishable to it even when
one writes to ``~/.cache/hypergumbo/`` and the other to a user-supplied
``--out`` path. Wrapping each write in a uniquely-named function gives
each safety zone a distinct sink callee, which the project-local catalog
at ``docs/hypergumbo-self-catalog/`` declares in its corresponding zone.

The wrappers are otherwise transparent — they delegate to
``Path.write_text`` / ``Path.write_bytes`` / ``shutil.copy2`` /
``np.save`` etc. No behavior change relative to a direct call.

How to use:
- Cache writes (``~/.cache/hypergumbo/<fingerprint>/...``) use
  :func:`cache_write` / :func:`cache_write_bytes` / :func:`cache_save_npy`.
- User-supplied ``--out`` writes use :func:`user_out_write` /
  :func:`user_out_open_json_dump` (for the ``with open(...) as f:
  json.dump(...)`` pattern).
- Ephemeral ``/tmp/`` artifacts (sketch comparisons, grammar build dirs)
  use :func:`tmp_artifact_write`.
- Install-target writes (downloaded binaries, archive contents) use
  :func:`install_artifact_write_bytes` / :func:`install_artifact_copy`.

What this pattern does NOT do:
- It does NOT prevent misuse — a developer who writes the wrong wrapper
  for a given write still gets the wrong zone. The pattern is a
  declarative-honesty discipline; the linter or audit catches drift.
- It does NOT extend hypergumbo-the-tool's analyzer with new
  capabilities. The analyzer still works on call-graph BFS by callee
  name. The wrappers just give the BFS distinct callees to bin into
  zones.
- It is NOT shipped to PyPI consumers. This module lives in
  hypergumbo's source tree so its OWN code can be zone-tagged; the
  pattern is documented in ``docs/SECURITY.md`` as the recommended
  technique for any hypergumbo user wanting path-bounded claims in
  their own code (in any language). Each project authors its own
  wrappers in its own language.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing-only import
    import numpy as np


def _safety_zone_barrier() -> None:
    """Structural-taint-pass barrier marker. Invoked by every wrapper below.

    The wrapper functions in this module each call this marker exactly
    once. Declaring ``_safety_zone_barrier`` as a sanitizer for every
    entry-point taint label in
    ``docs/hypergumbo-self-catalog/zone_barrier_sanitizers.yaml`` makes
    each wrapper itself behave as a sanitizer node in the structural
    BFS: the BFS records the wrapper as a zone sink (correct) but does
    NOT propagate forward into the wrapper's internal
    ``open`` / ``json.dump`` / ``Path.write_text`` calls — which would
    otherwise spuriously fire a ``host_fs`` finding for every
    user_out / user_cache / tmp_artifact write.

    Body is a no-op at runtime. The presence of the call is what makes
    each wrapper a sanitizer node.
    """


def cache_write(path: Path, content: str) -> None:
    """Write content to a hypergumbo cache path under ``~/.cache/hypergumbo/``.

    SAFETY ZONE: ``user_cache``. Only invoke for paths under the
    hypergumbo cache directory; never for arbitrary user paths.
    """
    _safety_zone_barrier()
    path.write_text(content)


def cache_write_bytes(path: Path, data: bytes) -> None:
    """Binary version of :func:`cache_write` for non-text cache entries.

    SAFETY ZONE: ``user_cache``.
    """
    _safety_zone_barrier()
    path.write_bytes(data)


def cache_save_npy(path: Path, embedding: "np.ndarray") -> None:  # pragma: no cover - exercised by test_cache_save_npy with importorskip
    """Save a numpy array to a cache path via ``np.save``.

    SAFETY ZONE: ``user_cache``. Embeddings cache is the primary
    consumer; ``np.save`` is the canonical write for ``.npy`` files.
    """
    _safety_zone_barrier()
    # Local import keeps numpy soft-required; sketch_embeddings already
    # imports numpy at call time, so this stays cost-free in practice.
    import numpy as np
    np.save(path, embedding)


def user_out_write(path: Path, content: str) -> None:
    """Write content to a user-supplied ``--out`` / ``--output`` path.

    SAFETY ZONE: ``user_out``. Used by ``cmd_slice`` / ``cmd_search`` /
    ``cmd_io_boundaries`` and similar runtime CLI subcommands that
    accept a user-specified destination. The user typed the path; they
    own it.
    """
    _safety_zone_barrier()
    path.write_text(content)


def user_out_open_json_dump(path: Path, obj: Any) -> None:
    """``with open(path, "w") as f: json.dump(obj, f, ...)`` in one call.

    SAFETY ZONE: ``user_out``. Same semantics as ``user_out_write`` but
    for the common ``open() + json.dump()`` shape used by ``cmd_compact``,
    ``cmd_run``, and similar. Always ``indent=2, sort_keys=True`` to
    keep output deterministic — required so the safety claim's
    re-verification can run on a freshly-generated artifact and produce
    byte-identical output.
    """
    _safety_zone_barrier()
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)


def tmp_artifact_write(path: Path, content: str) -> None:
    """Write content to an ephemeral path under ``/tmp/`` or a tempdir.

    SAFETY ZONE: ``tmp_artifact``. Used by sketch budget-comparison
    output and tree-sitter grammar build scaffolding. These writes are
    intentionally ephemeral — the OS clears them on reboot — and not
    audit-load-bearing the way cache writes are.
    """
    _safety_zone_barrier()
    path.write_text(content)


def install_artifact_write_bytes(path: Path, data: bytes) -> None:
    """Write a downloaded archive's bytes to disk during install flow.

    SAFETY ZONE: ``install_artifact``. Used during ``install-gitleaks``
    to land the downloaded release archive before extraction. The path
    is typically under ``/tmp/`` or the install dir.
    """
    _safety_zone_barrier()
    path.write_bytes(data)


def install_artifact_copy(src: Path, dst: Path) -> None:
    """Copy a built/extracted binary to its installation target path.

    SAFETY ZONE: ``install_artifact``. Used by ``install-gitleaks`` to
    place the gitleaks binary under ``~/.local/bin/``. Wraps
    ``shutil.copy2`` so the install-artifact zone has a distinct sink
    callee, separate from the general filesystem zone.
    """
    _safety_zone_barrier()
    shutil.copy2(src, dst)
