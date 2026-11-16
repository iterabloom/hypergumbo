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
- The mints produce the scheme's label plus the digest the pre-NewType
  code produced: the first 16 hex digits for stable and shape ids, the full
  64-hex digest for the repo fingerprint. The label is the one derived from
  each scheme constant (``schema.value_label``); the SCIP arm has a mint and
  a label of its own.
"""
from __future__ import annotations

import ast
import hashlib
import typing
from pathlib import Path
from typing import Optional

from hypergumbo_core.analyze.base import _short_sha256, mint_scip_stable_id, mint_shape_id
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
from hypergumbo_core.schema import (
    REPO_FINGERPRINT_PREFIX,
    SCIP_STABLE_ID_PREFIX,
    SHAPE_ID_PREFIX,
    STABLE_ID_PREFIX,
)
from hypergumbo_core.scip.index import scip_stable_id

REPO_ROOT = Path(__file__).resolve().parents[3]

#: (path under packages/, enclosing function) of every NewType constructor
#: call in shipped source. Each is where a value of that kind is minted.
MINT_SITES = {
    ("hypergumbo-core/src/hypergumbo_core/analyze/base.py", "_short_sha256", "StableId"),
    ("hypergumbo-core/src/hypergumbo_core/analyze/base.py", "mint_scip_stable_id", "StableId"),
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


def _digest16(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def test_mints_produce_their_label_and_the_unchanged_digest(tmp_path: Path):
    """Each mint writes its scheme's label before the digest the pre-label
    code wrote after ``sha256:``: the relabel moved no digest."""
    assert _short_sha256("file:python:a.py") == (
        STABLE_ID_PREFIX + _digest16("file:python:a.py")
    )
    assert mint_shape_id("function:FunctionDef()") == (
        SHAPE_ID_PREFIX + _digest16("function:FunctionDef()")
    )
    assert _canonical_external_stable_id("python", "a.py", "f") == (
        STABLE_ID_PREFIX + _digest16("external:python:a.py:f")
    )
    assert mint_scip_stable_id("scip:a.rs:m") == SCIP_STABLE_ID_PREFIX + _digest16("scip:a.rs:m")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert compute_repo_fingerprint_field(tmp_path) == (
        REPO_FINGERPRINT_PREFIX + compute_repo_fingerprint(tmp_path)
    )
    for value in (_short_sha256("x"), mint_shape_id("x"), mint_scip_stable_id("x")):
        assert type(value) is str


def test_each_mint_site_carries_its_own_label(tmp_path: Path):
    """A value minted at each site starts with that site's label, written out
    here as literals so the labels themselves are pinned."""
    assert _short_sha256("p").startswith("hgstableid15:")
    assert _canonical_external_stable_id("python", "a.py", "f").startswith("hgstableid15:")
    assert mint_shape_id("p").startswith("hgshapeid4:")
    assert scip_stable_id("src/lib.rs", "rust-analyzer cargo x 1.0 crate/").startswith("hgscipid1:")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert compute_repo_fingerprint_field(tmp_path).startswith("hgrepofp3:")


def test_the_scip_mint_and_the_tree_sitter_mint_never_agree():
    """Same hash input, two schemes: the digests match and the values do not,
    so a SCIP-arm id can never be mistaken for a tree-sitter one."""
    a, b = _short_sha256("scip:a.rs:m"), mint_scip_stable_id("scip:a.rs:m")
    assert a != b
    assert a.rpartition(":")[2] == b.rpartition(":")[2]
