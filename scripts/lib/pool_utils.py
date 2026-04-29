# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared pool-walking utilities for bakeoff and prospector scripts.

The agent-facing ``--pool`` argument can point at either:

- A flat directory of repos (legacy, e.g., ``~/whole_bunch_of_repos/``), or
- A curated catalog of collections-of-repos (e.g., ``~/ALL_REPOS/``), where
  most top-level entries are themselves directories of repos and several
  are symlinks.

These utilities discover candidate repos transparently, descending one level
into entries that don't look like a single repo. Symlinks are followed
(``os.scandir`` and ``os.path.isdir`` follow them by default), so a top-level
symlink to either a single repo or a collection works without special-casing.

A candidate is treated as "a single repo" if either:

- It contains a ``.git/`` directory at its top level (most cloned repos), or
- It contains source-shaped or manifest-shaped files at its top level
  (``.py``, ``.go``, ``Cargo.toml``, ``package.json``, ``Makefile``, etc.).

If neither, the entry is treated as a collection and its immediate children
are descended into. Recursion is bounded at depth=1: collections-of-
collections are not handled — in practice ``~/ALL_REPOS`` is exactly one
level deep, and pools structured otherwise should be flattened by the user.

This module is import-clean (no side effects beyond defining names), so
multiple scripts can import it safely. It's intentionally unaware of
bakeoff-state semantics so the dead-code prospector and other tools can use
it without dragging session-init details along.
"""

import os
from typing import Iterator, Optional

# Source-shaped extensions used for the "this is a repo" heuristic. Drawn
# from the same vocabulary the bakeoff scripts use elsewhere for language
# detection. Not exhaustive — this is "common enough that it would always
# be present in a real repo's top-level files", which is the only property
# that matters for the heuristic.
_REPO_SOURCE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".c", ".cpp", ".cc",
    ".h", ".hpp", ".java", ".kt", ".scala", ".rb", ".php", ".swift", ".cs",
    ".fs", ".ex", ".exs", ".erl", ".hrl", ".hs", ".lhs", ".ml", ".mli",
    ".lua", ".pl", ".pm", ".sh", ".bash", ".zsh", ".dart", ".sol", ".nim",
    ".d", ".lean", ".tla",
    ".toml", ".yaml", ".yml", ".lock", ".gemspec", ".cabal",
}

# Manifest filenames (no extension or non-standard extension) commonly found
# at repo roots. Matched by exact basename.
_ROOT_MANIFEST_NAMES = frozenset({
    "Cargo.toml", "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "go.mod", "Gemfile", "Makefile", "CMakeLists.txt", "Dockerfile",
    "README", "README.md", "README.rst",
    "LICENSE", "LICENSE.md",
})


def is_repo_root(path: str) -> bool:
    """Heuristic: does ``path`` look like a single repo (vs a collection)?

    Returns ``True`` if either:
    - ``path/.git/`` exists, or
    - any immediate child file matches the source/manifest heuristic.

    Returns ``False`` otherwise — caller should treat as a collection and
    descend, or skip the path entirely.

    On permission errors during the top-level scan, returns ``False``
    conservatively (we'd rather miss a repo than treat an inaccessible
    directory as one and crash later).
    """
    if not os.path.isdir(path):
        return False
    if os.path.isdir(os.path.join(path, ".git")):
        return True
    try:
        for entry in os.scandir(path):
            if not entry.is_file(follow_symlinks=True):
                continue
            name = entry.name
            if name in _ROOT_MANIFEST_NAMES:
                return True
            ext = os.path.splitext(name)[1].lower()
            if ext in _REPO_SOURCE_EXTS:
                return True
    except (PermissionError, OSError):
        return False
    return False


def _canonical(path: str) -> str:
    """Return the canonical (symlink-resolved) absolute path, or the input
    on error. Used for deduplication so a flat symlink and the underlying
    real directory don't both yield the same repo."""
    try:
        return os.path.realpath(path)
    except OSError:  # pragma: no cover - defensive; realpath rarely raises
        return path


def _iter_repos_recursive(
    path: str, depth: int, max_depth: int, seen: set,
) -> Iterator[os.DirEntry]:
    """Yield candidate repo DirEntry objects under ``path``, descending up
    to ``max_depth`` levels into directories that don't look like repos.
    ``seen`` is a shared set of canonical paths used for deduplication
    across the entire traversal.
    """
    try:
        entries = list(os.scandir(path))
    except (PermissionError, OSError, FileNotFoundError):
        return
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if not entry.is_dir():
            continue
        canon = _canonical(entry.path)
        if canon in seen:
            continue
        seen.add(canon)
        if is_repo_root(entry.path):
            yield entry
        elif depth < max_depth:
            yield from _iter_repos_recursive(
                entry.path, depth + 1, max_depth, seen,
            )


def iter_pool_repos(
    pool_path: str, *, max_depth: int = 2,
) -> Iterator[os.DirEntry]:
    """Yield candidate repo ``DirEntry`` objects under ``pool_path``.

    For each entry under ``pool_path`` (recursively, up to ``max_depth``
    additional levels into non-repo directories):

    - Skip hidden entries (names starting with ``.``).
    - Skip non-directory entries (loose files).
    - If the entry looks like a repo (per ``is_repo_root``), yield it.
    - Otherwise (treated as a collection), recurse if depth budget remains.

    ``max_depth`` defaults to 2 to handle pools like ``~/ALL_REPOS/`` where
    a top-level catalog entry can be a collection-of-collections (e.g.,
    ``plazaflow_deep_repos/`` containing both flat repo-symlinks and
    ``cohort_*/`` subdirectories of repos). ``max_depth=0`` disables
    recursion entirely.

    Yielded results are deduplicated by canonical (``realpath``) path, so
    a flat symlink and the underlying nested real directory don't both
    produce the same repo.

    Symlinks at any level are followed transparently (``DirEntry.is_dir()``
    defaults to ``follow_symlinks=True``).

    Permission errors at the pool root yield nothing. Permission errors
    inside a single sub-collection skip that sub-collection but don't
    abort the iteration.
    """
    yield from _iter_repos_recursive(pool_path, 0, max_depth, set())


def _search_collections(
    parent: str, name: str, depth: int, max_depth: int,
) -> Optional[str]:
    """Search ``parent`` for a collection containing a directory named
    ``name``. Recurses through non-repo directories up to ``max_depth``
    additional levels. Returns the matched path or None.
    """
    try:
        entries = list(os.scandir(parent))
    except (PermissionError, OSError):
        return None
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if not entry.is_dir():
            continue
        # Skip entries that are themselves repos — we'd never look up an
        # explicit repo name inside another repo.
        if is_repo_root(entry.path):
            continue
        candidate = os.path.join(entry.path, name)
        if os.path.isdir(candidate):
            return candidate
        if depth < max_depth:
            found = _search_collections(
                entry.path, name, depth + 1, max_depth,
            )
            if found is not None:
                return found
    return None


def resolve_repo_path(
    pool_path: str, name: str, *, max_depth: int = 2,
) -> Optional[str]:
    """Resolve an explicit repo ``name`` against ``pool_path``.

    Tries:

    1. Direct: ``pool_path/name``. Returns its absolute path if it's a
       directory.
    2. If direct doesn't exist and ``max_depth >= 1`` and ``name`` contains
       no slash, search through non-repo top-level entries (treated as
       collections) for a sub-path ending in ``name``. Recurses up to
       ``max_depth`` additional levels into nested collections.

    Names containing a slash are always tried as direct joins only —
    ``os.path.join`` is slash-aware, so callers can already pass
    ``coll/repo`` if they want to disambiguate manually.

    Returns the resolved absolute path or ``None`` if not found.
    """
    direct = os.path.join(pool_path, name)
    if os.path.isdir(direct):
        return direct
    if max_depth < 1 or "/" in name:
        return None
    return _search_collections(pool_path, name, 1, max_depth)
