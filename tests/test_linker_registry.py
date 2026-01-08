"""Tests for the linkers.registry module."""

from pathlib import Path

import pytest

from hypergumbo.ir import AnalysisRun, Edge, Symbol
from hypergumbo.linkers.registry import (
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    check_linker_requirements,
    clear_registry,
    get_all_linkers,
    get_linker,
    list_registered,
    register_linker,
    run_all_linkers,
    run_linker,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Clear registry before and after each test."""
    clear_registry()
    yield
    clear_registry()


class TestLinkerContext:
    """Tests for LinkerContext dataclass."""

    def test_defaults(self):
        """Default values are set correctly."""
        ctx = LinkerContext(repo_root=Path("/test"))
        assert ctx.repo_root == Path("/test")
        assert ctx.symbols == []
        assert ctx.edges == []
        assert ctx.captured_symbols == {}

    def test_custom_values(self):
        """Custom values can be set."""
        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=["sym1"],  # type: ignore
            edges=["edge1"],  # type: ignore
            captured_symbols={"c": ["c_sym"]},  # type: ignore
        )
        assert ctx.symbols == ["sym1"]
        assert ctx.edges == ["edge1"]
        assert ctx.captured_symbols == {"c": ["c_sym"]}


class TestLinkerContextSymbolLookup:
    """Tests for LinkerContext symbol lookup methods."""

    def _make_symbol(
        self, name: str, lang: str = "go", kind: str = "function"
    ) -> Symbol:
        """Helper to create a symbol."""
        from hypergumbo.ir import Span, Symbol

        sym_id = f"{lang}:test.go:1-10:{name}:{kind}"
        return Symbol(
            id=sym_id,
            name=name,
            kind=kind,
            language=lang,
            path="test.go",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test",
        )

    def test_get_symbol_by_id_found(self):
        """get_symbol_by_id returns symbol when found."""
        sym = self._make_symbol("foo")
        ctx = LinkerContext(repo_root=Path("/test"), symbols=[sym])

        result = ctx.get_symbol_by_id(sym.id)
        assert result is not None
        assert result.name == "foo"

    def test_get_symbol_by_id_not_found(self):
        """get_symbol_by_id returns None when not found."""
        sym = self._make_symbol("foo")
        ctx = LinkerContext(repo_root=Path("/test"), symbols=[sym])

        result = ctx.get_symbol_by_id("nonexistent:id")
        assert result is None

    def test_find_symbols_by_name_found(self):
        """find_symbols_by_name returns matching symbols."""
        sym1 = self._make_symbol("RegisterUserServer")
        sym2 = self._make_symbol("RegisterUserServer", lang="python")
        sym3 = self._make_symbol("OtherFunc")
        ctx = LinkerContext(repo_root=Path("/test"), symbols=[sym1, sym2, sym3])

        result = ctx.find_symbols_by_name("RegisterUserServer")
        assert len(result) == 2
        assert all(s.name == "RegisterUserServer" for s in result)

    def test_find_symbols_by_name_not_found(self):
        """find_symbols_by_name returns empty list when not found."""
        sym = self._make_symbol("foo")
        ctx = LinkerContext(repo_root=Path("/test"), symbols=[sym])

        result = ctx.find_symbols_by_name("nonexistent")
        assert result == []

    def test_find_symbols_by_name_with_dot_qualified(self):
        """find_symbols_by_name indexes by short name from qualified names."""
        from hypergumbo.ir import Span, Symbol

        # Create a symbol with qualified name
        sym = Symbol(
            id="go:test.go:1-10:MyClass.method:method",
            name="MyClass.method",
            kind="method",
            language="go",
            path="test.go",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test",
        )
        ctx = LinkerContext(repo_root=Path("/test"), symbols=[sym])

        # Should find by short name
        result = ctx.find_symbols_by_name("method")
        assert len(result) == 1
        assert result[0].name == "MyClass.method"

    def test_indexes_built_lazily(self):
        """Symbol indexes are built lazily on first lookup."""
        sym = self._make_symbol("foo")
        ctx = LinkerContext(repo_root=Path("/test"), symbols=[sym])

        # Before lookup, indexes should be None
        assert ctx._symbol_by_id is None
        assert ctx._symbols_by_name is None

        # After lookup, indexes should be built
        ctx.get_symbol_by_id(sym.id)
        assert ctx._symbol_by_id is not None
        assert ctx._symbols_by_name is not None


class TestLinkerContextUnresolvedEdges:
    """Tests for LinkerContext unresolved edge methods."""

    def _make_edge(self, src: str, dst: str, edge_type: str = "calls") -> Edge:
        """Helper to create an edge."""
        from hypergumbo.ir import Edge

        return Edge.create(
            src=src,
            dst=dst,
            edge_type=edge_type,
            line=1,
            confidence=0.9,
            origin="test",
            origin_run_id="test",
        )

    def test_get_unresolved_edges_finds_unresolved(self):
        """get_unresolved_edges finds edges with :unresolved suffix."""
        edge1 = self._make_edge(
            "go:main.go:1-10:main:function",
            "go:github.com/pkg:0-0:Foo:unresolved"
        )
        edge2 = self._make_edge(
            "go:main.go:1-10:main:function",
            "go:main.go:20-30:bar:function"  # resolved
        )
        ctx = LinkerContext(repo_root=Path("/test"), edges=[edge1, edge2])

        result = ctx.get_unresolved_edges()
        assert len(result) == 1
        assert result[0].dst.endswith(":unresolved")

    def test_get_unresolved_edges_filter_by_lang(self):
        """get_unresolved_edges can filter by language."""
        edge_go = self._make_edge(
            "go:main.go:1-10:main:function",
            "go:github.com/pkg:0-0:Foo:unresolved"
        )
        edge_py = self._make_edge(
            "python:main.py:1-10:main:function",
            "python:os:0-0:path:unresolved"
        )
        ctx = LinkerContext(repo_root=Path("/test"), edges=[edge_go, edge_py])

        result = ctx.get_unresolved_edges(lang="go")
        assert len(result) == 1
        assert result[0].dst.startswith("go:")

    def test_get_unresolved_edges_returns_empty_when_none(self):
        """get_unresolved_edges returns empty list when no unresolved edges."""
        edge = self._make_edge(
            "go:main.go:1-10:main:function",
            "go:main.go:20-30:bar:function"
        )
        ctx = LinkerContext(repo_root=Path("/test"), edges=[edge])

        result = ctx.get_unresolved_edges()
        assert result == []

    def test_parse_unresolved_dst_valid(self):
        """parse_unresolved_dst parses valid unresolved IDs."""
        ctx = LinkerContext(repo_root=Path("/test"))

        result = ctx.parse_unresolved_dst(
            "go:github.com/grpc/pkg:0-0:RegisterUserServer:unresolved"
        )
        assert result is not None
        assert result["lang"] == "go"
        assert result["package"] == "github.com/grpc/pkg"
        assert result["name"] == "RegisterUserServer"

    def test_parse_unresolved_dst_not_unresolved(self):
        """parse_unresolved_dst returns None for resolved IDs."""
        ctx = LinkerContext(repo_root=Path("/test"))

        result = ctx.parse_unresolved_dst("go:main.go:1-10:main:function")
        assert result is None

    def test_parse_unresolved_dst_too_few_parts(self):
        """parse_unresolved_dst returns None for malformed IDs."""
        ctx = LinkerContext(repo_root=Path("/test"))

        result = ctx.parse_unresolved_dst("go:foo:unresolved")
        assert result is None


class TestLinkerResult:
    """Tests for LinkerResult dataclass."""

    def test_defaults(self):
        """Default values are set correctly."""
        result = LinkerResult()
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_with_run(self):
        """Run can be set."""
        run = AnalysisRun(
            execution_id="test-exec",
            pass_id="test-linker",
            version="1.0",
        )
        result = LinkerResult(run=run)
        assert result.run is not None
        assert result.run.pass_id == "test-linker"


class TestRegisterLinker:
    """Tests for register_linker decorator."""

    def test_basic_registration(self):
        """Basic linker registration works."""

        @register_linker("test-linker")
        def link_test(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        linker = get_linker("test-linker")
        assert linker is not None
        assert linker.name == "test-linker"
        assert linker.priority == 50

    def test_with_priority(self):
        """Priority can be set."""

        @register_linker("early-linker", priority=10)
        def link_early(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        linker = get_linker("early-linker")
        assert linker is not None
        assert linker.priority == 10

    def test_with_description(self):
        """Description can be set."""

        @register_linker("desc-linker", description="A test linker")
        def link_desc(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        linker = get_linker("desc-linker")
        assert linker is not None
        assert linker.description == "A test linker"

    def test_returns_original_function(self):
        """Decorator returns the original function."""

        @register_linker("func-linker")
        def link_func(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult(symbols=["test"])  # type: ignore

        ctx = LinkerContext(repo_root=Path("/test"))
        result = link_func(ctx)
        assert result.symbols == ["test"]


class TestGetLinker:
    """Tests for get_linker function."""

    def test_returns_registered(self):
        """Returns registered linker."""

        @register_linker("found-linker")
        def link_found(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        linker = get_linker("found-linker")
        assert linker is not None
        assert linker.name == "found-linker"

    def test_returns_none_for_unknown(self):
        """Returns None for unknown linker."""
        linker = get_linker("unknown-linker")
        assert linker is None


class TestGetAllLinkers:
    """Tests for get_all_linkers function."""

    def test_empty_registry(self):
        """Empty registry yields nothing."""
        linkers = list(get_all_linkers())
        assert linkers == []

    def test_returns_all_linkers(self):
        """Returns all registered linkers."""

        @register_linker("linker-a")
        def link_a(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        @register_linker("linker-b")
        def link_b(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        linkers = list(get_all_linkers())
        names = [l.name for l in linkers]
        assert "linker-a" in names
        assert "linker-b" in names

    def test_sorted_by_priority(self):
        """Linkers are sorted by priority."""

        @register_linker("late", priority=90)
        def link_late(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        @register_linker("early", priority=10)
        def link_early(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        @register_linker("middle", priority=50)
        def link_middle(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        linkers = list(get_all_linkers())
        names = [l.name for l in linkers]
        assert names == ["early", "middle", "late"]


class TestRunLinker:
    """Tests for run_linker function."""

    def test_runs_linker(self):
        """Runs the named linker."""
        call_count = [0]

        @register_linker("run-test")
        def link_run(ctx: LinkerContext) -> LinkerResult:
            call_count[0] += 1
            return LinkerResult()

        ctx = LinkerContext(repo_root=Path("/test"))
        run_linker("run-test", ctx)
        assert call_count[0] == 1

    def test_passes_context(self):
        """Context is passed to linker."""
        received_ctx = [None]

        @register_linker("ctx-test")
        def link_ctx(ctx: LinkerContext) -> LinkerResult:
            received_ctx[0] = ctx
            return LinkerResult()

        ctx = LinkerContext(repo_root=Path("/my/path"))
        run_linker("ctx-test", ctx)
        assert received_ctx[0] is not None
        assert received_ctx[0].repo_root == Path("/my/path")

    def test_returns_result(self):
        """Returns linker result."""

        @register_linker("result-test")
        def link_result(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult(symbols=["sym"])  # type: ignore

        ctx = LinkerContext(repo_root=Path("/test"))
        result = run_linker("result-test", ctx)
        assert result.symbols == ["sym"]

    def test_raises_for_unknown(self):
        """Raises KeyError for unknown linker."""
        ctx = LinkerContext(repo_root=Path("/test"))
        with pytest.raises(KeyError, match="Unknown linker"):
            run_linker("unknown", ctx)


class TestRunAllLinkers:
    """Tests for run_all_linkers function."""

    def test_runs_all_linkers(self):
        """Runs all registered linkers."""
        calls = []

        @register_linker("all-a")
        def link_all_a(ctx: LinkerContext) -> LinkerResult:
            calls.append("a")
            return LinkerResult()

        @register_linker("all-b")
        def link_all_b(ctx: LinkerContext) -> LinkerResult:
            calls.append("b")
            return LinkerResult()

        ctx = LinkerContext(repo_root=Path("/test"))
        run_all_linkers(ctx)
        assert "a" in calls
        assert "b" in calls

    def test_returns_name_result_pairs(self):
        """Returns list of (name, result) tuples."""

        @register_linker("pair-test")
        def link_pair(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult(symbols=["s"])  # type: ignore

        ctx = LinkerContext(repo_root=Path("/test"))
        results = run_all_linkers(ctx)
        assert len(results) == 1
        name, result = results[0]
        assert name == "pair-test"
        assert result.symbols == ["s"]

    def test_runs_in_priority_order(self):
        """Linkers run in priority order."""
        order = []

        @register_linker("order-late", priority=90)
        def link_late(ctx: LinkerContext) -> LinkerResult:
            order.append("late")
            return LinkerResult()

        @register_linker("order-early", priority=10)
        def link_early(ctx: LinkerContext) -> LinkerResult:
            order.append("early")
            return LinkerResult()

        ctx = LinkerContext(repo_root=Path("/test"))
        run_all_linkers(ctx)
        assert order == ["early", "late"]


class TestClearRegistry:
    """Tests for clear_registry function."""

    def test_clears_all_linkers(self):
        """Clears all registered linkers."""

        @register_linker("clear-test")
        def link_clear(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        assert get_linker("clear-test") is not None
        clear_registry()
        assert get_linker("clear-test") is None


class TestListRegistered:
    """Tests for list_registered function."""

    def test_empty_registry(self):
        """Empty registry returns empty list."""
        assert list_registered() == []

    def test_returns_names(self):
        """Returns list of registered names."""

        @register_linker("list-a")
        def link_a(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        @register_linker("list-b")
        def link_b(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        names = list_registered()
        assert "list-a" in names
        assert "list-b" in names


class TestLinkerRequirements:
    """Tests for linker requirements/contracts system."""

    def test_register_with_requirements(self):
        """Linker can be registered with requirements."""

        def count_java_native(ctx: LinkerContext) -> int:
            return sum(
                1 for s in ctx.symbols
                if s.language == "java" and "native" in s.modifiers  # type: ignore
            )

        req = LinkerRequirement(
            name="java_native",
            description="Java native methods",
            check=count_java_native,
        )

        @register_linker("req-linker", requirements=[req])
        def link_req(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        linker = get_linker("req-linker")
        assert linker is not None
        assert len(linker.requirements) == 1
        assert linker.requirements[0].name == "java_native"

    def test_check_requirements_all_met(self):
        """check_linker_requirements reports all_met=True when requirements are met."""
        from hypergumbo.ir import Symbol, Span

        def count_items(ctx: LinkerContext) -> int:
            return len(ctx.symbols)

        req = LinkerRequirement(
            name="symbols",
            description="Any symbols",
            check=count_items,
        )

        @register_linker("check-met", description="Test linker", requirements=[req])
        def link_check(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        # Create context with one symbol
        sym = Symbol(
            id="test:a.py:1-1:foo:function",
            name="foo",
            kind="function",
            language="test",
            path="a.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=10),
            origin="test",
            origin_run_id="test",
        )
        ctx = LinkerContext(repo_root=Path("/test"), symbols=[sym])

        diagnostics = check_linker_requirements(ctx)

        assert len(diagnostics) == 1
        diag = diagnostics[0]
        assert diag.linker_name == "check-met"
        assert diag.linker_description == "Test linker"
        assert diag.all_met is True
        assert len(diag.requirements) == 1
        assert diag.requirements[0].met is True
        assert diag.requirements[0].count == 1

    def test_check_requirements_unmet(self):
        """check_linker_requirements reports all_met=False when requirements are unmet."""

        def count_nothing(ctx: LinkerContext) -> int:
            return 0

        req = LinkerRequirement(
            name="nothing",
            description="Nothing found",
            check=count_nothing,
        )

        @register_linker("check-unmet", requirements=[req])
        def link_unmet(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        ctx = LinkerContext(repo_root=Path("/test"))

        diagnostics = check_linker_requirements(ctx)

        assert len(diagnostics) == 1
        diag = diagnostics[0]
        assert diag.all_met is False
        assert diag.requirements[0].met is False
        assert diag.requirements[0].count == 0

    def test_check_requirements_multiple(self):
        """check_linker_requirements handles multiple requirements correctly."""

        def count_symbols(ctx: LinkerContext) -> int:
            return len(ctx.symbols)

        def count_edges(ctx: LinkerContext) -> int:
            return len(ctx.edges)

        reqs = [
            LinkerRequirement(name="symbols", description="Symbols", check=count_symbols),
            LinkerRequirement(name="edges", description="Edges", check=count_edges),
        ]

        @register_linker("multi-req", requirements=reqs)
        def link_multi(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        # Context with symbols but no edges
        ctx = LinkerContext(repo_root=Path("/test"), symbols=["s"], edges=[])  # type: ignore

        diagnostics = check_linker_requirements(ctx)

        assert len(diagnostics) == 1
        diag = diagnostics[0]
        # Has symbols (met) but no edges (unmet)
        assert diag.all_met is False
        assert diag.requirements[0].met is True  # symbols
        assert diag.requirements[1].met is False  # edges

    def test_check_requirements_skips_linkers_without_requirements(self):
        """Linkers without requirements are omitted from diagnostics."""

        @register_linker("no-req")
        def link_no_req(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        ctx = LinkerContext(repo_root=Path("/test"))

        diagnostics = check_linker_requirements(ctx)

        # Linker without requirements should not appear in diagnostics
        assert len(diagnostics) == 0


class TestFindEnclosingSymbol:
    """Tests for find_enclosing_symbol method."""

    def test_finds_enclosing_function(self):
        """Finds function that contains a given line."""
        from hypergumbo.ir import Symbol, Span

        func = Symbol(
            id="python:test.py:10-20:my_func:function",
            name="my_func",
            kind="function",
            language="python",
            path="test.py",
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test",
        )

        ctx = LinkerContext(repo_root=Path("/test"), symbols=[func])
        result = ctx.find_enclosing_symbol("test.py", 15)

        assert result is not None
        assert result.name == "my_func"

    def test_returns_none_when_no_match(self):
        """Returns None when no enclosing symbol found."""
        from hypergumbo.ir import Symbol, Span

        func = Symbol(
            id="python:test.py:10-20:my_func:function",
            name="my_func",
            kind="function",
            language="python",
            path="test.py",
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test",
        )

        ctx = LinkerContext(repo_root=Path("/test"), symbols=[func])
        result = ctx.find_enclosing_symbol("test.py", 5)  # Line outside function

        assert result is None

    def test_prefers_method_over_class(self):
        """Prefers method (more specific) over class when both enclose the line."""
        from hypergumbo.ir import Symbol, Span

        cls = Symbol(
            id="python:test.py:5-30:MyClass:class",
            name="MyClass",
            kind="class",
            language="python",
            path="test.py",
            span=Span(start_line=5, end_line=30, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test",
        )
        method = Symbol(
            id="python:test.py:10-20:my_method:method",
            name="my_method",
            kind="method",
            language="python",
            path="test.py",
            span=Span(start_line=10, end_line=20, start_col=4, end_col=0),
            origin="test",
            origin_run_id="test",
        )

        ctx = LinkerContext(repo_root=Path("/test"), symbols=[cls, method])
        result = ctx.find_enclosing_symbol("test.py", 15)

        assert result is not None
        assert result.name == "my_method"  # Method preferred over class

    def test_suffix_matching_for_paths(self):
        """Matches paths by suffix (handles absolute vs relative)."""
        from hypergumbo.ir import Symbol, Span

        func = Symbol(
            id="python:/home/user/project/src/test.py:10-20:my_func:function",
            name="my_func",
            kind="function",
            language="python",
            path="/home/user/project/src/test.py",
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test",
        )

        ctx = LinkerContext(repo_root=Path("/test"), symbols=[func])

        # Should match by suffix
        result = ctx.find_enclosing_symbol("src/test.py", 15)
        assert result is not None
        assert result.name == "my_func"

    def test_filters_by_kinds(self):
        """Only considers symbols of specified kinds."""
        from hypergumbo.ir import Symbol, Span

        # A variable (not in default kinds)
        var = Symbol(
            id="python:test.py:15-15:x:variable",
            name="x",
            kind="variable",
            language="python",
            path="test.py",
            span=Span(start_line=15, end_line=15, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test",
        )
        func = Symbol(
            id="python:test.py:10-20:my_func:function",
            name="my_func",
            kind="function",
            language="python",
            path="test.py",
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            origin="test",
            origin_run_id="test",
        )

        ctx = LinkerContext(repo_root=Path("/test"), symbols=[var, func])

        # Default kinds should skip variable, return function
        result = ctx.find_enclosing_symbol("test.py", 15)
        assert result is not None
        assert result.name == "my_func"


class TestEnclosureLinker:
    """Tests for enclosure post-processing in run_all_linkers."""

    def test_creates_uses_edges_for_synthetic_nodes(self):
        """Creates 'uses' edges from enclosing functions to synthetic nodes."""
        from hypergumbo.ir import Symbol, Span

        # A function in the analyzer output
        func = Symbol(
            id="python:test.py:10-20:my_func:function",
            name="my_func",
            kind="function",
            language="python",
            path="test.py",
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            origin="python-ast-v1",
            origin_run_id="test",
        )

        # A synthetic gRPC stub created by a linker
        stub = Symbol(
            id="grpc:test.py:15:EmailService:grpc_stub",
            name="EmailService",
            kind="grpc_stub",
            language="python",
            path="test.py",
            span=Span(start_line=15, end_line=15, start_col=0, end_col=0),
            origin="grpc-linker-v1",
            origin_run_id="test",
        )

        @register_linker("synthetic-test", priority=50)
        def link_synthetic(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult(symbols=[stub])

        ctx = LinkerContext(repo_root=Path("/test"), symbols=[func])
        results = run_all_linkers(ctx)

        # Should have the linker result AND the enclosure linker result
        assert len(results) == 2
        assert results[0][0] == "synthetic-test"
        assert results[1][0] == "enclosure"

        # Check enclosure edges
        enclosure_result = results[1][1]
        assert len(enclosure_result.edges) == 1
        edge = enclosure_result.edges[0]
        assert edge.src == func.id
        assert edge.dst == stub.id
        assert edge.edge_type == "uses"
        assert edge.evidence_type == "enclosing_scope"

    def test_skips_non_synthetic_kinds(self):
        """Doesn't create edges for non-synthetic node kinds."""
        from hypergumbo.ir import Symbol, Span

        func = Symbol(
            id="python:test.py:10-20:my_func:function",
            name="my_func",
            kind="function",
            language="python",
            path="test.py",
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            origin="python-ast-v1",
            origin_run_id="test",
        )

        # A regular symbol (not synthetic)
        other = Symbol(
            id="python:test.py:15:x:variable",
            name="x",
            kind="variable",  # Not in SYNTHETIC_KINDS
            language="python",
            path="test.py",
            span=Span(start_line=15, end_line=15, start_col=0, end_col=0),
            origin="other-linker-v1",
            origin_run_id="test",
        )

        @register_linker("non-synthetic-test", priority=50)
        def link_non_synthetic(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult(symbols=[other])

        ctx = LinkerContext(repo_root=Path("/test"), symbols=[func])
        results = run_all_linkers(ctx)

        # Only the linker result, no enclosure result (no synthetic nodes)
        assert len(results) == 1
        assert results[0][0] == "non-synthetic-test"

    def test_handles_no_enclosing_function(self):
        """Gracefully handles synthetic nodes with no enclosing function."""
        from hypergumbo.ir import Symbol, Span

        # Synthetic node at module level (no enclosing function)
        stub = Symbol(
            id="grpc:test.py:5:Service:grpc_stub",
            name="Service",
            kind="grpc_stub",
            language="python",
            path="test.py",
            span=Span(start_line=5, end_line=5, start_col=0, end_col=0),
            origin="grpc-linker-v1",
            origin_run_id="test",
        )

        @register_linker("no-enclosing-test", priority=50)
        def link_no_enclosing(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult(symbols=[stub])

        # Empty symbols list - no functions to enclose
        ctx = LinkerContext(repo_root=Path("/test"), symbols=[])
        results = run_all_linkers(ctx)

        # Only the linker result, no enclosure edges created
        assert len(results) == 1
        assert results[0][0] == "no-enclosing-test"
