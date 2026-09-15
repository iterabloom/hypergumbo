# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-lupav: the §3a walk's accounted-for exits are ENUMERATED, not surveyed.

WHY A CENSUS AND NOT MORE CASE TESTS. ``_ddg_taint_reaches`` returns ``False``
to mean "the walk ran to completion and accounted for the tainted value at
every step", and since 2026-09-02 a ``False`` on the §3a arm REMOVES a reported
flow. Every clause of INV-lupav has been the same defect wearing a different
construct: some path continued the walk without setting ``escaped``, and the
exhausted walk then read as positive evidence of absence. Four such clauses
were closed one at a time over six weeks, each found by hunting for a shape.

Hunting for shapes does not terminate. What terminates is the observation that
there are only finitely many places in this function where control continues
without recording an escape — so they can be COUNTED, and the count pinned.
A fifth appearing is then a test failure that names itself, instead of a
six-week search that starts when somebody notices a deleted finding.

THE CENSUS, as it stands, and every entry is licensed by an argument in the
walk's own source:

  1. the seen-set cycle guard — this pair is already being walked;
  2. a barrier line — a sanitizer consumed the value, so what continues
     carries the barrier's OUTPUT label (reachable only on the barrier arm,
     where the caller passes ``barrier_lines``; the §3a arm never sees it);
  3. ``followed`` and NO call at this line — a pure rebinding, so the heir
     really is the value's only exit;
  4. ``followed`` and a catalogued consuming callee (``_use_site_terminates``);
  5. no heir, and a catalogued consuming callee (``_use_site_terminates``).

THE COUNT IN THE PROJECT'S OWN NOTES SAID FOUR. It omitted the barrier exit,
which is a real distinction — that exit cannot fire on the arm with removal
authority — but not a structural one, and a note is not a gate. The census was
off by one before it was ever executed, which is the argument for executing it.

TWO OF THE FIVE TRUST THE CATALOGUE, and no coverage predicate guards that
half: ``fmt.Fprintf`` carried the dead-end marker while writing into its first
argument (PR #961). Counting them here keeps that fact adjacent to the
mechanism rather than in a changelog.

THE FILE CARRIES ITS OWN POSITIVE CONTROL, per the rule this codebase learned
from a vacuous guard that passed for an unrelated reason: ``_census`` is run
over synthetic sources that DO add an unaccounted exit, so the failing
direction is exercised in CI rather than demonstrated once by hand.
"""
from __future__ import annotations

import ast
from pathlib import Path

_TAINT = (
    Path(__file__).resolve().parents[1]
    / "src" / "hypergumbo_core" / "taint.py"
)
_WALK = "_ddg_taint_reaches"

#: Accounted-for exits — a ``continue`` inside the frontier loop that does NOT
#: record an escape first. See the module docstring for what each one is.
EXPECTED_ACCOUNTED_EXITS = 5


def _walk_fn(source: str) -> ast.FunctionDef:
    """The walk's AST node, from source text."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == _WALK:
            return node
    raise AssertionError(f"{_WALK} not found")


def _sets_escaped(stmt: ast.stmt) -> bool:
    """Is this statement exactly ``escaped = True``?"""
    return (
        isinstance(stmt, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "escaped" for t in stmt.targets
        )
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is True
    )


def _census(source: str) -> tuple[int, int]:
    """(accounted-for exits, escaping exits) inside the walk's frontier loop.

    An exit is ACCOUNTED FOR when no ``escaped = True`` precedes the
    ``continue`` in its own block. Reading only the continue's OWN block is
    deliberate: an ``escaped = True`` in an enclosing block belongs to a
    sibling branch, and crediting it would let a new unaccounted exit hide
    behind an unrelated one above it.
    """
    fn = _walk_fn(source)
    loop = next(n for n in ast.walk(fn) if isinstance(n, ast.While))

    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(fn):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def inside_loop(node: ast.AST) -> bool:
        cur = node
        while cur in parents:
            cur = parents[cur]
            if cur is loop:
                return True
        return False

    accounted = escaping = 0
    for node in ast.walk(fn):
        if not isinstance(node, ast.Continue) or not inside_loop(node):
            continue
        parent = parents[node]
        block = next(
            v for _f, v in ast.iter_fields(parent)
            if isinstance(v, list) and node in v
        )
        if any(_sets_escaped(s) for s in block[:block.index(node)]):
            escaping += 1
        else:
            accounted += 1
    return accounted, escaping


class TestAccountedForExitCensus:
    """The shipped walk, counted."""

    def test_the_walk_has_exactly_five_accounted_for_exits(self) -> None:
        """A sixth is a new way to earn ``False``, and must be argued for.

        This test failing is not a bug in the test. It means a path now
        continues the walk without recording an escape, and whoever added it
        owes the same argument the other five carry: why is the tainted value
        accounted for here? Add the entry to the docstring census and bump the
        constant in the same commit.
        """
        accounted, _escaping = _census(_TAINT.read_text())
        assert accounted == EXPECTED_ACCOUNTED_EXITS

    def test_escaping_exits_are_not_counted_as_accounted(self) -> None:
        """The other half of the partition, so the census cannot drift silently.

        Without this, deleting an ``escaped = True`` line — turning an escape
        into an unearned accounted-for exit — could keep the first assertion
        passing if an exit were removed elsewhere in the same commit.
        """
        _accounted, escaping = _census(_TAINT.read_text())
        assert escaping == 3

    def test_false_is_returned_from_exactly_one_place(self) -> None:
        """Every accounted-for path funnels through one ``return False``.

        A second one would be a removal licence reachable without passing the
        ``forfeit_refutation`` gate that guards the first — which is the shape
        ``test_taint_refutation_gate_contract`` exists to prevent at the CALL
        sites, asserted here at the definition.
        """
        fn = _walk_fn(_TAINT.read_text())
        falses = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Return)
            and isinstance(n.value, ast.Constant)
            and n.value.value is False
        ]
        assert len(falses) == 1
        assert falses[0] is fn.body[-1]


class TestTheCensusCanFail:
    """POSITIVE CONTROL. A lint that cannot be shown to fire matches nothing."""

    _STUB = (
        "def _ddg_taint_reaches(a):\n"
        "    escaped = False\n"
        "    while a:\n"
        "        if p():\n"
        "            continue\n"
        "        if q():\n"
        "            escaped = True\n"
        "            continue\n"
        "    return False\n"
    )

    def test_it_counts_the_stub(self) -> None:
        assert _census(self._STUB) == (1, 1)

    def test_a_new_unaccounted_exit_moves_the_count(self) -> None:
        """The failing direction, exercised."""
        extra = self._STUB.replace(
            "        if q():\n",
            "        if r():\n            continue\n        if q():\n",
        )
        assert _census(extra) == (2, 1)

    def test_deleting_an_escape_reclassifies_that_exit(self) -> None:
        """An ``escaped = True`` removed turns an escape into a ``False`` route."""
        weakened = self._STUB.replace("            escaped = True\n", "")
        assert _census(weakened) == (2, 0)

    def test_a_continue_outside_the_frontier_loop_is_not_counted(self) -> None:
        """Seeding runs its own loop, and its ``continue`` is not a walk exit."""
        seeded = self._STUB.replace(
            "    while a:\n",
            "    for s in a:\n        if not s:\n            continue\n"
            "    while a:\n",
        )
        assert _census(seeded) == (1, 1)

    def test_a_missing_walk_is_an_error_not_an_empty_census(self) -> None:
        """A renamed function must fail loudly, not report zero exits."""
        import pytest

        with pytest.raises(AssertionError):
            _census("def other():\n    pass\n")
