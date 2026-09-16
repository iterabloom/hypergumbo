#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Grep terms for ``scripts/smart-test``'s catalogue gate, derived per family.

WHAT WENT WRONG. ``smart-test`` unions the tests that read a changed catalogue
YAML so that moving a row between I/O boundaries runs the io-boundary suite
instead of the two tests the same commit happened to add (INV-muvis). The union
worked. Its KEY did not: it was a regex naming ``io_primitives`` and
``io_primitives_overlays``, which is 23 of the 169 YAML files the wheel ships.
Measured on dev 81f1a69f88, a change confined to ``frameworks/`` (107 files),
``dataflow_patterns/``, ``cfg_nodes/``, ``function_summaries/`` or
``taint_sources/`` selected ZERO tests — smart-test then wrote a 0-test
manifest, printed "no test-relevant files changed (docs/config only)", and
``ci.yml`` skipped pytest. A fix keyed to one instance left the class, and the
message asserted the change was config when it was the framework pattern layer
(INV-bigaz, INV-dohoj).

WHY THE TERMS ARE NOT LISTED HERE. A roster of ten directories and their
loaders would be a second home for a fact that already has one:
``hypergumbo_core.yaml_catalogs.YAML_CATALOGS`` names every catalogue
directory and the module that consumes it, and ``validate_registry()`` — gated
against the live tree by ``test_yaml_catalogs.py`` — refuses both a registered
directory that has vanished and an on-disk YAML directory nobody registered.
So the registry cannot silently fall behind the tree, and deriving from it
means the eleventh family is covered by the commit that registers it rather
than by an edit here.

HOW A FAMILY BECOMES GREP TERMS. For each changed path under
``packages/<pkg>/src/<module>/<directory>/`` ending in ``.yaml``/``.yml``:

1. ``<directory>`` — the family's own name, which every test naming the
   catalogue it exercises will contain.
2. the LOADER MODULE's basename, from the registry (``io_primitives`` ->
   ``hypergumbo_core.io_boundary`` -> ``io_boundary``). This is the
   load-bearing one and it is not obvious: measured on the live tree, the
   directory name alone reaches 58 test files and the loader module reaches
   139. A test that exercises classification without ever naming a catalogue
   is invisible to arm 1 and caught by arm 2.
3. every ``load_*`` function the loader module defines, read out of its source.
   This is what keeps the change non-narrowing: the old selector's third
   literal was ``load_catalog``, and deriving the loader's entry points covers
   it for every family rather than for the one someone wrote down.

ABSENT IS NOT EMPTY. A directory the registry does not know still yields arm 1,
and a registry that cannot be read or parsed degrades to arm 1 for everything
rather than to silence — the failure mode of a selector is under-selection, and
under-selection here is a green tick over a hole. Over-select, never
under-select, is the same rule the doc and playbook unions beside it follow.

CLI: ``catalogue_gate_terms.py REPO_ROOT [--registry PATH] < CHANGED``.
Reads newline-separated repo-relative changed paths from stdin, writes the
grep terms one per line, sorted and deduplicated, to stdout. Paths that are not
catalogue YAML contribute nothing, so an empty stdout means "no catalogue
changed" and the caller skips the union entirely. Always exits 0 unless argv is
malformed.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, Optional, Set

#: ``packages/<pkg>/src/<module>/<directory>/...yaml`` — the shape every
#: shipped catalogue has. Anchored at ``src`` so a test fixture YAML under
#: ``packages/<pkg>/tests/`` is not mistaken for shipped data.
CATALOGUE_YAML = re.compile(
    r"^packages/[^/]+/src/[^/]+/(?P<directory>[^/]+)/.*\.ya?ml$"
)

#: Where the registry lives, relative to the repo root.
DEFAULT_REGISTRY = Path(
    "packages/hypergumbo-core/src/hypergumbo_core/yaml_catalogs.py"
)

#: The loader entry points worth grepping for. ``load_`` rather than every
#: public name because a test that calls the loader names the loader; a test
#: that merely imports the module is already caught by the module arm.
_LOADER_FUNCTION = re.compile(r"^def (load_\w+)", re.MULTILINE)


def changed_directories(paths: Iterable[str]) -> Set[str]:
    """Catalogue directory names touched by ``paths``."""
    found: Set[str] = set()
    for raw in paths:
        path = raw.strip()
        if not path:
            continue
        match = CATALOGUE_YAML.match(path)
        if match:
            found.add(match.group("directory"))
    return found


def read_registry(registry: Path) -> Dict[str, str]:
    """Map catalogue directory -> loader dotted module, parsed not imported.

    ``ast`` rather than ``import`` on purpose: this runs from a shell script
    during selection, before anything has established that the package is
    installed or importable, and a registry read must not be able to execute
    the package's ``__init__``. A registry that is missing, unparseable, or
    shaped differently than expected yields ``{}``, and the caller degrades to
    the directory-name arm.
    """
    try:
        tree = ast.parse(registry.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return {}

    specs: Dict[str, str] = {}
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if not any(
            isinstance(t, ast.Name) and t.id == "YAML_CATALOGS" for t in targets
        ):
            continue
        for element in ast.walk(node):
            if not isinstance(element, ast.Call):
                continue
            fields = {
                kw.arg: kw.value.value
                for kw in element.keywords
                if kw.arg
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            }
            directory = fields.get("directory")
            loader = fields.get("loader")
            if directory and loader:
                specs[directory] = loader
    return specs


def _loader_source(package_src: Path, loader: str) -> Optional[Path]:
    """The file that defines ``loader``, as a module or as a package."""
    relative = Path(*loader.split("."))
    for candidate in (
        package_src / relative.with_suffix(".py"),
        package_src / relative / "__init__.py",
    ):
        if candidate.is_file():
            return candidate
    return None


def loader_terms(package_src: Path, loader: str) -> Set[str]:
    """The module basename plus every ``load_*`` entry point it defines."""
    terms = {loader.rsplit(".", 1)[-1]}
    source = _loader_source(package_src, loader)
    if source is not None:
        try:
            text = source.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - unreadable file on a readable path
            return terms
        terms |= set(_LOADER_FUNCTION.findall(text))
    return terms


def sibling_directories(specs: Dict[str, str], directory: str) -> Set[str]:
    """Registered families that share ``directory``'s loader module.

    Two families read by the same loader are read by the same code path, so a
    row change in one can break a test that names the other — and the pairs
    are real rather than hypothetical: ``io_primitives_overlays`` layers onto
    ``io_primitives`` through ``io_boundary``, and ``taint_sources`` /
    ``taint_sanitizers`` are two halves of one catalogue behind ``taint``.
    Without this the widening would NARROW the overlay arm, because the old
    selector fired one shared rule for both io_primitives directories and the
    literal ``io_primitives`` does not appear in every test that reads them.
    """
    loader = specs.get(directory)
    if not loader:
        return set()
    return {other for other, lo in specs.items() if lo == loader}


def gate_terms(repo_root: Path, registry: Path, paths: Iterable[str]) -> Set[str]:
    """Every grep term the catalogue gate should union for ``paths``."""
    directories = changed_directories(paths)
    if not directories:
        return set()
    specs = read_registry(registry)
    package_src = registry.parent.parent
    terms: Set[str] = set(directories)
    for directory in directories:
        loader = specs.get(directory)
        if loader:
            terms |= loader_terms(package_src, loader)
            terms |= sibling_directories(specs, directory)
    return terms


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repo_root", help="repository root")
    parser.add_argument(
        "--registry",
        default=None,
        help=f"catalogue registry (default: {DEFAULT_REGISTRY})",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root)
    registry = (
        Path(args.registry)
        if args.registry
        else repo_root / DEFAULT_REGISTRY
    )
    terms = gate_terms(repo_root, registry, sys.stdin.read().splitlines())
    for term in sorted(terms):
        print(term)
    return 0


if __name__ == "__main__":
    sys.exit(main())
