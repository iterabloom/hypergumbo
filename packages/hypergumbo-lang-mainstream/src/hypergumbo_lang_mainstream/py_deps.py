# SPDX-License-Identifier: AGPL-3.0-or-later
"""Python dependency manifest parsing for ``pyproject.toml`` projects.

Parallel of ``jvm_deps.py`` for Python. Extracts declared dependencies from
``pyproject.toml`` so that ``ir.create_boundary_nodes`` can classify
unresolved Python imports as tier 2 (direct dependency the project
declares) vs tier 3 (indirect / stdlib / unknown).

Why
---
Without a manifest, every Python boundary node falls back to tier 3.
On a Python-heavy repo with hypergumbo's own pyproject (``click``,
``rich``, ``pydantic``, …) every direct dep is the same tier-3 as some
deep transitive thing — sketch / slice ranking and dead-code analysis
can't distinguish the project's own pinned deps from arbitrary externals
(WI-nunuj).

Sources parsed
--------------
1. ``[project].dependencies`` (PEP 621).
2. ``[project.optional-dependencies]`` (PEP 621 extras — flattened, all
   direct).
3. ``[tool.poetry.dependencies]`` (Poetry convention; the special
   ``python`` key is excluded — it's the interpreter constraint, not a
   library dep).

Monorepo support (WI-zujip)
---------------------------
The walk collects every ``pyproject.toml`` under ``repo_root`` rather
than just the root file. Monorepo layouts where the root pyproject is
shared-tool-configuration only and actual ``[project].dependencies``
live in ``packages/<pkg>/pyproject.toml`` are first-class. Skips the
shared ``DEFAULT_EXCLUDES`` directory set (``node_modules``, ``venv``,
``dist``, …) and dot-prefixed dirs so a ``pyproject.toml`` inside a
``.venv/`` site-packages directory cannot smuggle a fake dep.
Mirrors :func:`hypergumbo_lang_mainstream.py._detect_source_roots`,
which fixed the same monorepo gap for file discovery (WI-davan E1).

Distribution-name vs import-name resolution
-------------------------------------------
The dist name on PyPI doesn't always equal the Python import name —
``PyYAML`` → ``yaml``, ``beautifulsoup4`` → ``bs4``,
``scikit-learn`` → ``sklearn``. We use ``importlib.metadata.packages_distributions()``
to invert at parse time when the dev environment has the package
installed (the bakeoff context usually does). Unknown dist names fall
through to the dist name verbatim with hyphens swapped for underscores
— most deps match this anyway, and a false-negative just lands in
tier 3, the current default (no regression).

Stdlib carve-out
----------------
``sys.stdlib_module_names`` (Python 3.10+) is used to filter the resolved
import-name set. A user who declares ``os`` (or any other stdlib name)
in ``pyproject.toml`` won't accidentally promote it to tier 2.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from hypergumbo_core.supply_chain import DependencyManifest


def _load_pyproject(pyproject_path: Path) -> dict | None:
    """Parse a ``pyproject.toml`` file. Tomllib (3.11+) preferred, tomli as fallback."""
    try:
        content = pyproject_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        import tomllib
        return tomllib.loads(content)
    except ImportError:  # pragma: no cover  # Python 3.10 path; no tomli on this runner
        try:  # pragma: no cover
            import tomli  # pragma: no cover
            return tomli.loads(content)  # pragma: no cover
        except ImportError:  # pragma: no cover
            return None
    except (ValueError, OSError):  # malformed TOML
        return None


# Specifier separators per PEP 440 / PEP 508. Order matters: longer
# tokens first so ``>=`` is found before ``>``.
_SPECIFIER_SEPS = (">=", "<=", "==", "~=", "!=", "===", ";", "[", "<", ">")


def _strip_specifier(dep: str) -> str:
    """Reduce a PEP 508 dep string like ``click>=8.0; python_version >= '3.10'``
    to its bare distribution name (``click``).
    """
    for sep in _SPECIFIER_SEPS:
        if sep in dep:
            dep = dep.split(sep, 1)[0]
    return dep.strip()


def _extract_pep621_distribution_names(data: dict) -> set[str]:
    """Read PEP 621 ``[project].dependencies`` and ``[project.optional-dependencies]``."""
    out: set[str] = set()
    project = data.get("project")
    if not isinstance(project, dict):
        return out
    deps = project.get("dependencies")
    if isinstance(deps, list):
        for dep in deps:
            if isinstance(dep, str):
                name = _strip_specifier(dep)
                if name:
                    out.add(name)
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for dep_list in optional.values():
            if isinstance(dep_list, list):
                for dep in dep_list:
                    if isinstance(dep, str):
                        name = _strip_specifier(dep)
                        if name:
                            out.add(name)
    return out


def _extract_poetry_distribution_names(data: dict) -> set[str]:
    """Read ``[tool.poetry.dependencies]``. The ``python`` key is the
    interpreter constraint, not a library — skip it.
    """
    out: set[str] = set()
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return out
    poetry = tool.get("poetry")
    if not isinstance(poetry, dict):
        return out
    poetry_deps = poetry.get("dependencies")
    if not isinstance(poetry_deps, dict):
        return out
    for name in poetry_deps:
        if isinstance(name, str) and name.lower() != "python":
            out.add(name)
    return out


_PEP503_NORMALIZE_RE = re.compile(r"[-_.]+")


def _normalize_dist_name(name: str) -> str:
    """PEP 503 distribution-name normalization: lower-case + collapse
    runs of ``-``, ``_``, ``.`` into a single ``-``.
    """
    return _PEP503_NORMALIZE_RE.sub("-", name).lower()


def _build_dist_to_import_map() -> dict[str, set[str]]:
    """Invert ``importlib.metadata.packages_distributions()``.

    ``packages_distributions()`` returns ``{import_name: [dist_names]}``
    for installed distributions. We invert to ``{normalized_dist_name:
    {import_name, ...}}`` so a single PyPI dist (which can install
    multiple top-level import packages) maps to all of them.
    """
    out: dict[str, set[str]] = {}
    try:
        from importlib.metadata import packages_distributions
        mapping = packages_distributions()
    except (ImportError, AttributeError):  # pragma: no cover  # Py <3.10 lacks the API
        return out
    for import_name, dists in mapping.items():
        for dist in dists:
            out.setdefault(_normalize_dist_name(dist), set()).add(import_name)
    return out


def _resolve_import_names(dist_names: set[str]) -> set[str]:
    """Map distribution names to importable top-level package names.

    Distribution-name → import-name is the asymmetric part: ``PyYAML``
    installs the ``yaml`` import; ``beautifulsoup4`` installs ``bs4``;
    ``scikit-learn`` installs ``sklearn``. When the dev environment has
    the dist installed, ``packages_distributions()`` gives us the real
    mapping; otherwise we fall back to the dist name verbatim (with
    hyphens swapped for underscores) — most deps match this anyway, and
    a false negative falls through to the tier-3 default rather than
    silently promoting something to tier 2.
    """
    dist_to_import = _build_dist_to_import_map()
    out: set[str] = set()
    for name in dist_names:
        normalized = _normalize_dist_name(name)
        imports = dist_to_import.get(normalized)
        if imports:
            out |= imports
        else:
            # Fallback: dist name with hyphens normalised to underscores.
            out.add(name.replace("-", "_"))
    return out


def _find_pyproject_files(repo_root: Path) -> list[Path]:
    """Walk ``repo_root`` and return every ``pyproject.toml`` path.

    Monorepo support: collects the root pyproject AND every
    ``packages/<pkg>/pyproject.toml`` (or any depth). Skips
    ``DEFAULT_EXCLUDES`` directories (``node_modules``, ``venv``,
    ``dist``, …) and dot-prefixed dirs so fake deps inside a vendored
    ``.venv/site-packages/<some-pkg>/pyproject.toml`` cannot leak
    into the manifest.

    Returns sorted by path for deterministic output.
    """
    from hypergumbo_core.discovery import DEFAULT_EXCLUDES

    skip = set(DEFAULT_EXCLUDES)
    out: list[Path] = []
    stack: list[Path] = [repo_root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except (PermissionError, OSError):  # pragma: no cover
            continue
        for entry in entries:
            if entry.is_file() and entry.name == "pyproject.toml":
                out.append(entry)
            elif entry.is_dir():
                if entry.name in skip or entry.name.startswith("."):
                    continue
                stack.append(entry)
    return sorted(out)


def parse_python_dependencies(repo_root: Path) -> DependencyManifest:
    """Parse every ``pyproject.toml`` in ``repo_root`` into a ``DependencyManifest``.

    Walks the tree to collect the root pyproject AND any per-package
    pyprojects (monorepo layouts under ``packages/<pkg>/pyproject.toml``,
    ``libs/<lib>/pyproject.toml``, etc., per WI-zujip). Returns an empty
    manifest when no pyproject is present anywhere.

    Stdlib module names are filtered out via ``sys.stdlib_module_names``
    (Python 3.10+) so a user who erroneously declares ``os`` (or any
    other stdlib name) in pyproject doesn't accidentally promote it to
    tier 2. Same dependency declared by multiple packages collapses to
    one entry (set-union semantics).

    Returns:
        ``DependencyManifest`` mapping importable top-level names to
        ``{"direct": True}`` entries. Suitable for direct merge with
        other-language manifests via ``DependencyManifest.merge``.
    """
    entries: dict[str, dict] = {}

    pyproject_files = _find_pyproject_files(repo_root)
    if not pyproject_files:
        return DependencyManifest(entries=entries)

    dist_names: set[str] = set()
    for pyproject in pyproject_files:
        data = _load_pyproject(pyproject)
        if not isinstance(data, dict):
            continue
        dist_names |= _extract_pep621_distribution_names(data)
        dist_names |= _extract_poetry_distribution_names(data)

    import_names = _resolve_import_names(dist_names)

    stdlib_names = getattr(sys, "stdlib_module_names", frozenset())
    import_names = {n for n in import_names if n not in stdlib_names}

    for name in import_names:
        entries[name] = {"direct": True}

    return DependencyManifest(entries=entries)
