# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which tests a change to a function needs, from records made BEFORE the change.

WHAT PROBLEM THIS SOLVES. The selection index records, for a test and a block
(a function body, see :mod:`block_hash`), which of the block's statements the
test ran. A test that ran none of the statements a change touches behaves as
before, so it need not run. But the records in hand when a change is made are
of the block as it WAS: they name the old statements. This module compares the
old and the new source of a file and answers, per block, in the OLD numbering:
a test whose record holds one of THESE statements must run.

THE RULES, one per kind of change. Each is "which old statement must a test
have run, to run the changed code":

* a statement that was EDITED or DELETED: that statement;
* a statement that was INSERTED: its ANCHOR, the statement control passes just
  before reaching it. That is the statement before it in the same list of
  statements; else the statement it is nested in (the ``if`` whose branch it
  starts); else the block's entry, which means every test that reached the
  block. An ``except`` clause is anchored to its ``try``, never to the clause
  before it. A statement no run is ever seen executing cannot stand for the
  one after it and is skipped as an anchor: a docstring or other bare
  constant, ``global`` / ``nonlocal``, an annotation with no value;
* a DOCSTRING or other bare constant that was edited, added or removed:
  nothing. It runs no code. (smart-test treats a file that differs only in
  comments and docstrings the same way; a test that reads ``__doc__`` is the
  known price.) A block in which nothing else changed is ``PROSE``;
* ``global`` / ``nonlocal`` or a bare annotation edited, added or removed:
  every test that reached the block. No run is seen executing such a line,
  and it changes what the statements around it mean;
* the block changed and none of its own statements did (its signature, a
  decorator): every test that reached the block;
* a block that is NEW: nothing of its own. Nobody ran it; a test reaches it
  through a caller that changed to call it, and that caller's change selects
  the test. The exception is a new METHOD: a call that used to reach an
  inherited method reaches the new one with no caller changed, so a new
  method is reported as such (``NEW_METHOD``) for the caller of this module
  to treat as a change to its class;
* a block that is GONE (deleted or renamed): every test that reached it.

Measured before this was written (45 pull requests, 989 changed blocks that
existed before the change): these rules held every test that the records made
AFTER each change showed running a changed statement, at 99% of the test time
of that hindsight rule and 61% of selecting every test that reached a changed
block. An edited statement could be given a narrower rule (the tests that ran
the old one, without its anchor's); it was measured too and saves 0.2%.

WHAT "THE SAME STATEMENT" MEANS. Two statements match when they are equal in
everything that is theirs: their own text with the statements nested in them
taken out, how deeply they are nested, and which list of their parent they
are in (an ``if``'s body or its ``else``, the third ``case`` of a ``match``).
Text alone would call a statement that moved out of an ``if`` unchanged, and a
test that skipped the branch now runs it. The two lists are aligned with
``difflib``; a statement outside every matching run was edited, deleted or
inserted.

WHAT IS CARRIED. For every old statement the change did not touch,
``carried`` gives its ordinal in the new block. A test selected by nothing
here ran only such statements, so its record for the new block is its old
record renumbered: the index can keep knowing what that test runs without
running it.

THE NUMBERING IS ``block_hash``'s. Ordinals index ``Block.statements``; this
module walks the tree again because it also needs each statement's parent, and
its own test checks, on every source file of this repository, that the two
walks number the same statements in the same order.

IMPORT-TIME BLOCKS (a module body, a class body outside any function) are
reported like any other, but no test's record says it ran their statements:
they run when the file is imported. The caller applies the import rule to
them (:mod:`import_closure`).
"""
from __future__ import annotations

import ast
import difflib
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Union

from .block_hash import MODULE_BLOCK, Block, blocks_for_source

#: The anchor of a statement nothing precedes: the block's entry.
ENTRY = -1

_BLOCKS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_BlockNode = Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
_STATEMENTS = (ast.stmt, ast.ExceptHandler)


class Kind(Enum):
    """What happened to one block between the old source and the new."""

    #: Some of its own statements changed: ``ran`` names the old ones to look for.
    STATEMENTS = "statements"
    #: Only docstrings or other bare constants changed: no test.
    PROSE = "prose"
    #: Every test that reached the old block must run; ``why`` says why.
    WHOLE = "whole"
    #: A new function: no test of its own (module docstring).
    NEW = "new"
    #: A new method of a class: unchanged callers may now reach it.
    NEW_METHOD = "new method"
    #: Deleted or renamed: every test that reached the old block.
    GONE = "gone"


@dataclass(frozen=True)
class BlockChange:
    """What one block's change asks of the tests that reached it."""

    name: str
    kind: Kind
    #: Whether the NEW block (the old one, for ``GONE``) runs at import.
    import_time: bool = False
    #: OLD ordinals: a test whose record holds one of them must run.
    ran: frozenset[int] = frozenset()
    #: old ordinal -> new ordinal, for every statement the change left alone.
    carried: Mapping[int, int] = field(default_factory=dict)
    #: For ``WHOLE``: the reason, in words.
    why: str = ""
    #: The digest of the OLD block (``""`` for a block that is new): records
    #: are of a block with a given digest, and only records of this one speak
    #: about ``ran``.
    old_digest: str = ""


@dataclass(frozen=True)
class _Statement:
    #: What matches it to "the same statement" in another version.
    key: str
    #: Ordinal of its anchor, or ``ENTRY``.
    anchor: int
    span: tuple[int, int]
    #: ``"constant"`` (a docstring: runs nothing), ``"scope"`` (``global``,
    #: ``nonlocal``, a bare annotation: runs nothing and changes what its
    #: neighbours mean), or ``""`` for a statement a run is seen executing.
    silent: str = ""


def _own_text(node: ast.AST) -> str:
    """``node`` written out with the statements and blocks nested in it left
    out, and no positions: what is the statement's own. Written here, not
    with ``ast.dump`` on a pruned copy: copying each statement's subtree to
    prune it costs the depth of the nesting again for every statement."""
    parts = [type(node).__name__]
    for name, value in ast.iter_fields(node):
        if isinstance(value, list):
            items = [_own_text(item) if isinstance(item, ast.AST) else repr(item)
                     for item in value if not isinstance(item, (*_STATEMENTS, *_BLOCKS))]
            parts.append(f"{name}=[{', '.join(items)}]")
        elif isinstance(value, ast.AST):
            # No field of Python's tree holds ONE statement: they come in lists.
            parts.append(f"{name}={_own_text(value)}")
        else:
            parts.append(f"{name}={value!r}")
    return "(" + " ".join(parts) + ")"


def _own_key(node: ast.AST, depth: int, place: str) -> str:
    return f"{depth}|{place}|{hashlib.sha256(_own_text(node).encode()).hexdigest()[:24]}"


def _silence(node: ast.AST) -> str:
    """Why no run is ever seen executing this statement, or ``""`` when one is
    (``_Statement.silent``; module docstring)."""
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return "constant"
    if isinstance(node, (ast.Global, ast.Nonlocal)) or (
            isinstance(node, ast.AnnAssign) and node.value is None):
        return "scope"
    return ""


def _statements(block_node: ast.AST) -> list[_Statement]:
    """The block's own statements in ``block_hash``'s ordinal order, each with
    its key and its anchor."""
    out: list[_Statement] = []

    def visit(parent: ast.AST, parent_ordinal: int, depth: int, place: str) -> None:
        for name, value in ast.iter_fields(parent):
            is_list = isinstance(value, list)
            previous: Optional[int] = None
            for index, child in enumerate(value if is_list else [value]):
                if not isinstance(child, ast.AST) or isinstance(child, _BLOCKS):
                    continue
                if isinstance(child, _STATEMENTS):
                    ordinal = len(out)
                    anchored_to_parent = previous is None or isinstance(child, ast.ExceptHandler)
                    silent = _silence(child)
                    out.append(_Statement(
                        key=_own_key(child, depth, f"{place}{name}"),
                        anchor=parent_ordinal if anchored_to_parent or previous is None else previous,
                        span=(child.lineno, child.end_lineno or child.lineno), silent=silent))
                    visit(child, ordinal, depth + 1, "")
                    if is_list and not silent:
                        previous = ordinal
                else:
                    # Not a statement (a ``match`` case, a ``with`` item): the
                    # statements beneath it are told apart by where it sits.
                    visit(child, parent_ordinal, depth,
                          f"{place}{name}[{index}]." if is_list else f"{place}{name}.")

    visit(block_node, ENTRY, 0, "")
    return out


def _header_key(node: ast.AST) -> str:
    """Everything of a block's node that is not one of its statements: its
    arguments, decorators, bases, return annotation."""
    return _own_key(node, 0, "header")


@dataclass(frozen=True)
class _Parsed:
    block: Block
    statements: tuple[_Statement, ...]
    header: str
    is_method: bool


def _parse(path: str, source: str) -> dict[str, _Parsed]:
    """Every block of ``source`` by qualified name, with its statements."""
    tree = ast.parse(source)
    blocks = {block.name: block for block in blocks_for_source(path, source)}
    out = {MODULE_BLOCK: _Parsed(blocks[MODULE_BLOCK], tuple(_statements(tree)), "", False)}

    def children(node: ast.AST) -> list[_BlockNode]:
        found: list[_BlockNode] = []
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _BLOCKS):
                found.append(child)
            else:
                found.extend(children(child))
        return found

    def walk(node: ast.AST, prefix: str, in_class: bool) -> None:
        seen: dict[str, int] = {}
        for child in children(node):
            # The same naming as block_hash.blocks_for_source.
            seen[child.name] = seen.get(child.name, 0) + 1
            label = child.name if seen[child.name] == 1 else f"{child.name}#{seen[child.name]}"
            qualified = f"{prefix}{label}"
            out[qualified] = _Parsed(
                blocks[qualified], tuple(_statements(child)), _header_key(child),
                is_method=in_class and not isinstance(child, ast.ClassDef))
            walk(child, f"{qualified}.", isinstance(child, ast.ClassDef))

    walk(tree, "", False)
    return out


def _compare(old: _Parsed, new: _Parsed) -> BlockChange:
    """One block that exists in both versions and whose digest moved."""
    name, at_import = new.block.name, new.block.import_time
    olds, news = old.statements, new.statements
    matcher = difflib.SequenceMatcher(None, [s.key for s in olds], [s.key for s in news],
                                      autojunk=False)
    carried: dict[int, int] = {}
    ran: set[int] = set()
    inserted: list[int] = []
    touched = scope = False
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            carried.update(zip(range(i1, i2), range(j1, j2), strict=True))
            continue
        touched = True
        scope = scope or any(s.silent == "scope" for s in (*olds[i1:i2], *news[j1:j2]))
        ran.update(i for i in range(i1, i2) if not olds[i].silent)
        inserted.extend(j for j in range(j1, j2) if not news[j].silent)
    header = old.header != new.header
    if header or scope or not touched:
        why = ("its signature or a decorator changed" if header
               else "a global, nonlocal or bare annotation changed" if scope
               else "it changed in a way none of its statements shows")
        return BlockChange(name, Kind.WHOLE, at_import, why=why, old_digest=old.block.digest)

    new_to_old = {new_ordinal: old_ordinal for old_ordinal, new_ordinal in carried.items()}
    for ordinal in inserted:
        anchor = news[ordinal].anchor
        # Up past statements that are themselves new: they have no record.
        while anchor != ENTRY and anchor not in new_to_old:
            anchor = news[anchor].anchor
        if anchor == ENTRY:
            return BlockChange(name, Kind.WHOLE, at_import, old_digest=old.block.digest,
                               why="a statement was added or edited where the block is entered")
        ran.add(new_to_old[anchor])
    kind = Kind.STATEMENTS if ran else Kind.PROSE
    return BlockChange(name, kind, at_import, ran=frozenset(ran), carried=carried,
                       old_digest=old.block.digest)


def statement_changes(
    path: str, old_source: Optional[str], new_source: Optional[str],
) -> dict[str, BlockChange]:
    """What changed in each block of one file, by qualified block name.

    ``old_source`` is ``None`` for a file that did not exist and ``new_source``
    for one that no longer does. Blocks that did not change are left out.
    Raises ``SyntaxError`` for a source that does not parse: a file that cannot
    be read must not look like a file in which nothing changed.
    """
    old = _parse(path, old_source) if old_source is not None else {}
    new = _parse(path, new_source) if new_source is not None else {}
    changes: dict[str, BlockChange] = {}
    for name, was in old.items():
        if name not in new:
            changes[name] = BlockChange(name, Kind.GONE, was.block.import_time,
                                        old_digest=was.block.digest)
    for name, now in new.items():
        before = old.get(name)
        if before is None:
            kind = Kind.NEW_METHOD if now.is_method else Kind.NEW
            changes[name] = BlockChange(name, kind, now.block.import_time)
        elif before.block.digest != now.block.digest:
            changes[name] = _compare(before, now)
    return changes
