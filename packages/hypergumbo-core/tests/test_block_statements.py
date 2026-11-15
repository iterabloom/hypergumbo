# SPDX-License-Identifier: AGPL-3.0-or-later
"""A block's statements, numbered so that the numbers survive what its digest survives.

The selection index records, per test and block, WHICH of the block's
statements the test ran (``selection_index``, format 4). A record made today
is read later, against a file that has been edited in between, so a statement
needs a name that an unrelated edit does not move. A line number is not one,
and neither is a line's offset inside its function: the block digest ignores
comments, blank lines and formatting (it is a dump of the AST without
positions), so adding a comment inside a function would leave the digest
equal and shift every offset below it, and a stale record would look current.

So a statement is named by its ORDINAL: its position in a walk of the block's
own statements, nested blocks left out, exactly the tree the digest is
computed from. Two-sided, like the digest tests:

* an edit the digest ignores (a comment, a blank line, a reflow, code added
  above the block, a nested function added or removed) leaves every ordinal
  where it was;
* an edit that changes the statements changes the digest, which is how a
  reader knows the ordinals no longer apply.

And line ownership: a line belongs to the innermost statement of the innermost
block around it; the ``def`` line and decorators of a NESTED block belong to
no statement (the nested block is not part of its parent's digest, so it can
have no ordinal there).
"""
from __future__ import annotations

import ast
from textwrap import dedent

from hypergumbo_core.block_hash import Block, blocks_for_source, statement_owner


def _blocks(src: str) -> dict[str, Block]:
    return {b.name: b for b in blocks_for_source("m.py", dedent(src).lstrip("\n"))}


def _owner(src: str) -> dict[int, tuple[str, int]]:
    return statement_owner(blocks_for_source("m.py", dedent(src).lstrip("\n")))


def _texts(src: str, name: str) -> list[str]:
    """Each statement of block ``name`` in ordinal order, as the first line of
    its NORMALIZED text (``ast.unparse``), so a reflow reads the same."""
    source = dedent(src).lstrip("\n")
    at = {(node.lineno, node.end_lineno): node for node in ast.walk(ast.parse(source))
          if isinstance(node, (ast.stmt, ast.ExceptHandler))}
    block = {b.name: b for b in blocks_for_source("m.py", source)}[name]
    return [ast.unparse(at[span]).splitlines()[0] for span in block.statements]


PLAIN = """
    import os

    def alpha(x):
        total = x + 1
        if total > 3:
            total = 0
        else:
            total += 1
        return total
    """


class TestWhatIsNumbered:
    def test_statements_are_numbered_in_source_order_with_bodies_inside(self) -> None:
        assert _texts(PLAIN, "alpha") == [
            "total = x + 1", "if total > 3:", "total = 0", "total += 1", "return total"]

    def test_a_statement_spans_all_its_lines(self) -> None:
        block = _blocks("""
            def alpha(x):
                value = compute(
                    x,
                    2,
                )
                return value
            """)["alpha"]
        assert block.statements == ((2, 5), (6, 6))

    def test_an_except_clause_has_its_own_number(self) -> None:
        """``try:`` being entered and a handler running are different facts."""
        assert _texts("""
            def alpha(x):
                try:
                    risky(x)
                except ValueError:
                    recover()
                finally:
                    done()
            """, "alpha") == [
            "try:", "risky(x)", "except ValueError:", "recover()", "done()"]

    def test_the_module_block_numbers_module_statements_only(self) -> None:
        assert _texts(PLAIN, "<module>") == ["import os"]

    def test_a_nested_block_is_not_one_of_its_parents_statements(self) -> None:
        src = """
            def outer(x):
                before = 1
                def inner(y):
                    return y + before
                after = inner(x)
                return after
            """
        assert _texts(src, "outer") == ["before = 1", "after = inner(x)", "return after"]
        assert _texts(src, "outer.inner") == ["return y + before"]

    def test_a_block_with_no_statement_of_its_own_has_none(self) -> None:
        blocks = _blocks("""
            class Holder:
                def beta(self):
                    return 1
            """)
        assert blocks["Holder"].statements == ()
        assert len(blocks["Holder.beta"].statements) == 1


class TestOrdinalsSurviveWhatTheDigestSurvives:
    BASE = """
        def alpha(x):
            total = x + 1
            if total > 3:
                total = 0
            return total
        """

    def _same(self, edited: str) -> None:
        before, after = _blocks(self.BASE)["alpha"], _blocks(edited)["alpha"]
        assert before.digest == after.digest, "the edit must be one the digest ignores"
        assert _texts(self.BASE, "alpha") == _texts(edited, "alpha")
        assert before.statements != after.statements, "the edit must move lines"

    def test_a_comment_inside_the_block(self) -> None:
        self._same("""
            def alpha(x):
                total = x + 1
                # why three: see the ADR
                if total > 3:
                    total = 0
                return total
            """)

    def test_blank_lines_and_a_reflowed_statement(self) -> None:
        self._same("""
            def alpha(x):

                total = (
                    x + 1
                )
                if total > 3:
                    total = 0

                return total
            """)

    def test_code_added_above_the_block(self) -> None:
        self._same("""
            import os

            LIMIT = 3

            def alpha(x):
                total = x + 1
                if total > 3:
                    total = 0
                return total
            """)

    def test_a_nested_function_added_between_two_statements(self) -> None:
        """The digest leaves nested blocks out, so the numbering must too:
        were ``helper`` numbered, ``return total`` would move from 3 to 4
        under a digest that says nothing changed."""
        self._same("""
            def alpha(x):
                total = x + 1
                if total > 3:
                    total = 0
                def helper():
                    return 1
                return total
            """)

    def test_a_changed_statement_changes_the_digest(self) -> None:
        """The other side: when the numbering no longer applies, the digest
        says so."""
        edited = """
            def alpha(x):
                total = x + 1
                extra = 2
                if total > 3:
                    total = 0
                return total
            """
        assert _blocks(self.BASE)["alpha"].digest != _blocks(edited)["alpha"].digest
        assert _texts(edited, "alpha")[1] == "extra = 2"


class TestWhichStatementALineBelongsTo:
    def test_innermost_statement_of_the_innermost_block(self) -> None:
        owner = _owner("""
            import os

            def alpha(x):
                total = x + 1
                if total > 3:
                    total = 0
                return total
            """)
        assert owner[1] == ("<module>", 0)
        assert owner[4] == ("alpha", 0)
        assert owner[5] == ("alpha", 1)   # the ``if`` line is the If itself
        assert owner[6] == ("alpha", 2)   # its body line is the inner statement
        assert owner[7] == ("alpha", 3)

    def test_every_line_of_a_multi_line_statement(self) -> None:
        owner = _owner("""
            def alpha(x):
                value = compute(
                    x,
                    2,
                )
                return value
            """)
        assert [owner[n] for n in (2, 3, 4, 5)] == [("alpha", 0)] * 4
        assert owner[6] == ("alpha", 1)

    def test_a_def_line_and_blank_lines_belong_to_no_statement(self) -> None:
        owner = _owner("""
            import os

            def alpha(x):
                return x
            """)
        assert 2 not in owner   # blank
        assert 3 not in owner   # ``def alpha`` at module level: no module statement

    def test_a_nested_blocks_header_belongs_to_no_statement(self) -> None:
        """Not to the parent (it has no ordinal there) and not to an enclosing
        compound statement that merely surrounds it."""
        owner = _owner("""
            def outer(flag):
                if flag:
                    @decorate
                    def inner(y):
                        return y
                    result = inner(1)
                return result
            """)
        assert owner[2] == ("outer", 0)
        assert 3 not in owner and 4 not in owner
        assert owner[5] == ("outer.inner", 0)
        assert owner[6] == ("outer", 1)
        assert owner[7] == ("outer", 2)

    def test_a_class_inside_a_function(self) -> None:
        owner = _owner("""
            def build():
                class Local:
                    size = 3
                    def get(self):
                        return self.size
                return Local
            """)
        assert 2 not in owner
        assert owner[3] == ("build.Local", 0)
        assert 4 not in owner
        assert owner[5] == ("build.Local.get", 0)
        assert owner[6] == ("build", 0)

    def test_an_except_line_belongs_to_its_handler(self) -> None:
        owner = _owner("""
            def alpha(x):
                try:
                    risky(x)
                except ValueError:
                    recover()
            """)
        assert owner[2] == ("alpha", 0)
        assert owner[3] == ("alpha", 1)
        assert owner[4] == ("alpha", 2)
        assert owner[5] == ("alpha", 3)


class TestBlockEqualityIsUnchanged:
    def test_statement_spans_are_not_part_of_a_blocks_identity(self) -> None:
        """Callers compare and hash blocks by path, name, lines and digest;
        the new fields must not make a moved block unequal to itself twice."""
        one = Block(path="m.py", name="f", first_line=1, last_line=2, digest="d")
        two = Block(path="m.py", name="f", first_line=1, last_line=2, digest="d",
                    statements=((1, 1),), node_first_line=1)
        assert one == two and hash(one) == hash(two)
