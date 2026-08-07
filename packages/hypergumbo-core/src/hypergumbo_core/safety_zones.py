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


class SafetyZoneViolation(RuntimeError):
    """A zone wrapper was handed a path outside the zone it declares.

    Raised rather than silently no-oping: a caller asking to delete
    ``$HOME`` through the cache wrapper has a bug, and swallowing it would
    hide the bug while still failing to do what the caller asked.
    """


def _cache_zone_root() -> Path:
    """Root of the ``user_cache`` zone: ``$XDG_CACHE_HOME/hypergumbo``.

    Resolved at call time, not import time, so a test (or a user) changing
    ``XDG_CACHE_HOME`` moves the zone with it. Duplicating
    ``sketch_embeddings._get_xdg_cache_base`` is deliberate and narrow:
    importing it here would make this module depend on the embeddings stack
    (and on numpy being installed) purely to learn one environment variable.
    """
    import os

    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "hypergumbo"


def _tmp_zone_root() -> Path:
    """Root of the ``tmp_artifact`` zone: the system temp directory."""
    import tempfile

    return Path(tempfile.gettempdir())


def _install_zone_root() -> Path:
    """Root of the ``install_artifact`` zone: ``~/.local/bin``.

    Kept as a literal rather than imported from :mod:`gitleaks` (which owns
    ``GITLEAKS_INSTALL_DIR``) so this module stays import-light and so the
    zone boundary is stated where it is enforced. ``test_safety_zones``
    pins the two against each other, so they cannot drift apart silently.
    """
    return Path.home() / ".local" / "bin"


def _require_within_zone(path: Path, root: Path, zone: str) -> None:
    """Refuse ``path`` unless it is inside ``root``.

    THE DEFECT THIS EXISTS TO CLOSE, stated plainly because a guard whose
    reason is forgotten gets removed: ``cmd_cache_clear`` built its target as
    ``cache_dir / repo`` and deleted it. ``pathlib`` discards the left
    operand when the right is ABSOLUTE, so ``--repo /home/you/thesis``
    resolved to ``/home/you/thesis`` and was recursively deleted, reported as
    a routine cache eviction. A relative ``../..`` escapes the same way. The
    call site checked ``is_dir()`` — existence, not containment.

    Both paths are ``resolve()``d first, so a symlink pointing out of the
    zone is refused too; checking the unresolved path would let
    ``<cache>/evil -> /`` through.

    ``root`` itself is permitted (clearing the whole cache is legitimate);
    anything above or beside it is not.
    """
    try:
        resolved = path.resolve()
        resolved_root = root.resolve()
    except OSError as exc:  # pragma: no cover - unreadable path component
        raise SafetyZoneViolation(
            f"cannot resolve {path} to check the {zone} zone: {exc}",
        ) from exc
    if resolved != resolved_root and not resolved.is_relative_to(resolved_root):
        raise SafetyZoneViolation(
            f"refusing to operate on {resolved}: outside the {zone!r} safety "
            f"zone rooted at {resolved_root}",
        )


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


def cache_rmtree(path: Path, zone_root: Path | None = None) -> None:
    """Recursively delete a cache directory under ``~/.cache/hypergumbo/``.

    SAFETY ZONE: ``user_cache``. Used by ``cmd_cache_clear`` to evict
    stale cache entries. Wrapping ``shutil.rmtree`` here gives the
    structural-taint pass a distinct callee — verify-claims previously
    flagged generic ``.rmtree`` reachability from runtime CLI as a
    documented overapproximation because the bare callee match couldn't
    tell cache-clear writes apart from arbitrary fs deletes.

    ENFORCED, not merely declared: a path outside the cache root raises
    :class:`SafetyZoneViolation`. See :func:`_require_within_zone` for the
    verified escape this closes.

    ``zone_root`` lets a caller that already resolved the cache base pass it
    in, so the guard is checked against the SAME root the caller is
    operating in rather than a second, independently-derived one. Omitting
    it falls back to the XDG-derived default — a caller that forgets is
    still guarded, just against the default zone.
    """
    _safety_zone_barrier()
    _require_within_zone(path, zone_root or _cache_zone_root(), "user_cache")
    shutil.rmtree(path)


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


def user_out_open_json_dump_gzip(path: Path, obj: Any) -> None:
    """``gzip.open + json.dump`` in one call. Same semantics as
    :func:`user_out_open_json_dump` but writes a gzipped JSON payload.

    SAFETY ZONE: ``user_out``. Used by ``cmd_run`` when ``--gzip`` is
    set, on both the main output and the budget-tier outputs. The
    inner JSON encoding is identical to the uncompressed path
    (``indent=2, sort_keys=True``) so ``gunzip <out>.json.gz`` produces
    byte-identical content to the uncompressed path's output.
    """
    _safety_zone_barrier()
    import gzip
    with gzip.open(path, "wt") as f:
        json.dump(obj, f, indent=2, sort_keys=True)


def cache_mkdir(path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    """Create a cache directory (typically under ``~/.cache/hypergumbo/``).

    SAFETY ZONE: ``user_cache``. INV-zudak: ``Path.mkdir`` is an
    fs-write primitive that needs to route through the wrapper just like
    ``cache_write`` / ``cache_write_bytes`` do, so the verify-claims
    pass can distinguish ``user_cache`` scaffolding from arbitrary
    ``host_fs`` directory creation.
    """
    _safety_zone_barrier()
    path.mkdir(parents=parents, exist_ok=exist_ok)


def tmp_artifact_write(path: Path, content: str) -> None:
    """Write content to an ephemeral path under ``/tmp/`` or a tempdir.

    SAFETY ZONE: ``tmp_artifact``. Used by sketch budget-comparison
    output and tree-sitter grammar build scaffolding. These writes are
    intentionally ephemeral — the OS clears them on reboot — and not
    audit-load-bearing the way cache writes are.
    """
    _safety_zone_barrier()
    path.write_text(content)


def tmp_artifact_mkdir(path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    """Create an ephemeral build directory under ``/tmp/`` or a tempdir.

    SAFETY ZONE: ``tmp_artifact``. INV-zudak: pairs with
    :func:`tmp_artifact_write` and :func:`tmp_artifact_rmtree` so that
    every fs-write primitive used by ``build_grammars.py`` routes
    through the wrapper. Without this, ``Path.mkdir`` callsites in
    grammar-scaffolding code surface as raw ``host_fs`` flows.
    """
    _safety_zone_barrier()
    path.mkdir(parents=parents, exist_ok=exist_ok)


def tmp_artifact_rmtree(path: Path) -> None:
    """Recursively delete an ephemeral build directory under ``/tmp/``.

    SAFETY ZONE: ``tmp_artifact``. Used by ``build_grammars.py`` to
    refresh the per-grammar scaffold directory before regenerating it.
    Distinct wrapper from :func:`cache_rmtree` so verify-claims can
    distinguish cache eviction from grammar-build scaffold reset.

    ENFORCED: a path outside the system temp directory raises
    :class:`SafetyZoneViolation`.
    """
    _safety_zone_barrier()
    _require_within_zone(path, _tmp_zone_root(), "tmp_artifact")
    shutil.rmtree(path)


def install_artifact_mkdir(path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    """Create an install-target directory (typically under ``~/.local/`` or
    an extras install dir).

    SAFETY ZONE: ``install_artifact``. INV-zudak: pairs with
    :func:`install_artifact_write_bytes` /
    :func:`install_artifact_copy` so the install zone has a complete
    sink surface for verify-claims. Used by ``install-gitleaks`` to
    scaffold ``GITLEAKS_INSTALL_DIR`` before landing the binary.
    """
    _safety_zone_barrier()
    path.mkdir(parents=parents, exist_ok=exist_ok)


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


def install_artifact_chmod(path: Path, mode: int) -> None:
    """Set the mode bits on an installed binary (typically +x).

    SAFETY ZONE: ``install_artifact``. Called immediately after
    ``install_artifact_copy`` lands a binary, to make it executable.
    Distinct wrapper so verify-claims can distinguish hypergumbo's own
    post-install ``chmod`` from arbitrary mode-bit mutation on the
    user's filesystem.
    """
    _safety_zone_barrier()
    path.chmod(mode)


def install_artifact_unlink(path: Path) -> None:
    """Remove an installed binary (uninstall path).

    SAFETY ZONE: ``install_artifact``. Used by ``cmd_uninstall_gitleaks``
    to evict the hypergumbo-managed binary. Distinct wrapper so
    verify-claims's overapproximate ``.unlink`` matches don't conflate
    install/uninstall flows with arbitrary file removal.

    ENFORCED: a path outside ``~/.local/bin`` raises
    :class:`SafetyZoneViolation`.
    """
    _safety_zone_barrier()
    _require_within_zone(path, _install_zone_root(), "install_artifact")
    path.unlink()
