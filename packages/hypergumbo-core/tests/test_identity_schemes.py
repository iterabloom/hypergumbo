# SPDX-License-Identifier: AGPL-3.0-or-later
"""The identity scheme constants live in a leaf module, and ``schema.py`` is
not loaded by the files every test loads.

smart-test adds, for a source file whose import-time code changed, every test
file whose imports reach it (``import_closure``). The root ``conftest.py``
loads the core package, the package loads ``ir`` and ``analyze/base``, and
both need the label of a stable id. While they took it from ``schema.py``,
every test file loaded ``schema.py`` and a change to any constant in it (a
schema version bump) selected the whole suite. The constants they need are in
``identity_schemes.py``, which imports nothing from the package.

These tests pin three things: the leaf is a leaf; the files every test loads
do not import ``schema.py`` when they are imported; and ``schema.py`` still
offers the same objects, so no consumer sees a second copy of a constant.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hypergumbo_core import identity_schemes, import_closure, schema

REPO = Path(__file__).resolve().parents[3]
CORE = "packages/hypergumbo-core/src/hypergumbo_core/"

#: Every name the leaf module offers and ``schema.py`` offers again.
SHARED_NAMES = (
    "STABLE_ID_SCHEME", "SHAPE_ID_SCHEME", "REPO_FINGERPRINT_SCHEME",
    "SCIP_STABLE_ID_SCHEME", "SYMBOL_FINGERPRINT_SCHEME",
    "STABLE_ID_PREFIX", "SHAPE_ID_PREFIX", "REPO_FINGERPRINT_PREFIX",
    "SCIP_STABLE_ID_PREFIX", "value_label", "identity_digest",
)


def _core_sources() -> dict[str, str]:
    """The root ``conftest.py`` and every source file of the core package, keyed
    by the repo-relative path ``import_closure`` expects."""
    sources = {"conftest.py": (REPO / "conftest.py").read_text(encoding="utf-8")}
    for path in sorted((REPO / CORE).rglob("*.py")):
        sources[path.relative_to(REPO).as_posix()] = path.read_text(encoding="utf-8")
    return sources


def test_the_leaf_imports_nothing_from_the_package() -> None:
    """A leaf: its only imports are standard-library modules, so loading it
    loads no other file of the package."""
    tree = ast.parse(Path(identity_schemes.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import: the leaf loads a package file"
            imported.add(node.module or "")
    assert imported == {"__future__", "re"}


def test_the_files_every_test_loads_do_not_load_schema() -> None:
    """The root conftest reaches ``ir`` and ``analyze/base`` (the reach control)
    and the leaf, and does not reach ``schema.py``."""
    graph = import_closure.build(_core_sources())

    def loaded_by_the_conftest(name: str) -> bool:
        return "conftest.py" in graph.dependents([CORE + name])

    assert loaded_by_the_conftest("ir.py")
    assert loaded_by_the_conftest("analyze/base.py")
    assert loaded_by_the_conftest("identity_schemes.py")
    assert not loaded_by_the_conftest("schema.py")


@pytest.mark.parametrize("name", SHARED_NAMES)
def test_schema_offers_the_leafs_own_objects(name: str) -> None:
    """One object under two names, never a second copy that could drift."""
    assert getattr(schema, name) is getattr(identity_schemes, name)


def test_the_shared_names_are_all_the_leaf_offers() -> None:
    """A name added to the leaf is added to ``SHARED_NAMES`` (and so to
    ``schema.py``), or this fails."""
    public = {name for name in vars(identity_schemes)
              if not name.startswith("_") and name not in {"annotations", "re"}}
    assert public == set(SHARED_NAMES)
