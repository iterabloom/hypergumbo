# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compute a hash representing the analyzer's identity (WI-panih).

The hypergumbo results cache is keyed by:

    ~/.cache/hypergumbo/<fingerprint>/results/<state_hash>/<analyzer_identity>/

This module computes the ``<analyzer_identity>`` segment. Without it,
two hypergumbo installs analyzing the same source tree share a cache
entry — which is wrong because:

1. **Stable + dev coexistence.** A ``pipx install hypergumbo`` (released
   5.0.x) on PATH alongside an editable install for dev work produces
   the same ``<fingerprint>/<state_hash>`` key. Whichever runs first
   poisons the cache for the other.
2. **Wheel-pin RCT.** mgumbo / dgumbo / stable hypergumbo arms
   analyzing the same target must produce different outputs by design.
   Without analyzer identity in the key, the first arm to touch the
   cache poisons it for the other two.
3. **Lang-package partial upgrades.** A new analyzer or linker shipped
   in ``hypergumbo-lang-extended1`` without bumping
   ``hypergumbo-core``'s ``__version__`` changes output for some
   target repos; pre-upgrade cached results would still be served.

How it works
------------

The hash combines two signals so neither failure mode above goes
unobserved:

- ``hypergumbo_core.__version__`` — a cheap base discriminator that
  handles released-vs-released without walking the filesystem.
- A SHA256 over every installed ``hypergumbo_*`` package's ``.py``
  file content (sorted by relative path), capturing dev edits AND
  lang-package upgrades AND the presence of new lang packages.

The discovery walks ``importlib.metadata.distributions()`` for any
distribution whose ``Name`` starts with ``hypergumbo``, then imports
each one to read its ``__path__``. This is robust to pipx, pip,
editable installs, and virtualenv layouts.

Performance: the walk runs once per process; the result is memoized.
On a typical install (~7 packages, hundreds of ``.py`` files) it's
single-digit milliseconds.
"""

from __future__ import annotations

import hashlib
from importlib.metadata import distributions
from pathlib import Path
from types import ModuleType


_CACHED_HASH: str | None = None


def compute_analyzer_identity_hash() -> str:
    """Return a 16-char hex hash identifying the running analyzer.

    Memoized for the lifetime of the process; call
    :func:`reset_cache_for_testing` from tests that need to exercise
    the recompute path.
    """
    global _CACHED_HASH
    if _CACHED_HASH is not None:
        return _CACHED_HASH

    package_hashes: dict[str, str] = {}
    seen_paths: set[Path] = set()

    # Always include hypergumbo_core first — it's the entry-point
    # package and carries the version string.
    import hypergumbo_core as _hg_core

    package_hashes["hypergumbo_core"] = _hash_package_py_files(_hg_core)
    seen_paths.update(
        Path(p).resolve() for p in (getattr(_hg_core, "__path__", []) or [])
    )

    # Discover every installed `hypergumbo*` distribution and hash its
    # importable module. The `Name` → module-name translation maps
    # dashes to underscores (`hypergumbo-lang-mainstream` → module
    # `hypergumbo_lang_mainstream`).
    for dist in distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if not name.startswith("hypergumbo"):
            continue
        module_name = name.replace("-", "_")
        if module_name in package_hashes:
            continue
        module = _try_import(module_name)
        if module is None:
            continue
        paths = [
            Path(p).resolve()
            for p in (getattr(module, "__path__", []) or [])
        ]
        if not paths:
            continue
        # Avoid double-counting namespace packages that share a path
        # with one we've already hashed.
        if any(p in seen_paths for p in paths):
            continue
        seen_paths.update(paths)
        package_hashes[module_name] = _hash_package_py_files(module)

    version = getattr(_hg_core, "__version__", "0.0.0")
    parts = [f"__version__={version}"]
    for name in sorted(package_hashes.keys()):
        parts.append(f"{name}={package_hashes[name]}")
    combined = "\n".join(parts)
    _CACHED_HASH = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]
    return _CACHED_HASH


def _try_import(module_name: str) -> ModuleType | None:
    """Import a module by name, return None on ImportError.

    The metadata says the package is installed, but a misconfigured
    namespace package or a broken extension could still raise on
    actual import. Treat that as "not part of analyzer identity"
    rather than crashing the cache-path constructor.
    """
    try:
        return __import__(module_name)
    except ImportError:  # pragma: no cover — defensive
        return None


def _hash_package_py_files(module: ModuleType) -> str:
    """Hash every ``.py`` file in a package's ``__path__`` deterministically.

    Iterates each path in sorted order, then ``.py`` files in
    sorted relative-path order so the result is byte-stable across
    processes. Skips ``__pycache__`` and any unreadable file.
    """
    h = hashlib.sha256()
    for pkg_path_str in sorted(
        str(p) for p in (getattr(module, "__path__", []) or [])
    ):
        pkg_path = Path(pkg_path_str).resolve()
        if not pkg_path.exists():
            continue
        py_files = sorted(pkg_path.rglob("*.py"))
        for py_file in py_files:
            try:
                rel = py_file.relative_to(pkg_path)
            except ValueError:  # pragma: no cover — defensive
                continue
            if "__pycache__" in rel.parts:
                continue
            h.update(str(rel).encode("utf-8"))
            h.update(b"\0")
            try:
                h.update(py_file.read_bytes())
            except OSError:  # pragma: no cover — defensive
                continue
            h.update(b"\0\0")
    return h.hexdigest()[:16]


def reset_cache_for_testing() -> None:
    """Reset the memoized hash. Tests-only escape hatch."""
    global _CACHED_HASH
    _CACHED_HASH = None
