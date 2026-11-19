# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``language=None`` stand-in fingerprint is minted in one place.

What is pinned, and why:

- A Class-B stand-in (ADR-0031: ``language is None``) has no source the
  central ``stamp_symbol_fingerprints`` pass could hash, so its producer
  stamps an identity-derived fingerprint and the post-pass keeps it (spec
  "``fingerprint``": the one documented second shape). The formula, the
  first 16 hex digits of ``sha256(node id)``, was written out at fourteen
  linker sites and in the ``make_synthetic_symbol_identity`` backstop, whose
  comment said it "matches" the linkers (ADR-0034: one factory per kind of
  identity value). ``mint_stand_in_fingerprint`` is that one place now.
- The value is unchanged: bare 16 lowercase hex, no label. The spec states
  the shape and says it carries no ``hgfp2:`` label by design;
  ``hgfp2:`` (``SYMBOL_FINGERPRINT_SCHEME``) names the source subtree walk,
  a different algorithm, and no other scheme constant names this one.
  Whether it should get a label of its own is an owner decision (WI-tovog).
- The gate: no shipped source computes a fingerprint, or a 16-hex digest of
  an id, inline. A new copy fails ``test_no_inline_copy_in_shipped_source``
  and names its file and line.
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from hypergumbo_core.analyze.base import (
    make_synthetic_symbol_identity,
    mint_stand_in_fingerprint,
)
from hypergumbo_core.fingerprint import stamp_symbol_fingerprints
from hypergumbo_core.ir import Span, Symbol

REPO_ROOT = Path(__file__).resolve().parents[3]

#: The factory: the one function allowed to hash an id into a fingerprint.
_FACTORY = ("hypergumbo-core/src/hypergumbo_core/analyze/base.py", "mint_stand_in_fingerprint")


def test_the_factory_writes_the_value_the_inline_copies_wrote() -> None:
    node_id = "typescript:web/doc.ts:4-4:meta:function"
    expected = hashlib.sha256(node_id.encode()).hexdigest()[:16]
    assert mint_stand_in_fingerprint(node_id) == expected
    assert re.fullmatch(r"[0-9a-f]{16}", mint_stand_in_fingerprint("x"))


def test_the_backstop_mints_through_the_factory() -> None:
    sym = Symbol(
        id="annotation:a.py:0-0:orders:function", name="orders", kind="function",
        language=None, path="a.py", span=Span(1, 1, 0, 0), origin="t",
        protocol_origin="annotation",
    )
    _stable_id, _label, fingerprint = make_synthetic_symbol_identity(sym)
    assert fingerprint == mint_stand_in_fingerprint(sym.id)


def test_the_post_pass_keeps_a_stand_in_fingerprint(tmp_path: Path) -> None:
    """What reaches the output: the central pass leaves the minted value on a
    ``language=None`` node (it cannot hash a stand-in's source)."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    sym = Symbol(
        id="annotation:a.py:1-1:orders:function", name="orders", kind="function",
        language=None, path="a.py", span=Span(1, 1, 0, 5), origin="t",
        fingerprint=mint_stand_in_fingerprint("annotation:a.py:1-1:orders:function"),
    )
    before = sym.fingerprint
    stamp_symbol_fingerprints([sym], tmp_path)
    assert sym.fingerprint == before


def _offences(path: Path, rel: str) -> list[str]:
    """Inline fingerprint mints in *path*: a ``fingerprint`` keyword or
    attribute assignment whose value calls ``hexdigest``, and any
    ``hashlib.sha256(<id>.encode()).hexdigest()[:16]`` of an id (a name ending
    ``id`` or an ``.id`` attribute), outside the factory."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []

    def calls_hexdigest(node: ast.AST) -> bool:
        return any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "hexdigest"
            for n in ast.walk(node)
        )

    def hashes_an_id(node: ast.AST) -> bool:
        for n in ast.walk(node):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "sha256" and n.args):
                continue
            arg = n.args[0]
            if not (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
                    and arg.func.attr == "encode"):
                continue
            target = arg.func.value
            if isinstance(target, ast.Name) and target.id.endswith("id"):
                return True
            if isinstance(target, ast.Attribute) and target.attr == "id":
                return True
        return False

    def visit(node: ast.AST, enclosing: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if (rel, enclosing) != _FACTORY:
                # One offence per site: a flagged site's value is not read again.
                if isinstance(child, ast.keyword) and child.arg == "fingerprint" \
                        and calls_hexdigest(child.value):
                    found.append(f"{rel}:{child.value.lineno} fingerprint= computed inline")
                    continue
                if isinstance(child, ast.Assign) and any(
                    isinstance(t, ast.Attribute) and t.attr == "fingerprint"
                    for t in child.targets
                ) and calls_hexdigest(child.value):
                    found.append(f"{rel}:{child.lineno} .fingerprint computed inline")
                    continue
                if isinstance(child, ast.Subscript) and hashes_an_id(child.value) \
                        and calls_hexdigest(child.value):
                    found.append(f"{rel}:{child.lineno} 16-hex digest of an id")
                    continue
            visit(child, enclosing)

    visit(tree, "<module>")
    return found


def test_no_inline_copy_in_shipped_source() -> None:
    packages = REPO_ROOT / "packages"
    offences: list[str] = []
    for path in sorted(packages.glob("*/src/**/*.py")):
        offences.extend(_offences(path, path.relative_to(packages).as_posix()))
    assert offences == [], (
        "mint a language=None stand-in's fingerprint through "
        "analyze.base.mint_stand_in_fingerprint, not inline: " + "; ".join(offences)
    )


def test_the_scan_sees_each_inline_form(tmp_path: Path) -> None:
    """Non-vacuity: every form the gate names is found, at any depth, and the
    factory itself is not."""
    source = tmp_path / "m.py"
    source.write_text(
        "import hashlib\n"
        "def f(sub_id, sym):\n"
        "    Symbol(fingerprint=hashlib.sha256(sub_id.encode()).hexdigest()[:16])\n"
        "    sym.fingerprint = hashlib.sha256(\n"
        "        sym.id.encode()\n"
        "    ).hexdigest()[:16]\n"
        "    x = hashlib.sha256(sym.id.encode()).hexdigest()[:16]\n"
        "    y = hashlib.sha256(b'other').hexdigest()[:16]\n"
        "    return x, y\n",
        encoding="utf-8",
    )
    found = _offences(source, "m.py")
    assert [f.split(" ", 1)[1] for f in found] == [
        "fingerprint= computed inline",
        ".fingerprint computed inline",
        "16-hex digest of an id",
    ], found
    factory = tmp_path / "base.py"
    factory.write_text(
        "import hashlib\n"
        "def mint_stand_in_fingerprint(node_id):\n"
        "    return hashlib.sha256(node_id.encode()).hexdigest()[:16]\n",
        encoding="utf-8",
    )
    assert _offences(factory, _FACTORY[0]) == []
    assert _offences(factory, "elsewhere.py") != []
