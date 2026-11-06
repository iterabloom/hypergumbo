# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every third-party library ``packages/*/src`` imports has been looked at by somebody.

Two instruments classify hypergumbo's own dependencies, and both are keyed by
what is CALLED. ``scripts/check-io-allowlist`` names the risky primitives it
knows by hand (``PRIMITIVES``, ``OPEN_LIKE``, ``DYNAMIC_NAME``,
``YAML_LOADER_ARG``, ``PREFIX_MODULES``); ``docs/io-primitives-overlays/
hypergumbo-self.yaml`` carries rows and dated ``module_completeness`` grants
for what the self-analysis reaches. A library reached only through a receiver
the analysis cannot type, or only as an exception class in an ``except``
clause, appears in neither: nobody has read its surface and nothing says so
(google.protobuf was that library on 2026-11-05).

This test asks the cheaper question at the cheapest granularity: for every
top-level import name under ``packages/*/src`` that is neither stdlib nor
first-party, at least one gate primitive OR one overlay row or grant lives
under that name. Head-level on purpose: per-module exactness on CALLS is the
cron self-claims gate's job (``verify_claims._adjudicate_external_modules``);
this asks "did anybody look at this library at all". An import inside
``if TYPE_CHECKING:`` or a function body counts the same: the question is who
looked, not when it loads. The fix for a failure is to read the library's
surface and write the grant (or the rows), never to extend an exemption list
here: there is none.
"""
# covers: scripts/check-io-allowlist, docs/io-primitives-overlays/hypergumbo-self.yaml
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "scripts" / "check-io-allowlist"
OVERLAY = REPO_ROOT / "docs" / "io-primitives-overlays" / "hypergumbo-self.yaml"
STDLIB = set(sys.stdlib_module_names) | {"__future__"}


def first_party_packages(root: Path) -> set[str]:
    """The import names the tree itself provides: every package under packages/*/src."""
    return {p.name for p in root.glob("packages/*/src/*") if (p / "__init__.py").is_file()}


def third_party_import_heads(root: Path = REPO_ROOT) -> dict[str, set[str]]:
    """head -> the dotted modules imported under it, over every .py under packages/*/src.

    An absolute ``import a.b`` or ``from a.b import c`` contributes ``a.b``; a
    relative import contributes nothing (first-party by construction).
    """
    first_party = first_party_packages(root)
    heads: dict[str, set[str]] = {}
    for path in sorted(root.glob("packages/*/src/**/*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                head = module.split(".")[0]
                if head not in STDLIB and head not in first_party:
                    heads.setdefault(head, set()).add(module)
    return heads


def gate_inventory_heads(gate_path: Path = GATE) -> set[str]:
    """Top-level names of every primitive the allowlist gate inventories by hand."""
    loader = importlib.machinery.SourceFileLoader("io_allowlist_gate_for_heads", str(gate_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    gate = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = gate  # dataclasses resolves annotations through sys.modules
    loader.exec_module(gate)
    names = (set(gate.PRIMITIVES) | set(gate.OPEN_LIKE) | set(gate.DYNAMIC_NAME)
             | set(gate.YAML_LOADER_ARG) | set(gate.PREFIX_MODULES))
    return {name.split(".")[0] for name in names}


def overlay_heads(overlay_path: Path = OVERLAY) -> set[str]:
    """Top-level names of every row and module_completeness grant in the self overlay."""
    doc = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
    modules = {row["module"] for section in doc.values() if isinstance(section, list)
               for row in section if isinstance(row, dict) and "module" in row}
    return {module.split(".")[0] for module in modules}


def unclassified_heads(root: Path = REPO_ROOT) -> dict[str, set[str]]:
    classified = gate_inventory_heads() | overlay_heads()
    return {head: modules for head, modules in third_party_import_heads(root).items()
            if head not in classified}


# --- the live tree ----------------------------------------------------------


def test_every_third_party_import_head_is_in_the_gate_inventory_or_the_self_overlay():
    residual = unclassified_heads()
    assert not residual, (
        "libraries imported under packages/*/src that neither scripts/check-io-allowlist "
        "nor docs/io-primitives-overlays/hypergumbo-self.yaml says anything about -- read "
        f"each one's surface and write the grant or the rows: {residual}"
    )


def test_the_live_tree_exercises_both_instruments():
    # Not vacuous: the tree imports a library only the overlay classifies (proquint,
    # pure transcoding), one both classify (yaml), and the gate inventories one the
    # tree does not import at all (requests). Each membership is read, not assumed.
    heads, gate, overlay = third_party_import_heads(), gate_inventory_heads(), overlay_heads()
    assert "proquint" in heads and "proquint" in overlay and "proquint" not in gate
    assert "yaml" in heads and "yaml" in gate and "yaml" in overlay
    assert "requests" not in heads and "requests" in gate


# --- the collector, on a fixture tree -----------------------------------------


def _fixture_tree(tmp_path: Path, source: str) -> Path:
    pkg = tmp_path / "packages" / "hypergumbo-fixture" / "src" / "hypergumbo_fixture"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(source)
    (pkg / "sibling.py").write_text("thing = 1\n")
    return tmp_path


def test_collector_keeps_absolute_imports_and_skips_stdlib_first_party_and_relative(tmp_path: Path):
    root = _fixture_tree(tmp_path, (
        "from __future__ import annotations\n"
        "import os, json\n"
        "import leftpad\n"
        "from leftpad.core import pad\n"
        "from leftpad import rightpad as rp\n"
        "from . import sibling\n"
        "from .sibling import thing\n"
        "import hypergumbo_fixture.sibling\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import typesonly\n"
        "def f():\n"
        "    import lazyload.sub\n"
    ))
    assert first_party_packages(root) == {"hypergumbo_fixture"}
    assert third_party_import_heads(root) == {
        "leftpad": {"leftpad", "leftpad.core"},
        "typesonly": {"typesonly"},
        "lazyload": {"lazyload.sub"},
    }


def test_an_unclassified_head_is_reported_with_its_modules(tmp_path: Path):
    root = _fixture_tree(tmp_path, "import leftpad.core\nimport yaml\nimport proquint\n")
    assert unclassified_heads(root) == {"leftpad": {"leftpad.core"}}
