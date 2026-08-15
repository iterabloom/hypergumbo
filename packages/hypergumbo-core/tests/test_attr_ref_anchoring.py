# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-fafol: a ``module_attr_ref`` must be anchored to the callable that reads.

Propagation pairs a source and a sink that SHARE A CALLER. Every tree-sitter
analyzer passed the FILE pseudo-symbol as ``caller_symbol`` to
``emit_module_attribute_refs``, so an attribute source's ``src`` was the file
while the sink's was the function, and the two could never meet. Python was
unaffected because its own WI-guhok helper already anchors to the enclosing
callable.

Measured on one JS file holding both shapes in sibling functions:

    os.hostname()        -> fs.writeFileSync    FOUND     (a CALL, anchored
                                                           to its function)
    process.env.API_KEY  -> fs.writeFileSync    MISSED    (an ATTRIBUTE,
                                                           anchored to the file)

Same file, same sink, same claim — the only difference is which end anchors
where. After the fix the same fixture reports **2** flows instead of 1, and
the emitted edge's src is ``javascript:app.js:4-7:leakEnv:function``.

RESOLVED BY LINE SPAN rather than by walking tree-sitter ancestors to a
function node: span containment needs no per-language knowledge of which node
kinds are callables, so the rule lives once instead of in five copies that
drift, and the next analyzer inherits it.
"""

from __future__ import annotations

from hypergumbo_core.analyze.base import (
    _innermost_callable_at,
    symbols_by_path_index,
    symbols_for_path,
)
from hypergumbo_core.ir import Span, Symbol


def _sym(name: str, kind: str, lo: int, hi: int, path: str = "app.js") -> Symbol:
    return Symbol(
        id=f"javascript:{path}:{lo}-{hi}:{name}:{kind}",
        name=name, kind=kind, language="javascript", path=path,
        span=Span(start_line=lo, end_line=hi, start_col=0, end_col=0),
    )


class TestInnermostCallable:
    def test_the_containing_callable_is_found(self) -> None:
        syms = [_sym("leakEnv", "function", 4, 7),
                _sym("leakHost", "function", 9, 12)]
        assert _innermost_callable_at(5, syms).name == "leakEnv"
        assert _innermost_callable_at(10, syms).name == "leakHost"

    def test_the_innermost_span_wins(self) -> None:
        """A read inside a nested closure belongs to the closure, not to the
        function that encloses it — otherwise a sink in the outer function
        would appear to share a caller with a source it cannot see."""
        syms = [_sym("outer", "function", 1, 20),
                _sym("inner", "function", 5, 9)]
        assert _innermost_callable_at(7, syms).name == "inner"
        assert _innermost_callable_at(3, syms).name == "outer"

    def test_a_module_level_read_has_no_owner(self) -> None:
        """None is a REAL answer and is load-bearing: a top-of-file
        ``process.env`` read genuinely belongs to the file, and the caller
        keeps its existing pseudo-symbol for exactly that case. The fix must
        not invent a callable that is not there."""
        syms = [_sym("leakEnv", "function", 4, 7)]
        assert _innermost_callable_at(1, syms) is None
        assert _innermost_callable_at(99, syms) is None

    def test_non_callable_symbols_are_ignored(self) -> None:
        """A class or variable spanning the read cannot own a dataflow."""
        syms = [_sym("MyClass", "class", 1, 30), _sym("CONST", "variable", 1, 30)]
        assert _innermost_callable_at(5, syms) is None

    def test_no_symbols_at_all_is_not_an_error(self) -> None:
        assert _innermost_callable_at(5, None) is None
        assert _innermost_callable_at(5, []) is None


class TestPathIndex:
    """The key format is NOT stable across the pipeline, and assuming it was
    cost a silent no-op.

    At the point js_ts builds this index the symbols carry ABSOLUTE paths,
    while the per-file name computed at the emit site is repo-RELATIVE. The
    lookup returned an empty list, the anchoring silently did nothing, and the
    measured verdict was unchanged — indistinguishable from the fix not
    working at all. Both halves live in one module so neither can drift into
    assuming a format the other does not produce.
    """

    def test_lookup_tries_each_spelling_in_turn(self) -> None:
        abs_path = "/repo/app.js"
        idx = symbols_by_path_index([_sym("f", "function", 1, 2, abs_path)])
        assert symbols_for_path(idx, abs_path, "app.js")
        assert symbols_for_path(idx, "app.js", abs_path), (
            "order must not matter — either spelling must find the file"
        )

    def test_an_unknown_path_returns_empty_rather_than_raising(self) -> None:
        idx = symbols_by_path_index([_sym("f", "function", 1, 2)])
        assert symbols_for_path(idx, "nope.js") == []

    def test_symbols_are_grouped_per_file(self) -> None:
        idx = symbols_by_path_index([
            _sym("a", "function", 1, 2, "a.js"),
            _sym("b", "function", 1, 2, "b.js"),
            _sym("c", "function", 4, 5, "a.js"),
        ])
        assert len(symbols_for_path(idx, "a.js")) == 2
        assert len(symbols_for_path(idx, "b.js")) == 1
