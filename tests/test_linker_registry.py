"""Tests for the linkers.registry module."""

from pathlib import Path

import pytest

from hypergumbo.ir import AnalysisRun
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
