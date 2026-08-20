# SPDX-License-Identifier: AGPL-3.0-or-later
"""AST-block hashing: the change unit for coverage-directed test selection.

WHAT PROBLEM THIS SOLVES. Coverage tells us which tests executed which lines.
To turn that into "this commit changed X, so run these tests" the map must be
keyed on something stable across edits that do not change behaviour. Keying on
``(file, line)`` fails immediately: adding one import shifts every line below it
and the whole file's tests get selected, which is the outcome the selector
exists to avoid.

WHY BLOCKS AND NOT LINES. The obvious repair — hash each LINE's text, dropping
its position — swaps a false positive for a false negative. ``return None``
hashes identically everywhere, so moving a line from one function to another
leaves every hash unchanged while behaviour changed. Hashing the enclosing
BLOCK keeps the position-independence and closes that hole, because a move
changes both the block's qualified name and its parent's structure.

THE DIGEST IS ``ast.dump(node, include_attributes=False)``. Excluding attributes
drops line and column numbers, which is precisely the position-independence we
want, and parsing to an AST discards comments and formatting for free. So a
reflow, a comment edit, or an insertion above the block all leave the digest
alone, while a signature change, a decorator, or any body edit perturb it.

CHILDREN ARE REMOVED FROM THE PARENT ENTIRELY, not reduced to markers. The
first version kept a ``<block:name>`` placeholder so that adding, removing or
renaming a child would perturb the parent. Measured against the real semantics
that was wrong twice over: it made every structural edit select the enclosing
block's tests — and since the enclosing block is import-time code credited to
EVERY test in the file, adding one function selected the whole file's suite.
Removing children outright means only genuine module-level statement changes
perturb the parent, and renames/moves are still caught, by a different and
sharper route: the old qualified name VANISHES from the index, and
:func:`~hypergumbo_core.selection_index.changed_blocks` treats a vanished block
as changed and selects the tests that used to run it.

KNOWN FALSE NEGATIVE OF THAT CHOICE, recorded rather than hidden: adding a
second definition that SHADOWS an earlier one at the same scope (``def f``
twice) creates a new key ``f#2`` with no history while leaving ``f`` untouched,
so nothing is selected although behaviour changed. Rare, and linters flag it.

A BLOCK OWNS ITS BODY, NOT ITS HEADER. The ``def``/``class`` line and any
decorators execute in the ENCLOSING scope at import time, so they belong to the
enclosing block. Getting this backwards was the defect that made the first
implementation credit every function in a file to every test: the ``def alpha``
line runs at import, and while ``alpha`` owned that line, ``alpha`` looked like
import-time code and was handed to every test touching the file. A signature or
decorator edit still selects the right tests, because both live inside the
FunctionDef node and move the block's own digest.

LINE OWNERSHIP IS INNERMOST-WINS. Blocks are emitted outermost-first and
:func:`line_owner` lets later (deeper) blocks overwrite earlier claims, so a
covered line resolves to the tightest block containing it.

KNOWN EDGE, RECORDED RATHER THAN SOLVED: editing a ``# pragma: no cover``
comment changes what coverage measures without changing the AST, so it produces
no digest change here. It falls through to the import-graph slice, which is one
of the reasons this is a FOURTH selector rather than a replacement.
"""
from __future__ import annotations

import ast
import copy
import hashlib
from dataclasses import dataclass
from typing import Iterable, Union

#: Nodes that own a block. Everything else folds into its enclosing block.
_BLOCK_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

_BlockNode = Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]

#: Name of the synthetic block holding module-level statements. Angle brackets
#: cannot collide with a Python identifier.
MODULE_BLOCK = "<module>"


@dataclass(frozen=True)
class Block:
    """One hashable unit of code.

    ``name`` is the qualified name (``Holder.beta``, ``outer.inner``) and is the
    JOIN KEY against the stored index — not ``first_line``, which shifts under
    edits elsewhere in the file and is carried only to map coverage's line
    numbers back onto blocks.
    """

    path: str
    name: str
    first_line: int
    last_line: int
    digest: str


class _Elide(ast.NodeTransformer):
    """Delete nested blocks wherever they appear beneath the node being hashed.

    Returning ``None`` removes the node from its parent list AND stops the
    recursion there, so a nested block's own contents never reach the parent's
    digest. Written as three explicit methods rather than one alias because
    ``NodeVisitor`` declares each ``visit_*`` with its own concrete node type,
    and a shared implementation assigned to all three is an incompatible
    override under strict mypy — 3 `assignment` errors on a shrink-only ratchet.

    Recursion into non-block nodes is what makes a conditionally-defined
    function (``if X: def f(): ...``) elide correctly; it is not a direct child
    of the enclosing block.
    """

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None


def _elided_digest(node: ast.AST) -> str:
    """Hash a node's own structure, with all nested blocks removed.

    Deep-copies first: the transformer rewrites in place and the caller still
    needs the original tree to walk into those same children. ``generic_visit``
    rather than ``visit`` so the ROOT survives — visiting it would delete the
    very node being hashed.
    """
    clone = _Elide().generic_visit(copy.deepcopy(node))
    dumped = ast.dump(clone, include_attributes=False)
    return hashlib.sha256(dumped.encode()).hexdigest()[:32]


def _body_span(node: _BlockNode) -> tuple[int, int]:
    """The lines a block OWNS: its body, excluding header and decorators.

    ``def f(...)`` and ``@decorator`` execute in the enclosing scope at import,
    so they belong to it. See the module docstring — owning them here is what
    made every function look like import-time code.
    """
    return node.body[0].lineno, (node.end_lineno or node.lineno)


def _child_blocks(node: ast.AST) -> list[_BlockNode]:
    """Block descendants not separated from ``node`` by another block.

    Descends through non-block nodes so a def nested inside an ``if`` or a
    ``with`` is found; ``ast.iter_child_nodes`` alone would miss it.
    """
    found: list[_BlockNode] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _BLOCK_NODES):
            found.append(child)
        else:
            found.extend(_child_blocks(child))
    return found


def blocks_for_source(path: str, source: str) -> list[Block]:
    """Parse ``source`` into blocks, outermost first.

    Raises ``SyntaxError`` on unparsable input — deliberately not swallowed, so
    a caller indexing a broken file learns about it rather than silently
    recording zero blocks and, later, selecting zero tests.
    """
    tree = ast.parse(source)
    n_lines = len(source.splitlines())
    blocks: list[Block] = [Block(
        path=path,
        name=MODULE_BLOCK,
        first_line=1,
        last_line=max(n_lines, 1),
        digest=_elided_digest(tree),
    )]

    def walk(node: ast.AST, prefix: str) -> None:
        seen: dict[str, int] = {}
        for child in _child_blocks(node):
            # Two defs sharing a name at one level (a conditional redefinition)
            # would collide; disambiguate by source order rather than by line,
            # which would move under an unrelated edit.
            seen[child.name] = seen.get(child.name, 0) + 1
            label = child.name if seen[child.name] == 1 \
                else f"{child.name}#{seen[child.name]}"
            qualified = f"{prefix}{label}"
            first, last = _body_span(child)
            blocks.append(Block(
                path=path,
                name=qualified,
                first_line=first,
                last_line=last,
                digest=_elided_digest(child),
            ))
            walk(child, f"{qualified}.")

    walk(tree, "")
    return blocks


def line_owner(blocks: Iterable[Block]) -> dict[int, str]:
    """``line number -> owning block name``, innermost wins.

    Relies on :func:`blocks_for_source` emitting outermost-first so a deeper
    block simply overwrites its parent's claim on the lines they share.
    """
    owner: dict[int, str] = {}
    for block in blocks:
        for line in range(block.first_line, block.last_line + 1):
            owner[line] = block.name
    return owner
