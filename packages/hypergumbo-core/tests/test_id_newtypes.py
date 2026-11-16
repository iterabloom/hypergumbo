# SPDX-License-Identifier: AGPL-3.0-or-later
"""The identifier NewTypes ``StableId``, ``ShapeId`` and ``RepoFingerprint``.

What is pinned, and why:

- Each name is a ``typing.NewType`` over ``str``, and the three fields it
  types (``Symbol.stable_id``, ``Symbol.shape_id``,
  ``AnalysisRun.repo_fingerprint``) resolve to it. A NewType is the
  identity at run time, so these values are the ``str`` they always were;
  the gain is static: mypy (the strict ratchet) rejects a plain ``str``
  reaching one of these slots.
- The NewType constructors are called ONLY at the minting sites listed in
  ``MINT_SITES``. The type only helps if a value is wrapped where it is
  hashed and prefixed, not wherever a caller wants mypy to stop
  complaining: ``StableId(f"...")`` anywhere else is a hand-built id that
  the factories exist to prevent. A new legitimate mint is added to the
  list on purpose, in review.
- The mints produce exactly the strings the pre-NewType code produced:
  ``sha256:`` plus the first 16 hex digits for stable and shape ids, and
  ``sha256:`` plus the full 64-hex digest for the repo fingerprint.
"""
from __future__ import annotations

import ast
import hashlib
import typing
from pathlib import Path
from typing import Optional

from hypergumbo_core.analyze.base import _short_sha256, mint_shape_id
from hypergumbo_core.ir import (
    AnalysisRun,
    RepoFingerprint,
    ShapeId,
    StableId,
    Symbol,
    _canonical_external_stable_id,
)
from hypergumbo_core.repo_fingerprint import (
    compute_repo_fingerprint,
    compute_repo_fingerprint_field,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

#: (path under packages/, enclosing function) of every NewType constructor
#: call in shipped source. Each is where a value of that kind is minted.
MINT_SITES = {
    ("hypergumbo-core/src/hypergumbo_core/analyze/base.py", "_short_sha256", "StableId"),
    ("hypergumbo-core/src/hypergumbo_core/ir.py", "_canonical_external_stable_id", "StableId"),
    ("hypergumbo-core/src/hypergumbo_core/analyze/base.py", "mint_shape_id", "ShapeId"),
    (
        "hypergumbo-core/src/hypergumbo_core/repo_fingerprint.py",
        "compute_repo_fingerprint_field",
        "RepoFingerprint",
    ),
}

_NEWTYPE_NAMES = {"StableId", "ShapeId", "RepoFingerprint"}


def test_the_three_names_are_newtypes_over_str():
    for newtype in (StableId, ShapeId, RepoFingerprint):
        assert isinstance(newtype, typing.NewType), newtype
        assert newtype.__supertype__ is str, newtype


def test_each_retyped_field_resolves_to_its_newtype():
    symbol_hints = typing.get_type_hints(Symbol)
    assert symbol_hints["stable_id"] == Optional[StableId]
    assert symbol_hints["shape_id"] == Optional[ShapeId]
    run_hints = typing.get_type_hints(AnalysisRun)
    assert run_hints["repo_fingerprint"] == Optional[RepoFingerprint]
    create_hints = typing.get_type_hints(AnalysisRun.create)
    assert create_hints["repo_fingerprint"] == Optional[RepoFingerprint]


def _newtype_calls(path: Path) -> set[tuple[str, str]]:
    """(enclosing function, NewType name) for each constructor call in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[tuple[str, str]] = set()

    def visit(node: ast.AST, enclosing: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Call):
                func = child.func
                name = (
                    func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute)
                    else None
                )
                if name in _NEWTYPE_NAMES:
                    found.add((enclosing, name))
            visit(child, enclosing)

    visit(tree, "<module>")
    return found


def test_newtype_constructors_are_called_only_at_the_mint_sites():
    packages = REPO_ROOT / "packages"
    seen = set()
    for path in sorted(packages.glob("*/src/**/*.py")):
        rel = path.relative_to(packages).as_posix()
        for enclosing, name in _newtype_calls(path):
            seen.add((rel, enclosing, name))
    assert seen == MINT_SITES, (
        f"unexpected: {sorted(seen - MINT_SITES)}; "
        f"missing: {sorted(MINT_SITES - seen)}"
    )


def test_mint_scan_sees_nested_and_attribute_calls(tmp_path: Path):
    """Non-vacuity for the scan above: both call forms, at any depth."""
    source = tmp_path / "m.py"
    source.write_text(
        "import ir\n"
        "X = StableId('a')\n"
        "def outer():\n"
        "    def inner():\n"
        "        return ir.ShapeId('b')\n"
        "    return RepoFingerprint(str('c'))\n",
        encoding="utf-8",
    )
    assert _newtype_calls(source) == {
        ("<module>", "StableId"),
        ("inner", "ShapeId"),
        ("outer", "RepoFingerprint"),
    }


def test_mints_produce_the_unchanged_strings(tmp_path: Path):
    def short(payload: str) -> str:
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]

    assert _short_sha256("file:python:a.py") == short("file:python:a.py")
    assert mint_shape_id("function:FunctionDef()") == short("function:FunctionDef()")
    assert _canonical_external_stable_id("python", "a.py", "f") == short(
        "external:python:a.py:f"
    )
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert compute_repo_fingerprint_field(tmp_path) == (
        "sha256:" + compute_repo_fingerprint(tmp_path)
    )
    for value in (_short_sha256("x"), mint_shape_id("x")):
        assert type(value) is str
