# SPDX-License-Identifier: AGPL-3.0-or-later
"""Python dependency manifest parsing for ``pyproject.toml`` projects.

Parallel of ``jvm_deps.py`` for Python. Extracts declared dependencies from
``pyproject.toml`` so that ``ir.create_boundary_nodes`` can stamp an
unresolved Python import's boundary node ``meta["directness"]`` as a direct
dependency the project declares, or not. Every boundary node is tier 3
(ADR-0041 §1 made tier 3 the sole external tier and moved direct/transitive
onto the ``directness`` stamp); this module does not change tiers.

Why
---
Without a manifest, no Python boundary node can be called direct. On a
Python-heavy repo with hypergumbo's own pyproject (``click``, ``rich``,
``pydantic``, …) every direct dep would read the same as some deep transitive
thing — sketch / slice ranking and dead-code analysis can't distinguish the
project's own pinned deps from arbitrary externals (WI-nunuj).

Sources parsed
--------------
One list, :data:`PYPROJECT_DEPENDENCY_TABLES`, names every ``pyproject.toml``
table that declares dependencies, with the shape of its entries and the role
of the table. This module reads it from the parsed TOML to build the manifest,
and ``toml_config`` reads the same list from the syntax tree to emit one
``kind="dependency"`` node per entry, so the two readers cannot name different
tables (they did: the node reader knew one table and this one three).

==============================================  ============  =========
Table                                           Entries       Role
==============================================  ============  =========
``[project].dependencies``                      requirements  (runtime)
``[project.optional-dependencies].<extra>``     requirements  optional
``[dependency-groups].<group>`` (PEP 735)       requirements  dev
``[build-system].requires``                     requirements  build
``[tool.poetry.dependencies]``                  names         (runtime)
``[tool.poetry.dev-dependencies]``              names         dev
``[tool.poetry.group.<g>.dependencies]``        names         dev
``[tool.pdm.dev-dependencies].<group>``         requirements  dev
``[tool.uv].dev-dependencies``                  requirements  dev
``[tool.hatch.envs.<env>].dependencies``        requirements  dev
``[tool.hatch.envs.<env>].extra-dependencies``  requirements  dev
==============================================  ============  =========

``requirements`` is an array of PEP 508 strings; an entry that is not a
string (PEP 735 ``{include-group = "x"}``) or does not start with a
distribution name (PDM ``-e file:///...``) names no package. ``names`` is a
table whose keys are the distribution names, whatever the value (a version
string, ``{version = ..., extras = [...]}``, ``{git = ...}``, ``{path =
...}``, an array of constraints); the ``python`` key is the interpreter
constraint, not a library. The role is ``meta["dependency_scope"]`` on the
node: ``dev`` and ``build`` are the values ``json_config`` and the Cargo
tables already use, ``optional`` is new with these tables.

Not read, because the table declares no package of its own:
``[tool.uv.sources]`` and ``[tool.poetry.extras]`` (each names packages
another table declares), ``[tool.setuptools.dynamic]`` (the names are in the
file it points to), ``[tool.uv].constraint-dependencies`` /
``override-dependencies`` and ``[tool.pdm.resolution.overrides]`` (version
constraints on packages declared elsewhere).

Which tables open the manifest (:func:`parse_python_dependencies`)
------------------------------------------------------------------
An import that matches no entry is stamped ``undeclared`` only when the
manifest has entries at all, so an entry is also a claim that the project's
declarations were read. Only the published tables (the runtime ones and the
extras) make that claim. A ``dev`` or ``build`` table adds its names when some
published table produced one, and is otherwise left out: a ``setup.py``
project whose ``pyproject.toml`` holds nothing but ``[build-system].requires``
declares its runtime dependencies where this module does not look, and one
``setuptools`` entry would stamp every import of that project ``undeclared``.

Monorepo support (WI-zujip)
---------------------------
The walk collects every ``pyproject.toml`` under ``repo_root`` rather
than just the root file. Monorepo layouts where the root pyproject is
shared-tool-configuration only and actual ``[project].dependencies``
live in ``packages/<pkg>/pyproject.toml`` are first-class. Skips the
shared ``discovery.manifest_walk_skip()`` directory set (``DEFAULT_EXCLUDES``
plus ``node_modules`` / ``vendor``), dot-prefixed dirs, and directories a
discovery content rule claims (a virtualenv, Mix ``deps/``, …) so a
``pyproject.toml`` inside a virtualenv's site-packages cannot smuggle a fake
dep.
Mirrors :func:`hypergumbo_lang_mainstream.py._detect_source_roots`,
which fixed the same monorepo gap for file discovery (WI-davan E1).

Workspace-member subtraction (INV-nuzas, ADR-0041 §1): the walk also reads
each pyproject's OWN distribution name (``_extract_distribution_name``:
``[project].name``, else ``[tool.poetry].name``). Those names go through the
same dist-to-import resolution and are removed from the final set, after the
stdlib carve-out, so a monorepo sibling one package depends on (e.g.
``hypergumbo_core``) is treated as first-party source, never stamped as a
direct external dependency.

Distribution-name vs import-name resolution
-------------------------------------------
The dist name on PyPI doesn't always equal the Python import name —
``PyYAML`` → ``yaml``, ``beautifulsoup4`` → ``bs4``,
``scikit-learn`` → ``sklearn``. We use ``importlib.metadata.packages_distributions()``
to invert at parse time when the dev environment has the package
installed (the bakeoff context usually does). Unknown dist names fall
through to the dist name verbatim with hyphens swapped for underscores
— most deps match this anyway. A false negative stamps the import
``directness='undeclared'`` (a declared dependency reads as phantom)
whenever some pyproject produced entries; tier is unaffected.

Stdlib carve-out
----------------
The single-source ``python.yaml`` ``stdlib_modules`` catalog (ADR-0041 §3,
via ``io_boundary.load_catalog("python").is_stdlib_module``) filters the
resolved import-name set. A user who declares ``os`` (or any other stdlib
name) in ``pyproject.toml`` won't accidentally have it stamped direct. This is
the SAME stdlib recognizer the supply-chain ecosystem classifier uses — one
source, not the live interpreter's ``sys.stdlib_module_names`` (which would
be a second, version-drifting source the §3 single-source constraint forbids).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping, Optional, Sequence

from hypergumbo_core.installed_env import kept_per_environment
from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.supply_chain import DependencyManifest


def _load_pyproject(pyproject_path: Path) -> dict | None:
    """Parse a ``pyproject.toml`` file. Tomllib (3.11+) preferred, tomli as fallback."""
    try:
        content = pyproject_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        import tomllib
    except ImportError:  # pragma: no cover  # Python 3.10 fallback path
        try:
            import tomli as tomllib
        except ImportError:
            return None
    try:
        return tomllib.loads(content)
    except (ValueError, OSError):  # malformed TOML (TOMLDecodeError is a ValueError subclass)
        return None


#: Entry shape: an array of PEP 508 requirement strings.
SHAPE_REQUIREMENTS = "requirements"
#: Entry shape: a table whose keys are the distribution names (Poetry).
SHAPE_NAMES = "names"

#: A path element that matches any one key (an extra, a group, an environment).
ANY_KEY = "*"


@dataclass(frozen=True)
class DependencyTable:
    """One ``pyproject.toml`` table that declares dependencies.

    ``path`` is the key path from the document root, :data:`ANY_KEY` standing
    for one key of any name. ``shape`` is :data:`SHAPE_REQUIREMENTS` or
    :data:`SHAPE_NAMES`. ``scope`` is the role of the table, the value of
    ``meta["dependency_scope"]`` on its nodes: ``None`` for a runtime
    dependency, else ``optional`` / ``dev`` / ``build``.
    """

    path: tuple[str, ...]
    shape: str
    scope: Optional[str] = None

    @property
    def published(self) -> bool:
        """Whether the entries are in the built distribution's metadata
        (``Requires-Dist``): the runtime tables and the extras."""
        return self.scope in (None, "optional")

    def match(self, path: Sequence[str]) -> bool:
        """Whether the first ``len(self.path)`` keys of ``path`` are this table."""
        if len(path) < len(self.path):
            return False
        return all(
            want in (ANY_KEY, got)
            for want, got in zip(self.path, path, strict=False)
        )


#: Every table of a ``pyproject.toml`` that declares dependencies (see the
#: module docstring for the forms left out and why). The single source for
#: this module's manifest and for ``toml_config``'s dependency nodes.
PYPROJECT_DEPENDENCY_TABLES: tuple[DependencyTable, ...] = (
    DependencyTable(("project", "dependencies"), SHAPE_REQUIREMENTS),
    DependencyTable(
        ("project", "optional-dependencies", ANY_KEY), SHAPE_REQUIREMENTS, "optional"),
    DependencyTable(("dependency-groups", ANY_KEY), SHAPE_REQUIREMENTS, "dev"),
    DependencyTable(("build-system", "requires"), SHAPE_REQUIREMENTS, "build"),
    DependencyTable(("tool", "poetry", "dependencies"), SHAPE_NAMES),
    DependencyTable(("tool", "poetry", "dev-dependencies"), SHAPE_NAMES, "dev"),
    DependencyTable(
        ("tool", "poetry", "group", ANY_KEY, "dependencies"), SHAPE_NAMES, "dev"),
    DependencyTable(
        ("tool", "pdm", "dev-dependencies", ANY_KEY), SHAPE_REQUIREMENTS, "dev"),
    DependencyTable(("tool", "uv", "dev-dependencies"), SHAPE_REQUIREMENTS, "dev"),
    DependencyTable(
        ("tool", "hatch", "envs", ANY_KEY, "dependencies"), SHAPE_REQUIREMENTS, "dev"),
    DependencyTable(
        ("tool", "hatch", "envs", ANY_KEY, "extra-dependencies"),
        SHAPE_REQUIREMENTS, "dev"),
)

# PEP 508 ``name``: letters, digits, ``.``, ``_``, ``-``, starting and ending
# with a letter or digit. Whatever follows (extras, a version specifier, a
# marker, ``@ url``) is not part of the name.
_PEP508_NAME_RE = re.compile(r"\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


def pep508_name(requirement: str) -> Optional[str]:
    """The distribution name a PEP 508 requirement string starts with.

    ``click>=8.0; python_version >= '3.10'`` -> ``click``;
    ``uvicorn[standard]`` -> ``uvicorn``; ``pip @ git+https://...`` ->
    ``pip``. ``None`` when the string does not start with a name (an empty
    entry, PDM's ``-e file:///...``).
    """
    match = _PEP508_NAME_RE.match(requirement)
    return match.group(1) if match else None


def is_interpreter_constraint(name: str) -> bool:
    """Whether a key of a :data:`SHAPE_NAMES` table is Poetry's ``python``
    entry: the interpreter constraint, not a package."""
    return name.lower() == "python"


def _values_at(data: Any, path: Sequence[str]) -> Iterator[Any]:
    """Every value of the parsed TOML ``data`` at ``path`` (:data:`ANY_KEY`
    fans out over the keys of a table)."""
    if not path:
        yield data
        return
    if not isinstance(data, dict):
        return
    head, rest = path[0], path[1:]
    children = list(data.values()) if head == ANY_KEY else (
        [data[head]] if head in data else [])
    for child in children:
        yield from _values_at(child, rest)


def declared_distributions(data: dict) -> Iterator[tuple[DependencyTable, str]]:
    """Every ``(table, distribution name)`` a parsed ``pyproject.toml`` declares.

    One pass over :data:`PYPROJECT_DEPENDENCY_TABLES`; a value of the wrong
    type for its table's shape declares nothing.
    """
    for table in PYPROJECT_DEPENDENCY_TABLES:
        for value in _values_at(data, table.path):
            if table.shape == SHAPE_REQUIREMENTS:
                if not isinstance(value, list):
                    continue
                for entry in value:
                    name = pep508_name(entry) if isinstance(entry, str) else None
                    if name:
                        yield table, name
            elif isinstance(value, dict):
                for name in value:
                    if isinstance(name, str) and not is_interpreter_constraint(name):
                        yield table, name


def _extract_distribution_name(data: dict) -> str | None:
    """Read a package's OWN distribution name from its ``pyproject.toml``.

    PEP 621 ``[project].name`` is preferred; Poetry ``[tool.poetry].name``
    is the fallback. Returns ``None`` when neither is a non-empty string.

    Used for workspace-member subtraction (supply-verdict F3 / INV-nuzas,
    ADR-0041 §1). In a monorepo, a sibling package that another package
    declares as a dependency is first-party workspace source, not a
    third-party direct dependency. Collecting each package's own name lets
    :func:`parse_python_dependencies` remove the sibling from the declared
    dependency set instead of mislabelling in-repo code as a direct external
    dependency (the "direct dependency lie", leverage #27).
    """
    project = data.get("project")
    if isinstance(project, dict):
        name = project.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            name = poetry.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


_PEP503_NORMALIZE_RE = re.compile(r"[-_.]+")


def _normalize_dist_name(name: str) -> str:
    """PEP 503 distribution-name normalization: lower-case + collapse
    runs of ``-``, ``_``, ``.`` into a single ``-``.
    """
    return _PEP503_NORMALIZE_RE.sub("-", name).lower()


def _build_dist_to_import_map() -> Mapping[str, frozenset[str]]:
    """Invert ``importlib.metadata.packages_distributions()``.

    ``packages_distributions()`` returns ``{import_name: [dist_names]}``
    for installed distributions. We invert to ``{normalized_dist_name:
    {import_name, ...}}`` so a single PyPI dist (which can install
    multiple top-level import packages) maps to all of them.

    BUILT ONCE PER ENVIRONMENT. The call reads the metadata of every
    installed distribution (0.36 s for 250 of them on an idle machine, 0.86 s
    a call in a loaded test run) and ``parse_python_dependencies`` reaches it
    twice per run. The inverted map is kept by
    :func:`hypergumbo_core.installed_env.kept_per_environment` until a
    distribution is installed, upgraded or removed (checking that costs about
    a millisecond). The slot names ``packages_distributions`` itself, so a
    stand-in for it is never answered with the real environment's map, or
    the reverse. The map is read-only, so no caller can change what the next
    one gets.
    """
    try:
        from importlib.metadata import packages_distributions
    except ImportError:  # pragma: no cover  # Py <3.10 lacks the API
        return MappingProxyType({})

    def build() -> Mapping[str, frozenset[str]]:
        out: dict[str, set[str]] = {}
        for import_name, dists in packages_distributions().items():
            for dist in dists:
                out.setdefault(_normalize_dist_name(dist), set()).add(import_name)
        return MappingProxyType(
            {dist: frozenset(names) for dist, names in out.items()})

    return kept_per_environment(("dist-to-import", packages_distributions), build)


def _resolve_import_names(dist_names: set[str]) -> set[str]:
    """Map distribution names to importable top-level package names.

    Distribution-name → import-name is the asymmetric part: ``PyYAML``
    installs the ``yaml`` import; ``beautifulsoup4`` installs ``bs4``;
    ``scikit-learn`` installs ``sklearn``. When the dev environment has
    the dist installed, ``packages_distributions()`` gives us the real
    mapping; otherwise we fall back to the dist name verbatim (with
    hyphens swapped for underscores) — most deps match this anyway, and
    a false negative stamps the import ``undeclared`` (a declared
    dependency reads as phantom) rather than silently stamping something
    direct.
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
    ``discovery.manifest_walk_skip()`` names (``node_modules``, ``vendor``,
    ``dist``, …), dot-prefixed dirs and content-claimed dependency dirs
    (``walks_into``) so fake deps inside a vendored
    ``venv/lib/.../site-packages/<some-pkg>/pyproject.toml`` cannot leak
    into the manifest.

    Returns sorted by path for deterministic output.
    """
    from hypergumbo_core.discovery import manifest_walk_skip, walks_into

    skip = manifest_walk_skip()
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
            # INV-pivir: never into a directory symlink (a cycle is unbounded).
            elif walks_into(entry, skip):
                stack.append(entry)
    return sorted(out)


def parse_python_dependencies(repo_root: Path) -> DependencyManifest:
    """Parse every ``pyproject.toml`` in ``repo_root`` into a ``DependencyManifest``.

    Walks the tree to collect the root pyproject AND any per-package
    pyprojects (monorepo layouts under ``packages/<pkg>/pyproject.toml``,
    ``libs/<lib>/pyproject.toml``, etc., per WI-zujip). Returns an empty
    manifest when no pyproject is present anywhere.

    Stdlib module names are filtered out via the single-source
    ``python.yaml`` stdlib catalog (ADR-0041 §3,
    ``io_boundary.load_catalog("python").is_stdlib_module``) so a user who
    erroneously declares ``os`` (or any other stdlib name) in pyproject
    doesn't accidentally have it stamped direct. Same dependency declared by
    multiple packages collapses to one entry (set-union semantics).

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
    unpublished_dist_names: set[str] = set()
    workspace_dist_names: set[str] = set()
    for pyproject in pyproject_files:
        data = _load_pyproject(pyproject)
        if not isinstance(data, dict):
            continue
        for table, name in declared_distributions(data):
            (dist_names if table.published else unpublished_dist_names).add(name)
        own_name = _extract_distribution_name(data)
        if own_name:
            workspace_dist_names.add(own_name)

    # A dev or build table adds to a manifest; it does not open one (module
    # docstring, "Which tables open the manifest").
    if dist_names:
        dist_names |= unpublished_dist_names

    import_names = _resolve_import_names(dist_names)

    # Single-source stdlib carve-out (ADR-0041 §3 / WI-bifih): the python.yaml
    # ``stdlib_modules`` catalog is the ONE authoritative stdlib recognizer for
    # both the supply-chain/ecosystem classifier and this manifest filter,
    # replacing the prior second source (the live interpreter's
    # ``sys.stdlib_module_names``) so the two cannot drift.
    stdlib_catalog = load_catalog("python")
    import_names = {n for n in import_names if not stdlib_catalog.is_stdlib_module(n)}

    # Workspace-member subtraction (supply-verdict F3 / INV-nuzas, ADR-0041
    # §1). A monorepo sibling that another package declares as a dependency is
    # first-party workspace source, not a third-party direct dependency.
    # Resolve each package's own distribution name through the same
    # dist→import path and remove it, so the boundary classifier does not stamp
    # in-repo packages (``hypergumbo_core`` et al.) as external (tier-3)
    # dependency nodes. (Pre-ADR-0041 this read "external tier-2 direct
    # dependency"; ADR-0041 §1 made tier-3 the sole external tier and moved
    # direct/transitive onto the ``directness`` meta stamp.) Applied AFTER the
    # stdlib carve-out so the final
    # resolved import-name set is filtered.
    workspace_import_names = _resolve_import_names(workspace_dist_names)
    import_names -= workspace_import_names

    for name in import_names:
        entries[name] = {"direct": True}

    return DependencyManifest(entries=entries)
