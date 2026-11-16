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

STATEMENTS ARE NUMBERED BY THE SAME TREE THE DIGEST IS TAKEN FROM. The index
also records which of a block's statements each test ran, and such a record is
read later against an edited file, so a statement needs a name that edits the
digest ignores do not move. A line number is not one; nor is a line's offset
inside its function, because a comment added inside the function shifts every
offset below it while the digest stays equal, and a stale record would read as
current. So ``Block.statements`` lists the block's OWN statements (every
``ast.stmt`` and ``except`` clause beneath it that is not inside a nested
block, nor a nested block itself) in tree order, and a statement's name is its
position in that list: its ORDINAL. Equal digests mean equal dumps of that
tree, so equal lists: an ordinal recorded under a digest means the same
statement in any source whose block still has that digest.

A nested block is left out of the numbering for the same reason it is left out
of the digest: adding a function between two statements must not renumber the
second while the digest says nothing changed. The price, recorded: the ``def``
line and decorators of a NESTED block belong to no statement
(:func:`statement_owner` maps them to nothing), so no record says which test
ran them. A ``match`` statement's ``case`` lines count as the ``match`` itself
(``ast.match_case`` carries no position).
"""
from __future__ import annotations

import ast
import copy
import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Union

#: Nodes that own a block. Everything else folds into its enclosing block.
_BLOCK_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

_BlockNode = Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]

#: Nodes that get an ordinal in their block (see the module docstring).
_STATEMENT_NODES = (ast.stmt, ast.ExceptHandler)

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
    #: The block's own statements run when the file is IMPORTED: the module
    #: body, and a class body not inside any function. Coverage records those
    #: lines under no test's context, so the selection index holds no row
    #: saying which tests ran them (``selection_index.narrowing_blockers``).
    import_time: bool = False
    #: ``(first line, last line)`` of each of the block's own statements, in
    #: ordinal order. Positions, so not part of the block's identity: two
    #: parses of a block that only moved are the same block.
    statements: tuple[tuple[int, int], ...] = field(default=(), compare=False)
    #: First line of the block's NODE: its first decorator, or its ``def`` /
    #: ``class`` line (``first_line`` is where the BODY starts). 0 when the
    #: block was not built by :func:`blocks_for_source`.
    node_first_line: int = field(default=0, compare=False)


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


def _own_statements(node: ast.AST) -> tuple[tuple[int, int], ...]:
    """Line spans of ``node``'s own statements, in tree order.

    The walk is the one :class:`_Elide` leaves: it never enters a nested
    block and never counts one. Tree order, not line order, so the result is a
    function of the structure the digest hashes and of nothing else.
    """
    spans: list[tuple[int, int]] = []

    def visit(parent: ast.AST) -> None:
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, _BLOCK_NODES):
                continue
            if isinstance(child, _STATEMENT_NODES):
                spans.append((child.lineno, child.end_lineno or child.lineno))
            visit(child)

    visit(node)
    return tuple(spans)


def _node_first_line(node: _BlockNode) -> int:
    """Where a block's node starts: its first decorator, else its own line."""
    return min([node.lineno, *(d.lineno for d in node.decorator_list)])


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
        import_time=True,
        statements=_own_statements(tree),
        node_first_line=1,
    )]

    def walk(node: ast.AST, prefix: str, at_import: bool = True) -> None:
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
            runs_at_import = at_import and isinstance(child, ast.ClassDef)
            blocks.append(Block(
                path=path,
                name=qualified,
                first_line=first,
                last_line=last,
                digest=_elided_digest(child),
                import_time=runs_at_import,
                statements=_own_statements(child),
                node_first_line=_node_first_line(child),
            ))
            walk(child, f"{qualified}.", runs_at_import)

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


def statement_owner(blocks: Iterable[Block]) -> dict[int, tuple[str, int]]:
    """``line number -> (block name, statement ordinal)``, innermost wins.

    A line inside several of a block's statements (the body of an ``if``)
    belongs to the innermost, which tree order lists last. A line of a nested
    block belongs to that block's statements or to nothing: on reaching a
    nested block its whole node, header and decorators included, is first
    taken away from the enclosing blocks, so the header is nobody's statement
    and an ``if`` around a ``def`` does not claim the ``def``'s lines.

    Relies, like :func:`line_owner`, on :func:`blocks_for_source` emitting a
    block before the blocks nested in it. Lines that are no statement's
    (blank, comment, a ``def`` line) are absent.
    """
    owner: dict[int, tuple[str, int]] = {}
    for block in blocks:
        if block.name != MODULE_BLOCK:
            for line in range(block.node_first_line, block.last_line + 1):
                owner.pop(line, None)
        for ordinal, (first, last) in enumerate(block.statements):
            for line in range(first, last + 1):
                owner[line] = (block.name, ordinal)
    return owner
