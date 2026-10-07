# SPDX-License-Identifier: AGPL-3.0-or-later
"""npm dependency manifest parsing (``package.json`` + lockfiles).

Parallel of ``py_deps.py`` / ``jvm_deps.py`` for JavaScript/TypeScript
(WI-juzaj). Builds the ``"npm"`` scoped table of a :class:`DependencyManifest`
so ``ir.create_boundary_nodes`` can stamp a JS/TS boundary node (an import of
``leaflet``, ``@peertube/embed-api``, ``tailwindcss/plugin``) ``direct`` or
``transitive``. Before this, the only language-agnostic manifests read were
go.mod, Gradle/Maven and pyproject.toml, and every JS boundary node read
``directness=unknown``.

Sources
-------
* Direct: the keys of ``dependencies``, ``devDependencies``,
  ``peerDependencies`` and ``optionalDependencies`` of every
  ``package.json``. All four are declarations the project made; which one a
  package sits in is a build/runtime distinction, not a directness one.
* Transitive: every other package named in a lockfile --
  ``package-lock.json`` / ``npm-shrinkwrap.json`` (v2/v3 ``packages`` keys
  ``node_modules/<name>``, nested ``node_modules/a/node_modules/b``; v1
  recursive ``dependencies``), ``yarn.lock`` (classic and Berry entry
  headers ``"name@range", name@range:``) and ``pnpm-lock.yaml``
  (``packages:`` / ``snapshots:`` keys across the ``/name/1.0.0``,
  ``/name@1.0.0`` and ``name@1.0.0(peer@x)`` generations). Lockfiles are
  read with line/regex scanning rather than a YAML parser -- only the entry
  keys are needed, and that keeps pnpm locks free of a PyYAML dependency.
* Neither: each ``package.json``'s own ``name`` (workspace siblings,
  ADR-0041 D8a, as ``py_deps`` subtracts pyproject names), and any dependency
  whose version is a ``workspace:`` / ``file:`` / ``link:`` reference --
  in-repo source, not a registry package.

The specifier -> package rule (``@scope/name/sub`` -> ``@scope/name``,
``lodash/fp`` -> ``lodash``) lives in ``supply_chain.npm_specifier_package``,
next to the manifest that applies it. Node built-ins (``fs``; the analyzer
strips ``node:``) match no declared package and get no directness stamp;
classifying them ``stdlib`` belongs to the single-source stdlib catalog
(ADR-0041 §3), not to this reader.

Walk
----
Every manifest under the repo, skipping ``discovery.manifest_walk_skip()``
names (``node_modules`` among them), dot-dirs and content-claimed dependency
directories, so a vendored package's own ``package.json`` cannot declare
dependencies on the project's behalf.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hypergumbo_core.supply_chain import DependencyManifest

_DEP_SECTIONS = (
    "dependencies", "devDependencies", "peerDependencies", "optionalDependencies",
)
_LOCAL_VERSION_PREFIXES = ("workspace:", "file:", "link:", "portal:")
_PACKAGE_LOCKS = ("package-lock.json", "npm-shrinkwrap.json")
_MANIFEST_NAMES = frozenset(
    {"package.json", "yarn.lock", "pnpm-lock.yaml", *_PACKAGE_LOCKS}
)
_PNPM_KEY_RE = re.compile(r"^  (['\"]?)(/?[^\s'\"]+)\1:\s*$")


def _split_name_version(spec: str) -> str | None:
    """``@scope/name@1.2`` -> ``@scope/name``; ``name@^1`` -> ``name``."""
    spec = spec.strip().strip("'\"")
    at = spec.find("@", 1) if spec.startswith("@") else spec.find("@")
    name = spec[:at] if at > 0 else spec
    return name or None


def parse_package_json(data: Any) -> tuple[set[str], str | None]:
    """Return ``(direct deps, own name)`` from a parsed ``package.json``."""
    direct: set[str] = set()
    own: str | None = None
    if not isinstance(data, dict):
        return direct, own
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        own = name.strip()
    for section in _DEP_SECTIONS:
        deps = data.get(section)
        if not isinstance(deps, dict):
            continue
        for dep, version in deps.items():
            if not isinstance(dep, str) or not dep:
                continue
            if isinstance(version, str) and version.startswith(_LOCAL_VERSION_PREFIXES):
                continue
            direct.add(dep)
    return direct, own


def _v1_dependency_names(deps: Any, out: set[str]) -> None:
    """Recurse a lockfile-v1 ``dependencies`` tree collecting package names."""
    if not isinstance(deps, dict):
        return
    for name, info in deps.items():
        if isinstance(name, str) and name:
            out.add(name)
        if isinstance(info, dict):
            _v1_dependency_names(info.get("dependencies"), out)


def parse_package_lock(data: Any) -> set[str]:
    """Package names in a ``package-lock.json`` / ``npm-shrinkwrap.json``."""
    names: set[str] = set()
    if not isinstance(data, dict):
        return names
    packages = data.get("packages")
    if isinstance(packages, dict):
        for key in packages:
            if isinstance(key, str) and "node_modules/" in key:
                names.add(key.rsplit("node_modules/", 1)[1])
    _v1_dependency_names(data.get("dependencies"), names)
    return names


def parse_yarn_lock(src: str) -> set[str]:
    """Package names from ``yarn.lock`` entry headers (classic and Berry).

    A header is an unindented line ending in ``:`` listing one or more
    ``name@range`` descriptors separated by ``, ``. Berry's
    ``__metadata:`` block is skipped.
    """
    names: set[str] = set()
    for line in src.splitlines():
        if not line or line[0] in " \t#" or not line.rstrip().endswith(":"):
            continue
        header = line.rstrip()[:-1]
        for descriptor in header.split(","):
            name = _split_name_version(descriptor)
            if name and name != "__metadata":
                names.add(name)
    return names


def parse_pnpm_lock(src: str) -> set[str]:
    """Package names from the ``packages:`` / ``snapshots:`` keys of a pnpm lock."""
    names: set[str] = set()
    in_section = False
    for line in src.splitlines():
        if line and not line[0].isspace():
            in_section = line.rstrip() in ("packages:", "snapshots:")
            continue
        if not in_section:
            continue
        match = _PNPM_KEY_RE.match(line)
        if match is None:
            continue
        key = match.group(2).split("(", 1)[0].lstrip("/")
        # lockfile v5 puts the version in its own path segment
        # (``/name/1.0.0``, ``/@scope/name/1.0.0_peer@1``); later
        # generations join it with ``@`` (``/name@1.0.0``, ``name@1.0.0``).
        parts = key.split("/")
        width = 2 if key.startswith("@") else 1
        if len(parts) > width:
            name: str | None = "/".join(parts[:width])
        else:
            name = _split_name_version(key)
        if name:
            names.add(name)
    return names


def _find_npm_files(repo_root: Path) -> list[Path]:
    """Walk ``repo_root`` for package.json and lockfiles, sorted."""
    from hypergumbo_core.discovery import manifest_walk_skip, walks_into

    skip = manifest_walk_skip()
    out: list[Path] = []
    stack: list[Path] = [repo_root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except OSError:  # pragma: no cover  # unreadable dir mid-walk
            continue
        for entry in entries:
            if entry.is_file():
                if entry.name in _MANIFEST_NAMES:
                    out.append(entry)
            elif walks_into(entry, skip):
                stack.append(entry)
    return sorted(out)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def parse_npm_dependencies(repo_root: Path) -> DependencyManifest:
    """Parse every npm manifest/lockfile under ``repo_root``.

    Returns a manifest whose ``scoped["npm"]`` maps package names to
    ``{"direct": bool}``, or an empty manifest when no npm project exists.
    """
    direct: set[str] = set()
    own_names: set[str] = set()
    locked: set[str] = set()
    for path in _find_npm_files(repo_root):
        if path.name == "package.json":
            deps, own = parse_package_json(_load_json(path))
            direct |= deps
            if own:
                own_names.add(own)
        elif path.name in _PACKAGE_LOCKS:
            locked |= parse_package_lock(_load_json(path))
        else:
            src = _read_text(path)
            if src is None:
                continue
            if path.name == "yarn.lock":
                locked |= parse_yarn_lock(src)
            else:
                locked |= parse_pnpm_lock(src)

    table: dict[str, dict[str, bool]] = {}
    for name in locked - direct - own_names:
        table[name] = {"direct": False}
    for name in direct - own_names:
        table[name] = {"direct": True}
    if not table:
        return DependencyManifest()
    return DependencyManifest(scoped={"npm": table})
