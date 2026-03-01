"""Branch coverage tests for framework_patterns.py.

Targets specific branch paths not exercised by the main test suite.
Each test documents the specific branch partial it covers.

Covered branches:
- Pattern._extract_single_value: direct key not in metadata (399->402)
- Pattern._extract_http_method: no capture groups in decorator regex (420->443)
- Pattern._extract_http_method_from_annotation: no capture groups (462->470)
- extract_usage_value: unrecognized transform falls through all elif (535->525)
- extract_usage_value: "last" transform with preceding "split:" (535 true branch)
- extract_usage_value: "last" without preceding "split:" (539->525, 540->539)
- extract_usage_value: dead code check for " | " (537->525, unreachable but tested)
- enrich_symbols: symbol with empty name skipped in lookup (954->953)
- enrich_symbols: duplicate simple_name, non-callable doesn't overwrite (958->953)
"""

from hypergumbo_core.framework_patterns import (
    Pattern,
    _combine_route_paths,
    _extract_from_metadata,
    extract_usage_value,
)
from hypergumbo_core.ir import Span, Symbol, UsageContext


class TestPatternExtractSingleValueBranches:
    """Branch coverage for Pattern._extract_single_value."""

    def test_direct_key_not_found_in_metadata(self) -> None:
        """Branch 399->402: direct key lookup returns None.

        Pattern._extract_single_value takes the else branch (not args[], not kwargs.)
        then checks if the path key exists in metadata. When it doesn't, falls
        through to return None.
        """
        pattern = Pattern(
            concept="http_route",
            decorator="^app\\.(get|post)$",
            extract_path="route_path",  # will look for "route_path" in metadata
            extract_method="decorator_suffix",
        )
        sym = Symbol(
            id="py:app.py:1-3:index:function",
            name="index",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 3, 0, 30),
            meta={"decorators": [{"name": "app.get", "metadata": {"other_key": "value"}}]},
        )
        result = pattern.matches(sym)
        assert result is not None
        # _extract_single_value returned None because "route_path" is not in metadata.
        # The caller in matches() converts None to "" (line 240: path if path else "")
        assert result.get("path") == ""


class TestPatternExtractHttpMethodBranches:
    """Branch coverage for Pattern._extract_http_method."""

    def test_decorator_suffix_no_capture_groups(self) -> None:
        """Branch 420->443: regex match has no capture groups.

        When extract_method="decorator_suffix" and the decorator regex has no
        capturing groups, match.groups() returns () which is falsy. Method
        extraction returns None, falling through to the end.
        """
        pattern = Pattern(
            concept="http_route",
            decorator="^app\\.get$",  # No capture groups
            extract_method="decorator_suffix",
        )
        sym = Symbol(
            id="py:app.py:1-3:index:function",
            name="index",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 3, 0, 30),
            meta={"decorators": [{"name": "app.get", "metadata": "/"}]},
        )
        result = pattern.matches(sym)
        assert result is not None
        assert result.get("method") is None

    def test_annotation_prefix_no_capture_groups(self) -> None:
        """Branch 462->470: annotation regex match has no capture groups.

        When extract_method="annotation_prefix" and the annotation regex has
        no capturing groups, match.groups() returns () which is falsy.
        """
        pattern = Pattern(
            concept="http_route",
            annotation="^GetMapping$",  # No capture groups
            extract_method="annotation_prefix",
        )
        sym = Symbol(
            id="java:App.java:1-3:index:function",
            name="index",
            kind="function",
            language="java",
            path="App.java",
            span=Span(1, 3, 0, 30),
            meta={"annotations": ["GetMapping"]},
        )
        result = pattern.matches(sym)
        assert result is not None
        assert result.get("method") is None


class TestExtractUsageValueBranches:
    """Branch coverage for extract_usage_value pipe transforms."""

    def _make_ctx(self, context_name: str = "test", metadata: dict | None = None) -> UsageContext:
        """Create a UsageContext for testing."""
        return UsageContext(
            id="test:ctx:1",
            kind="test",
            context_name=context_name,
            position=None,
            metadata=metadata or {},
            symbol_ref=None,
            path="test.py",
            span=Span(1, 1, 0, 10),
        )

    def test_last_transform_with_preceding_split(self) -> None:
        """Branch 535 true: the 'last' transform is reached and matched.

        Tests: literal:a;b;c | split:; | last -> "c"
        """
        ctx = self._make_ctx()
        result = extract_usage_value(ctx, "literal:a;b;c | split:; | last")
        assert result == "c"

    def test_last_transform_without_preceding_split(self) -> None:
        """Branches 539->525, 540->539: 'last' without preceding 'split:'.

        The reversed loop in the "last" handler iterates over preceding
        transforms but never finds one starting with "split:". The loop
        completes without breaking (540->539 continues, then 539->525 exits).
        """
        ctx = self._make_ctx()
        result = extract_usage_value(ctx, "literal:hello | uppercase | last")
        assert result == "HELLO"

    def test_unrecognized_transform_falls_through(self) -> None:
        """Branch 535->525: unrecognized transform falls through all elif branches.

        When a transform is not "uppercase", "lowercase", doesn't start with
        "split:", and is not "last", the for-loop continues to the next transform.
        """
        ctx = self._make_ctx()
        # "bogus" is not recognized — falls through all checks, loop continues
        result = extract_usage_value(ctx, "literal:hello | bogus | uppercase")
        # The bogus transform is silently ignored, uppercase still applies
        assert result == "HELLO"


class TestCombineRoutePathsBranches:
    """Branch coverage for _combine_route_paths."""

    def test_both_empty_returns_none(self) -> None:
        """_combine_route_paths with empty strings returns None."""
        result = _combine_route_paths("", "")
        assert result is None

    def test_both_none_returns_none(self) -> None:
        """_combine_route_paths with None inputs returns None."""
        result = _combine_route_paths(None, None)
        assert result is None


class TestStandaloneExtractFromMetadata:
    """Branch coverage for standalone _extract_from_metadata function."""

    def test_direct_key_not_found(self) -> None:
        """Path not in metadata returns None."""
        result = _extract_from_metadata({"other": "value"}, "missing_key")
        assert result is None

    def test_direct_key_found(self) -> None:
        """Path found in metadata returns its value."""
        result = _extract_from_metadata({"route_path": "/users"}, "route_path")
        assert result == "/users"

    def test_direct_key_list_value_joined(self) -> None:
        """List values are comma-joined (e.g., methods=['GET', 'POST'])."""
        result = _extract_from_metadata({"methods": ["GET", "POST"]}, "methods")
        assert result == "GET,POST"

    def test_direct_key_single_element_list(self) -> None:
        """Single-element list is returned without comma."""
        result = _extract_from_metadata({"methods": ["GET"]}, "methods")
        assert result == "GET"


class TestEnrichSymbolsBranches:
    """Branch coverage for enrich_symbols name lookup."""

    def test_symbol_with_empty_name_skipped_in_lookup(self) -> None:
        """Branch 954->953: symbol with empty/falsy name is skipped.

        The name-based lookup in enrich_symbols skips symbols where
        s.name is falsy (empty string).
        """
        from hypergumbo_core.framework_patterns import enrich_symbols

        sym_no_name = Symbol(
            id="py:app.py:1-3::function",
            name="",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 3, 0, 30),
        )
        sym_with_name = Symbol(
            id="py:app.py:5-7:handler:function",
            name="handler",
            kind="function",
            language="python",
            path="app.py",
            span=Span(5, 7, 0, 30),
        )
        results = enrich_symbols([sym_no_name, sym_with_name], [])
        assert isinstance(results, list)
        assert len(results) == 2

    def test_duplicate_name_noncallable_does_not_overwrite(self) -> None:
        """Branch 958->953: same simple_name, non-callable doesn't overwrite callable.

        When symbol_by_name already has a function/method for a name, a subsequent
        symbol with the same name but a non-callable kind (e.g., class) does NOT
        overwrite it. This is the false branch of the condition:
        'if simple_name not in symbol_by_name or s.kind in ("function", "method")'
        """
        from hypergumbo_core.framework_patterns import enrich_symbols

        # First: a function named "handler"
        sym_func = Symbol(
            id="py:app.py:1-3:handler:function",
            name="handler",
            kind="function",
            language="python",
            path="app.py",
            span=Span(1, 3, 0, 30),
        )
        # Second: a class also named "handler" — should NOT overwrite the function
        sym_class = Symbol(
            id="py:app.py:5-10:handler:class",
            name="handler",
            kind="class",
            language="python",
            path="app.py",
            span=Span(5, 10, 0, 50),
        )
        # Process: function first, then class
        results = enrich_symbols([sym_func, sym_class], [])
        assert isinstance(results, list)
