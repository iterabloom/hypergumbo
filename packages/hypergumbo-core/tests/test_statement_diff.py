# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which old statements a change makes a test answer for.

``statement_diff`` compares two versions of a file and says, per function, in
the numbering of the OLD version: a test that ran one of these statements must
run again. The claim that matters is soundness, and the last test here checks
it by EXECUTION: small functions are generated, changed at random, and run on
a grid of inputs under a line tracer; an input whose result differs between
the two versions must have run, in the old one, a statement the diff names (or
the diff must have asked for every test). The tests before it pin one rule
each, on code a person can read.
"""
from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hypergumbo_core import statement_diff
from hypergumbo_core.block_hash import MODULE_BLOCK, blocks_for_source, statement_owner
from hypergumbo_core.statement_diff import BlockChange, Kind

REPO = Path(__file__).resolve().parents[3]

OLD = textwrap.dedent('''
    def f(x):
        """Doc."""          # 0
        a = 1               # 1
        if x:               # 2
            b = 2           # 3
        else:
            b = 5           # 4
        return a + b        # 5
''')


def _change(old: str, new: str, block: str = "f") -> BlockChange:
    return statement_diff.statement_changes("m.py", old, new)[block]


def _edit(before: str, after: str) -> BlockChange:
    assert before in OLD
    return _change(OLD, OLD.replace(before, after))


def test_the_numbering_is_block_hashs_on_real_source_files() -> None:
    """The index's records name statements by ``block_hash``'s ordinals. This
    module walks the tree itself; on real code the two walks must agree.
    Every third source file of the core package under 60 KB: all 220 take
    most of a minute under coverage, and agree (checked when this was written:
    3,953 blocks, 47,599 statements)."""
    every = sorted((REPO / "packages" / "hypergumbo-core" / "src").rglob("*.py"))
    files = [path for path in every if path.stat().st_size < 60_000][::3]
    assert len(files) > 50
    blocks = statements = 0
    for path in files:
        source = path.read_text(encoding="utf-8")
        parsed = statement_diff._parse(str(path), source)
        for block in blocks_for_source(str(path), source):
            assert tuple(s.span for s in parsed[block.name].statements) == block.statements, (
                path, block.name)
            blocks += 1
            statements += len(block.statements)
    assert blocks > 500 and statements > 5000, (blocks, statements)


def test_an_unchanged_file_and_a_reflowed_one_report_nothing() -> None:
    assert statement_diff.statement_changes("m.py", OLD, OLD) == {}
    reflowed = OLD.replace("a = 1 ", "a = 1  # a comment\n\n    # and a blank line").replace(
        "return a + b", "return (a +\n            b)")
    assert statement_diff.statement_changes("m.py", OLD, reflowed) == {}


def test_an_edited_statement_asks_for_itself_and_for_what_leads_to_it() -> None:
    change = _edit("b = 2 ", "b = 3 ")
    assert change.kind is Kind.STATEMENTS
    # The old ``b = 2`` (3), and the ``if`` (2) whose branch the new one starts.
    assert change.ran == frozenset({2, 3})
    assert change.carried == {0: 0, 1: 1, 2: 2, 4: 4, 5: 5}
    assert not change.import_time and change.why == ""
    # The records that can speak about ``ran`` are those of the OLD block.
    (old_block,) = [b for b in blocks_for_source("m.py", OLD) if b.name == "f"]
    assert change.old_digest == old_block.digest


def test_a_deleted_statement_asks_only_for_itself() -> None:
    change = _edit("    a = 1               # 1\n", "")
    assert (change.kind, change.ran) == (Kind.STATEMENTS, frozenset({1}))
    assert change.carried == {0: 0, 2: 1, 3: 2, 4: 3, 5: 4}


def test_an_inserted_statement_asks_for_the_statement_before_it() -> None:
    change = _edit("    return a + b", "    c = 9\n    return a + b + c")
    # ``c = 9`` follows the ``if`` (2); the edited ``return`` (5) follows ``c = 9``,
    # which is new, so it too is answered for by the ``if``.
    assert change.ran == frozenset({2, 5})


def test_a_statement_inserted_first_in_a_branch_asks_for_the_statement_it_is_nested_in() -> None:
    change = _edit("        b = 5 ", "        z = 0\n        b = 5 ")
    assert change.ran == frozenset({2})
    assert change.carried[4] == 5


def test_a_statement_added_where_the_block_is_entered_asks_for_every_test() -> None:
    change = _edit('    """Doc."""          # 0\n', '    """Doc."""\n    first = 0\n')
    assert change.kind is Kind.WHOLE
    assert change.why == "a statement was added or edited where the block is entered"
    assert change.ran == frozenset()
    assert change.old_digest
    # The docstring is before it and cannot answer: no run is seen executing it.
    edited_first = _edit("    a = 1 ", "    a = 7 ")
    assert edited_first.kind is Kind.WHOLE


def test_an_except_clause_is_answered_for_by_its_try_not_by_the_clause_before() -> None:
    old = textwrap.dedent('''
        def f(x):
            pre = 0             # 0
            try:                # 1
                y = 1 // x      # 2
            except ZeroDivisionError:   # 3
                y = 0           # 4
            return y            # 5
    ''')
    new = old.replace("    return y", "    except TypeError:\n        y = -1\n    return y")
    change = _change(old, new)
    # The new clause and its body: the ``try`` (1). Not clause 3, which a
    # TypeError never enters.
    assert change.ran == frozenset({1})
    assert change.carried == {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 7}


def test_only_a_docstring_changing_is_prose_and_asks_for_nothing() -> None:
    change = _edit('"""Doc."""', '"""Another docstring."""')
    assert (change.kind, change.ran) == (Kind.PROSE, frozenset())
    assert change.carried == {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}
    removed = _edit('    """Doc."""          # 0\n', "")
    assert removed.kind is Kind.PROSE
    # A bare constant in the middle is the same thing.
    assert _edit("    a = 1 ", '    a = 1\n    "a note" ').kind is Kind.PROSE
    # With a code edit beside it, only the code edit counts.
    both = _change(OLD, OLD.replace('"""Doc."""', '"""Other."""').replace("b = 5 ", "b = 6 "))
    assert (both.kind, both.ran) == (Kind.STATEMENTS, frozenset({2, 4}))


@pytest.mark.parametrize("line", ["global a", "nonlocal_like: int"])
def test_a_scope_declaration_changes_what_its_neighbours_mean(line: str) -> None:
    """No run is seen executing ``global a`` or a bare annotation, and both
    change which variable the unchanged lines around them touch."""
    added = _edit("    a = 1 ", f"    {line}\n    a = 1 ")
    assert added.kind is Kind.WHOLE
    assert added.why == "a global, nonlocal or bare annotation changed"
    with_it = OLD.replace("    a = 1 ", f"    {line}\n    a = 1 ")
    assert _change(with_it, OLD).kind is Kind.WHOLE, "removing it is the same"


@pytest.mark.parametrize(("before", "after"), [
    ("def f(x):", "def f(x, y=1):"),
    ("def f(x):", "def f(x) -> int:"),
    ("def f(x):", "@decorated\ndef f(x):"),
])
def test_a_signature_or_decorator_change_asks_for_every_test(before: str, after: str) -> None:
    change = _edit(before, after)
    assert (change.kind, change.why) == (Kind.WHOLE, "its signature or a decorator changed")
    # Also when a statement changed too: the statement rule must not narrow it.
    both = _change(OLD, OLD.replace(before, after).replace("b = 5 ", "b = 6 "))
    assert both.kind is Kind.WHOLE


def test_a_statement_that_moves_out_of_a_branch_is_a_change_though_its_text_is_not() -> None:
    old = textwrap.dedent('''
        def f(x):
            a = 0           # 0
            if x:           # 1
                a = 1       # 2
                b = 2       # 3
            return a        # 4
    ''')
    new = old.replace("        b = 2       # 3", "    b = 2")
    change = _change(old, new)
    # A test that skipped the branch now runs ``b = 2``: the ``if`` (1) answers.
    assert change.kind is Kind.STATEMENTS and change.ran == frozenset({1, 3})


def test_a_statement_that_moves_to_the_else_branch_is_a_change() -> None:
    old = textwrap.dedent('''
        def f(x):
            if x:           # 0
                a = 1       # 1
                b = 2       # 2
            else:
                a = 3       # 3
            return a        # 4
    ''')
    new = textwrap.dedent('''
        def f(x):
            if x:
                a = 1
            else:
                b = 2
                a = 3
            return a
    ''')
    change = _change(old, new)
    assert change.kind is Kind.STATEMENTS and {0, 2} <= change.ran


def test_a_statement_that_moves_to_another_case_is_a_change() -> None:
    old = textwrap.dedent('''
        def f(x):
            match x:            # 0
                case 1:
                    a = 1       # 1
                    b = 2       # 2
                case _:
                    a = 3       # 3
            return a            # 4
    ''')
    new = textwrap.dedent('''
        def f(x):
            match x:
                case 1:
                    a = 1
                case _:
                    b = 2
                    a = 3
            return a
    ''')
    change = _change(old, new)
    assert change.kind is Kind.STATEMENTS and {0, 2} <= change.ran
    # And an edited case pattern is the match statement's own change.
    pattern = _change(old, old.replace("case 1:", "case 2:"))
    assert pattern.kind is Kind.WHOLE, "the match is the first statement: nothing precedes it"


def test_a_nested_functions_change_is_its_own_and_not_its_parents() -> None:
    old = textwrap.dedent('''
        def outer():
            def inner():
                return 1
            return inner()
    ''')
    changes = statement_diff.statement_changes("m.py", old, old.replace("return 1", "return 2"))
    assert set(changes) == {"outer.inner"}
    assert changes["outer.inner"].kind is Kind.WHOLE, "its only statement is where it is entered"


def test_new_gone_and_renamed_blocks() -> None:
    old = textwrap.dedent('''
        def keep():
            return 1

        def old_name():
            return 2

        class C:
            def m(self):
                return 3
    ''')
    new = textwrap.dedent('''
        def keep():
            return 1

        def new_name():
            return 2

        class C:
            def m(self):
                return 3

            def override(self):
                return 4

            class Inner:
                pass

        def added():
            def local():
                return 5
            return local()
    ''')
    changes = statement_diff.statement_changes("m.py", old, new)
    assert {name: change.kind for name, change in changes.items()} == {
        "old_name": Kind.GONE, "new_name": Kind.NEW,
        # A method: unchanged callers of an inherited ``override`` now reach it.
        "C.override": Kind.NEW_METHOD,
        "C.Inner": Kind.NEW, "added": Kind.NEW, "added.local": Kind.NEW,
    }
    assert changes["C.Inner"].import_time and not changes["C.override"].import_time


def test_an_added_and_a_deleted_file() -> None:
    added = statement_diff.statement_changes("m.py", None, OLD)
    assert {name: change.kind for name, change in added.items()} == {
        MODULE_BLOCK: Kind.NEW, "f": Kind.NEW}
    assert added[MODULE_BLOCK].import_time
    deleted = statement_diff.statement_changes("m.py", OLD, None)
    assert {name: change.kind for name, change in deleted.items()} == {
        MODULE_BLOCK: Kind.GONE, "f": Kind.GONE}
    assert all(change.old_digest for change in deleted.values())
    assert not any(change.old_digest for change in added.values())
    assert statement_diff.statement_changes("m.py", None, None) == {}


def test_module_and_class_bodies_are_reported_as_import_time() -> None:
    old = "X = 1\n\nclass C:\n    Y = 2\n\n    def m(self):\n        return 1\n"
    new = old.replace("X = 1", "X = 1\nZ = 9").replace("Y = 2", "Y = 3")
    changes = statement_diff.statement_changes("m.py", old, new)
    assert set(changes) == {MODULE_BLOCK, "C"}
    assert all(change.import_time for change in changes.values())


def test_a_source_that_does_not_parse_is_an_error_not_no_change() -> None:
    with pytest.raises(SyntaxError):
        statement_diff.statement_changes("m.py", OLD, "def f(:\n")
    with pytest.raises(SyntaxError):
        statement_diff.statement_changes("m.py", "def f(:\n", OLD)


def test_a_digest_that_moved_with_nothing_to_show_for_it_asks_for_every_test() -> None:
    """Two versions of a block with the same statements and the same header
    and different digests: nothing this module looks at explains the
    difference, so it must not be read as "nothing changed"."""
    parsed = statement_diff._parse("m.py", OLD)["f"]
    change = statement_diff._compare(parsed, parsed)
    assert (change.kind, change.why) == (
        Kind.WHOLE, "it changed in a way none of its statements shows")
    assert change.old_digest == parsed.block.digest


# ── soundness, by execution ─────────────────────────────────────────────────

_VALUES = ("x", "y", "a", "b", "0", "1", "2")
_expression = st.one_of(
    st.sampled_from(_VALUES),
    st.tuples(st.sampled_from(_VALUES), st.sampled_from(("+", "-", "*")),
              st.sampled_from(_VALUES)).map(" ".join))
_condition = st.tuples(st.sampled_from(_VALUES), st.sampled_from(("<", "==", ">")),
                       st.sampled_from(_VALUES)).map(" ".join)
_simple = st.one_of(
    st.tuples(st.sampled_from(("a", "b")), _expression).map(lambda t: f"{t[0]} = {t[1]}"),
    _expression.map(lambda e: f"return {e}"),
    st.just("pass"),
    st.just('"a bare constant"'),
)


def _body(depth: int) -> st.SearchStrategy[list[Any]]:
    """A list of statements: a string, or (kind, condition, body, orelse)."""
    if depth == 0:
        return st.lists(_simple, min_size=1, max_size=3)
    compound = st.tuples(st.sampled_from(("if", "try")), _condition, _body(depth - 1),
                         st.one_of(st.none(), _body(depth - 1)))
    return st.lists(st.one_of(_simple, compound), min_size=1, max_size=4)


def _render(body: list[Any], indent: int = 1) -> list[str]:
    pad = "    " * indent
    lines: list[str] = []
    for item in body:
        if isinstance(item, str):
            lines.append(pad + item)
        elif item[0] == "if":
            lines.append(f"{pad}if {item[1]}:")
            lines += _render(item[2], indent + 1)
            if item[3] is not None:
                lines.append(f"{pad}else:")
                lines += _render(item[3], indent + 1)
        else:
            lines.append(f"{pad}try:")
            lines.append(f"{pad}    a = a // ({item[1]})")
            lines += _render(item[2], indent + 1)
            lines.append(f"{pad}except ZeroDivisionError:")
            lines += _render(item[3] if item[3] is not None else ["pass"], indent + 1)
    return lines


def _source(body: list[Any]) -> str:
    return "\n".join(["def f(x, y):", "    a = 0", "    b = 0", *_render(body), "    return a - b"]) + "\n"


_GRID = [(x, y) for x in (-1, 0, 2) for y in (-1, 0, 1)]


def _mark(line: int) -> ast.stmt:
    """``_ran.add(<line>)``."""
    call = ast.Call(ast.Attribute(ast.Name("_ran", ast.Load()), "add", ast.Load()),
                    [ast.Constant(line)], [])
    return ast.Expr(call)


def _marked(body: list[ast.stmt]) -> list[ast.stmt]:
    """``body`` with a mark before every statement, at every depth: reaching a
    statement records its first line, which is the line a coverage run sees
    execute for it."""
    out: list[ast.stmt] = []
    for statement in body:
        out.append(_mark(statement.lineno))
        for name in ("body", "orelse", "finalbody"):
            inner = getattr(statement, name, None)
            if inner:
                setattr(statement, name, _marked(inner))
        for handler in getattr(statement, "handlers", []):
            handler.body = [_mark(handler.lineno), *_marked(handler.body)]
        out.append(statement)
    return out


def _run(source: str, args: tuple[int, int]) -> tuple[object, frozenset[int]]:
    """(what the call did, the lines of ``f`` it ran).

    The lines are recorded by code written into the function, NOT by
    ``sys.settrace``. A tracer set and then removed by a test also removes
    coverage's own on Python 3.11 and earlier: every test the worker ran
    afterwards went unmeasured, and CI (3.11) failed the coverage gate on a
    file its tests had fully run, while 3.12, which measures another way,
    showed nothing.
    """
    tree = ast.parse(source)
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    function.body = _marked(function.body)
    lines: set[int] = set()
    namespace: dict[str, Any] = {"_ran": lines}
    exec(compile(ast.fix_missing_locations(tree), "generated.py", "exec"), namespace)  # noqa: S102
    try:
        outcome: object = ("returned", namespace["f"](*args))
    except Exception as exc:
        outcome = ("raised", type(exc).__name__)
    return outcome, frozenset(lines)


def test_no_test_of_this_file_leaves_the_tracer_changed() -> None:
    """The runs above must leave ``sys.gettrace()`` as they found it (see
    ``_run``)."""
    before = sys.gettrace()
    assert _run("def f(x, y):\n    a = x\n    return a\n", (1, 2)) == (("returned", 1), {2, 3})
    assert sys.gettrace() is before


def _mutated(body: list[Any], data: st.DataObject, depth: int = 2) -> list[Any]:
    """``body`` with ONE change somewhere in it: a statement replaced, inserted
    or deleted, at this level or inside a compound statement."""
    body = list(body)
    index = data.draw(st.integers(0, len(body) - 1), label="where")
    item = body[index]
    choices = ["replace", "insert", "delete" if len(body) > 1 else "replace"]
    if not isinstance(item, str):
        choices += ["into body", "into else"]
    action = data.draw(st.sampled_from(choices), label="what")
    if action == "replace":
        body[index] = data.draw(_body(max(depth - 1, 0)), label="new")[0]
    elif action == "insert":
        body.insert(index, data.draw(_simple, label="new"))
    elif action == "delete":
        del body[index]
    elif action == "into body":
        body[index] = (item[0], item[1], _mutated(item[2], data, depth - 1), item[3])
    else:
        orelse = item[3] if item[3] is not None else ["pass"]
        body[index] = (item[0], item[1], item[2], _mutated(orelse, data, depth - 1))
    return body


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(old_body=_body(2), data=st.data())
def test_a_call_whose_result_changes_ran_a_statement_the_diff_names(
        old_body: list[Any], data: st.DataObject) -> None:
    """For a function, one change to it, and any input: if the result
    differs, the input ran (in the OLD version) a statement in ``ran``, or the
    diff asked for every test."""
    new_body = _mutated(old_body, data)
    old, new = _source(old_body), _source(new_body)
    change = statement_diff.statement_changes("generated.py", old, new).get("f")
    owner = statement_owner(blocks_for_source("generated.py", old))
    for args in _GRID:
        was, old_lines = _run(old, args)
        now, _ = _run(new, args)
        if was == now:
            continue
        assert change is not None, "the results differ and no change was reported"
        assert change.kind in (Kind.STATEMENTS, Kind.WHOLE), (change.kind, old, new)
        if change.kind is Kind.WHOLE:
            continue
        ran = {owner[line][1] for line in old_lines if line in owner and owner[line][0] == "f"}
        assert ran & change.ran, (
            f"f{args}: {was} became {now}, having run old statements {sorted(ran)}, "
            f"none of {sorted(change.ran)}\n--- old\n{old}--- new\n{new}")
