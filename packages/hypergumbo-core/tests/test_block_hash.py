# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for AST-block hashing, the change unit for coverage-directed selection.

WHAT THESE PIN, and why it is not "does hashing work". A coverage-directed
selector maps CHANGED CODE back to the tests that executed it. Keying that map
on ``(file, line number)`` makes every insertion above a function look like a
change to it, so a one-line import addition selects the whole file's tests. The
fix is to key on a hash of the block's STRUCTURE — but the naive version of that
(hash each line's text) trades the false positive for a false NEGATIVE: move a
line from one function to another and its hash is unchanged, so nothing is
selected while behaviour changed.

So the property set below is deliberately two-sided. Every test that asserts a
digest is STABLE has a partner asserting some nearby edit DOES perturb it, because
a hash that never changes would pass every stability test on its own.
"""
from __future__ import annotations

from textwrap import dedent

from hypergumbo_core.block_hash import blocks_for_source, line_owner


def _by_name(src: str) -> dict[str, str]:
    """``qualified name -> digest`` for a source string."""
    return {b.name: b.digest for b in blocks_for_source("m.py", dedent(src))}


BASE = """
    import os

    CONST = 1


    def alpha(x):
        return x + 1


    class Holder:
        attr = 2

        def beta(self, y):
            return y * 2
    """


class TestWhatIsABlock:
    def test_every_def_and_class_becomes_its_own_block(self) -> None:
        names = set(_by_name(BASE))
        assert {"alpha", "Holder", "Holder.beta"} <= names

    def test_module_level_statements_get_a_synthetic_block(self) -> None:
        """``import os`` and ``CONST = 1`` belong to something, or a change to
        them maps to no test at all."""
        assert "<module>" in _by_name(BASE)

    def test_a_method_is_qualified_by_its_class(self) -> None:
        """Two classes with a ``run`` method must not collide into one block."""
        names = set(_by_name("""
            class A:
                def run(self): return 1
            class B:
                def run(self): return 2
        """))
        assert {"A.run", "B.run"} <= names


class TestStabilityUnderIrrelevantEdits:
    """The reason line numbers were rejected as the key."""

    def test_inserting_a_line_above_does_not_perturb_a_later_block(self) -> None:
        after = BASE.replace("import os", "import os\n    import sys")
        assert _by_name(BASE)["alpha"] == _by_name(after)["alpha"]
        assert _by_name(BASE)["Holder.beta"] == _by_name(after)["Holder.beta"]

    def test_reformatting_does_not_perturb_a_block(self) -> None:
        """Whitespace is not behaviour. Hashing source TEXT would fail this."""
        after = BASE.replace("return x + 1", "return x+1")
        assert _by_name(BASE)["alpha"] == _by_name(after)["alpha"]

    def test_a_comment_only_edit_does_not_perturb_a_block(self) -> None:
        after = BASE.replace("return x + 1", "return x + 1  # noqa")
        assert _by_name(BASE)["alpha"] == _by_name(after)["alpha"]


class TestSensitivityToRealChanges:
    """The partners. Without these, a constant hash passes the section above."""

    def test_changing_a_body_perturbs_that_block(self) -> None:
        after = BASE.replace("return x + 1", "return x + 2")
        assert _by_name(BASE)["alpha"] != _by_name(after)["alpha"]

    def test_changing_a_body_does_NOT_perturb_its_siblings(self) -> None:
        """Otherwise one edit selects the whole file and this buys nothing."""
        after = BASE.replace("return x + 1", "return x + 2")
        assert _by_name(BASE)["Holder.beta"] == _by_name(after)["Holder.beta"]
        assert _by_name(BASE)["<module>"] == _by_name(after)["<module>"]

    def test_changing_a_signature_perturbs_the_block(self) -> None:
        after = BASE.replace("def alpha(x):", "def alpha(x, y=0):")
        assert _by_name(BASE)["alpha"] != _by_name(after)["alpha"]

    def test_adding_a_decorator_perturbs_the_block(self) -> None:
        after = BASE.replace("def alpha(x):", "@staticmethod\n    def alpha(x):")
        assert _by_name(BASE)["alpha"] != _by_name(after)["alpha"]


class TestMovingCodeIsDetected:
    """The failure mode that killed per-line hashing.

    Moving a function between classes leaves the FunctionDef node byte-identical,
    so its digest alone cannot see the move. Detection comes from the KEY (its
    qualified name) and from the parent's own digest, which lists its children.
    """

    #: Both classes carry a stable non-def member on purpose. Using ``pass`` as
    #: filler confounds the test: eliding the def leaves one body ``[]`` and the
    #: other ``[Pass()]``, so the digests differ for a reason that has nothing
    #: to do with the move.
    MOVED_FROM = """
        class A:
            marker = 0
            def helper(self): return 1
        class B:
            marker = 0
    """
    MOVED_TO = """
        class A:
            marker = 0
        class B:
            marker = 0
            def helper(self): return 1
    """

    def test_the_old_key_disappears(self) -> None:
        assert "A.helper" in _by_name(self.MOVED_FROM)
        assert "A.helper" not in _by_name(self.MOVED_TO)

    def test_the_new_key_appears(self) -> None:
        assert "B.helper" in _by_name(self.MOVED_TO)

    def test_the_parents_are_NOT_perturbed_and_that_is_deliberate(self) -> None:
        """Children are removed from a parent's digest entirely, so a move is
        detected by the vanished/appeared KEY, not by the parents changing.

        The earlier design kept a name marker so parents would change. It was
        wrong: the parent of a module-level def is the import-time module
        block, which is credited to EVERY test in the file, so any structural
        edit selected the whole file's suite.
        """
        before, after = _by_name(self.MOVED_FROM), _by_name(self.MOVED_TO)
        assert before["A"] == after["A"]


class TestConditionalDefs:
    """A def inside an ``if`` is not a direct child of the enclosing block."""

    COND = """
        if True:
            def maybe():
                return 1
    """

    def test_it_is_still_found_as_a_block(self) -> None:
        assert "maybe" in _by_name(self.COND)

    def test_its_body_does_not_leak_into_the_module_digest(self) -> None:
        after = self.COND.replace("return 1", "return 2")
        assert _by_name(self.COND)["<module>"] == _by_name(after)["<module>"]


class TestNesting:
    NESTED = """
        def outer():
            def inner():
                return 1
            return inner()
    """

    def test_a_nested_def_is_its_own_block(self) -> None:
        assert "outer.inner" in _by_name(self.NESTED)

    def test_editing_the_inner_body_does_not_perturb_the_outer(self) -> None:
        """The parent's digest elides its children's BODIES, keeping only their
        names — so the child owns its own changes and the parent owns structure."""
        after = self.NESTED.replace("return 1", "return 2")
        assert _by_name(self.NESTED)["outer"] == _by_name(after)["outer"]
        assert _by_name(self.NESTED)["outer.inner"] != _by_name(after)["outer.inner"]

    #: Written out rather than produced by ``.replace()`` on NESTED: the
    #: obvious target string matches INSIDE the deeper-indented line, so the
    #: inserted def lands at the wrong column and ``dedent`` then computes a
    #: different common prefix. The first version of this test failed for that
    #: reason and not because the digest was wrong.
    NESTED_PLUS = """
        def outer():
            def inner():
                return 1
            def other():
                return 9
            return inner()
    """

    def test_adding_a_nested_def_does_NOT_perturb_the_outer(self) -> None:
        """A new block carries no test history, so perturbing the parent would
        only over-select. The new key is reported instead."""
        before, after = _by_name(self.NESTED), _by_name(self.NESTED_PLUS)
        assert before["outer"] == after["outer"]
        assert "outer.other" in after and "outer.other" not in before

    def test_adding_a_sibling_does_not_perturb_the_existing_child(self) -> None:
        """The control for the test above: only the PARENT moved."""
        before, after = _by_name(self.NESTED), _by_name(self.NESTED_PLUS)
        assert before["outer.inner"] == after["outer.inner"]


class TestAsyncDefs:
    """``async def`` is a distinct AST node and needs its own elision arm — an
    omission coverage caught, not review."""

    ASYNC = """
        async def fetch(url):
            return await get(url)
    """

    def test_an_async_def_is_its_own_block(self) -> None:
        assert "fetch" in _by_name(self.ASYNC)

    def test_an_async_body_edit_does_not_perturb_the_module(self) -> None:
        """Proves the async arm ELIDES rather than falling through — without
        it, every async body edit would look like a module-level change."""
        after = self.ASYNC.replace("await get(url)", "await post(url)")
        assert _by_name(self.ASYNC)["<module>"] == _by_name(after)["<module>"]
        assert _by_name(self.ASYNC)["fetch"] != _by_name(after)["fetch"]


class TestLineOwnership:
    """The join to coverage: a covered LINE must resolve to exactly one block."""

    def test_the_innermost_block_owns_a_line(self) -> None:
        src = dedent(TestNesting.NESTED)
        blocks = blocks_for_source("m.py", src)
        owner = line_owner(blocks)
        inner_body = src.splitlines().index("        return 1") + 1
        assert owner[inner_body] == "outer.inner", (
            "an outer function must not claim its nested function's lines, or "
            "editing the inner one selects the outer one's tests too"
        )

    def test_module_level_lines_belong_to_the_module_block(self) -> None:
        src = dedent(BASE)
        owner = line_owner(blocks_for_source("m.py", src))
        assert owner[src.splitlines().index("CONST = 1") + 1] == "<module>"

    def test_a_decorator_line_belongs_to_the_ENCLOSING_scope(self) -> None:
        """Decorators and the ``def`` header execute at IMPORT, in the scope
        that contains them — so they are module-level code, not the callable's.

        This is the inverse of what the first version asserted, and getting it
        backwards was the bug: every ``def`` line runs at import, so while a
        function owned its own header it looked like import-time code and was
        credited to every test that touched the file.
        """
        src = dedent("""
            @property
            def thing(self):
                return 1
        """)
        owner = line_owner(blocks_for_source("m.py", src))
        lines = src.splitlines()
        assert owner[lines.index("@property") + 1] == "<module>"
        assert owner[lines.index("def thing(self):") + 1] == "<module>"
        assert owner[lines.index("    return 1") + 1] == "thing"

    def test_every_statement_line_has_an_owner(self) -> None:
        """A line with no owner is a change that maps to no test."""
        src = dedent(BASE)
        owner = line_owner(blocks_for_source("m.py", src))
        for i, line in enumerate(src.splitlines(), 1):
            if line.strip() and not line.strip().startswith("#"):
                assert i in owner, f"line {i} ({line!r}) has no owning block"
