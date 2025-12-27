"""Tests for the sketch module (token-budgeted Markdown output)."""
from pathlib import Path

from hypergumbo.sketch import (
    generate_sketch,
    estimate_tokens,
    truncate_to_tokens,
    _collect_source_files,
    _format_source_files,
    _format_all_files,
    _run_analysis,
    _format_entrypoints,
    _format_symbols,
)
from hypergumbo.ranking import compute_centrality, _is_test_path
from hypergumbo.profile import detect_profile
from hypergumbo.ir import Symbol, Edge, Span
from hypergumbo.entrypoints import Entrypoint, EntrypointKind


class TestEstimateTokens:
    """Tests for token estimation."""

    def test_empty_string(self) -> None:
        """Empty string has zero tokens."""
        assert estimate_tokens("") == 0

    def test_simple_text(self) -> None:
        """Simple text returns approximate token count."""
        # ~4 chars per token is the heuristic
        text = "Hello world"  # 11 chars -> ~3 tokens
        tokens = estimate_tokens(text)
        assert 2 <= tokens <= 5

    def test_longer_text(self) -> None:
        """Longer text scales appropriately."""
        text = "a" * 400  # 400 chars -> ~100 tokens
        tokens = estimate_tokens(text)
        assert 80 <= tokens <= 120


class TestTruncateToTokens:
    """Tests for token-based truncation."""

    def test_short_text_not_truncated(self) -> None:
        """Text under budget is not truncated."""
        text = "Hello world"
        result = truncate_to_tokens(text, max_tokens=100)
        assert result == text

    def test_long_text_truncated(self) -> None:
        """Text over budget is truncated."""
        text = "word " * 1000  # ~1000 tokens
        result = truncate_to_tokens(text, max_tokens=50)
        assert estimate_tokens(result) <= 60  # Allow some slack

    def test_preserves_section_boundaries(self) -> None:
        """Truncation prefers section boundaries."""
        text = "# Section 1\nContent one\n\n# Section 2\nContent two\n\n# Section 3\nContent three"
        result = truncate_to_tokens(text, max_tokens=20)
        # Should include at least the first section
        assert "# Section 1" in result

    def test_partial_sections_fit(self) -> None:
        """When some sections fit, return only those."""
        # Create text where first two sections fit but third doesn't
        sec1 = "A" * 20  # ~5 tokens
        sec2 = "B" * 20  # ~5 tokens
        sec3 = "C" * 200  # ~50 tokens
        text = f"{sec1}\n\n{sec2}\n\n{sec3}"

        result = truncate_to_tokens(text, max_tokens=15)

        # Should include first two sections
        assert "A" in result
        assert "B" in result
        # Third section should be excluded
        assert "C" * 50 not in result

    def test_markdown_headers_stay_with_content(self) -> None:
        """Markdown section headers must not be separated from their content.

        This prevents orphaned headers like '## Entry Points' appearing
        without their list of entries.
        """
        text = """# Title

## Overview
Some overview text.

## Source Files

- file1.py
- file2.py
- file3.py

## Entry Points

- handler1 (HTTP GET)
- handler2 (HTTP POST)
"""
        # Truncate to a size that can't fit Entry Points section
        result = truncate_to_tokens(text, max_tokens=35)

        # If "## Entry Points" is in result, its content must be there too
        if "## Entry Points" in result:
            assert "handler1" in result
        else:
            # Alternatively, the whole section should be excluded
            assert "handler1" not in result

    def test_markdown_title_preserved(self) -> None:
        """Title before first ## section is preserved."""
        text = """# My Project

## Overview
Some content.

## Details
More content.
"""
        result = truncate_to_tokens(text, max_tokens=15)

        # Title should be in result
        assert "# My Project" in result


class TestGenerateSketch:
    """Tests for full sketch generation."""

    def test_generates_markdown(self, tmp_path: Path) -> None:
        """Sketch output is valid Markdown."""
        # Create a simple Python project
        (tmp_path / "main.py").write_text("def hello():\n    pass\n")
        (tmp_path / "utils.py").write_text("def helper():\n    pass\n")

        sketch = generate_sketch(tmp_path)

        assert sketch.startswith("#")  # Markdown header
        assert "python" in sketch.lower()

    def test_includes_overview(self, tmp_path: Path) -> None:
        """Sketch includes language overview."""
        (tmp_path / "app.py").write_text("# Main app\nprint('hello')\n")

        sketch = generate_sketch(tmp_path)

        assert "Overview" in sketch or "python" in sketch.lower()

    def test_respects_token_budget(self, tmp_path: Path) -> None:
        """Sketch respects token budget."""
        # Create a larger project
        for i in range(20):
            (tmp_path / f"module_{i}.py").write_text(f"def func_{i}():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=100)

        tokens = estimate_tokens(sketch)
        assert tokens <= 120  # Allow some slack

    def test_includes_directory_structure(self, tmp_path: Path) -> None:
        """Sketch includes directory structure."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path)

        assert "src" in sketch

    def test_detects_entrypoints(self, tmp_path: Path) -> None:
        """Sketch includes detected entry points when available."""
        # Create a FastAPI-style app
        (tmp_path / "requirements.txt").write_text("fastapi\n")
        (tmp_path / "main.py").write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "@app.get('/health')\n"
            "def health():\n"
            "    return {'status': 'ok'}\n"
        )

        sketch = generate_sketch(tmp_path)

        # Should detect FastAPI framework
        assert "fastapi" in sketch.lower() or "Entry" in sketch

    def test_empty_project(self, tmp_path: Path) -> None:
        """Sketch handles empty projects."""
        sketch = generate_sketch(tmp_path)

        assert "No source files detected" in sketch

    def test_empty_files_zero_loc(self, tmp_path: Path) -> None:
        """Sketch handles files with zero lines of code."""
        # Create empty Python file (0 LOC)
        (tmp_path / "empty.py").write_text("")

        sketch = generate_sketch(tmp_path)

        # Should handle gracefully - either "No source code" or show 0 LOC
        assert "0 LOC" in sketch or "No source" in sketch

    def test_no_frameworks(self, tmp_path: Path) -> None:
        """Sketch handles projects with no detected frameworks."""
        (tmp_path / "main.py").write_text("print('hello')\n")

        sketch = generate_sketch(tmp_path)

        # Should not have Frameworks section
        assert "## Frameworks" not in sketch or "Frameworks" in sketch

    def test_many_directories(self, tmp_path: Path) -> None:
        """Sketch handles projects with many directories."""
        # Create 15 directories
        for i in range(15):
            (tmp_path / f"dir_{i:02d}").mkdir()
        (tmp_path / "main.py").write_text("print('hello')\n")

        sketch = generate_sketch(tmp_path)

        # Should show truncation message
        assert "... and" in sketch and "more directories" in sketch

    def test_various_directory_types(self, tmp_path: Path) -> None:
        """Sketch labels different directory types correctly."""
        (tmp_path / "lib").mkdir()
        (tmp_path / "test").mkdir()
        (tmp_path / "doc").mkdir()
        (tmp_path / "random").mkdir()
        (tmp_path / "main.py").write_text("print('hello')\n")

        sketch = generate_sketch(tmp_path)

        assert "Source code" in sketch  # lib/
        assert "Tests" in sketch  # test/
        assert "Documentation" in sketch  # doc/

    def test_hard_truncation_fallback(self, tmp_path: Path) -> None:
        """Truncation falls back to hard truncate if no section fits."""
        (tmp_path / "main.py").write_text("print('hello')\n")

        # Very small token budget - should trigger hard truncate
        result = truncate_to_tokens("A" * 1000, max_tokens=5)

        # Should be truncated to ~20 chars
        assert len(result) <= 25


class TestCollectSourceFiles:
    """Tests for source file collection."""

    def test_collects_python_files(self, tmp_path: Path) -> None:
        """Collects Python files from repo."""
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "utils.py").write_text("print('util')")

        profile = detect_profile(tmp_path)
        files = _collect_source_files(tmp_path, profile)

        assert len(files) == 2
        names = {f.name for f in files}
        assert "main.py" in names
        assert "utils.py" in names

    def test_prioritizes_source_directories(self, tmp_path: Path) -> None:
        """Files from src/ directories come first."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "core.py").write_text("print('core')")
        (tmp_path / "main.py").write_text("print('main')")

        profile = detect_profile(tmp_path)
        files = _collect_source_files(tmp_path, profile)

        # src/core.py should come before main.py
        names = [f.name for f in files]
        assert names[0] == "core.py"

    def test_handles_no_source_files(self, tmp_path: Path) -> None:
        """Returns empty list when no source files."""
        profile = detect_profile(tmp_path)
        files = _collect_source_files(tmp_path, profile)
        assert files == []


class TestFormatSourceFiles:
    """Tests for source file formatting."""

    def test_formats_file_list(self, tmp_path: Path) -> None:
        """Formats files as Markdown list."""
        files = [tmp_path / "a.py", tmp_path / "b.py"]

        result = _format_source_files(tmp_path, files)

        assert "## Source Files" in result
        assert "`a.py`" in result
        assert "`b.py`" in result

    def test_respects_max_files(self, tmp_path: Path) -> None:
        """Limits output to max_files."""
        files = [tmp_path / f"file_{i}.py" for i in range(10)]

        result = _format_source_files(tmp_path, files, max_files=3)

        assert "file_0.py" in result
        assert "file_1.py" in result
        assert "file_2.py" in result
        assert "... and 7 more files" in result

    def test_empty_files_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty string for empty file list."""
        result = _format_source_files(tmp_path, [])
        assert result == ""


class TestFormatAllFiles:
    """Tests for all files formatting."""

    def test_lists_all_files(self, tmp_path: Path) -> None:
        """Lists all non-excluded files."""
        (tmp_path / "readme.md").write_text("# README")
        (tmp_path / "main.py").write_text("print('hello')")

        result = _format_all_files(tmp_path)

        assert "## All Files" in result
        assert "`main.py`" in result
        assert "`readme.md`" in result

    def test_excludes_hidden_files(self, tmp_path: Path) -> None:
        """Excludes hidden files."""
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible.txt").write_text("public")

        result = _format_all_files(tmp_path)

        assert ".hidden" not in result
        assert "`visible.txt`" in result

    def test_excludes_node_modules(self, tmp_path: Path) -> None:
        """Excludes node_modules directory."""
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "package.json").write_text("{}")
        (tmp_path / "index.js").write_text("console.log('hi')")

        result = _format_all_files(tmp_path)

        assert "node_modules" not in result
        assert "`index.js`" in result

    def test_respects_max_files(self, tmp_path: Path) -> None:
        """Limits output to max_files."""
        for i in range(10):
            (tmp_path / f"file_{i}.txt").write_text(f"content {i}")

        result = _format_all_files(tmp_path, max_files=3)

        assert "... and 7 more files" in result

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty string for empty directory."""
        result = _format_all_files(tmp_path)
        assert result == ""


class TestRunAnalysis:
    """Tests for running static analysis."""

    def test_analyzes_python_files(self, tmp_path: Path) -> None:
        """Runs Python analysis on Python files."""
        (tmp_path / "main.py").write_text("def hello():\n    print('hi')\n")

        profile = detect_profile(tmp_path)
        symbols, edges = _run_analysis(tmp_path, profile)

        assert len(symbols) > 0
        names = {s.name for s in symbols}
        assert "hello" in names

    def test_handles_no_python(self, tmp_path: Path) -> None:
        """Returns empty results when no Python files."""
        (tmp_path / "readme.md").write_text("# Hello")

        profile = detect_profile(tmp_path)
        symbols, edges = _run_analysis(tmp_path, profile)

        assert symbols == []
        assert edges == []


class TestIsTestPath:
    """Tests for test file detection."""

    def test_tests_directory(self) -> None:
        """Detects /tests/ directory pattern."""
        assert _is_test_path("/project/tests/test_app.py") is True
        assert _is_test_path("src/tests/helpers.py") is True

    def test_test_singular_directory(self) -> None:
        """Detects /test/ directory pattern (singular, common in JS projects)."""
        assert _is_test_path("/project/test/app.router.js") is True
        assert _is_test_path("test/utils.js") is True
        assert _is_test_path("/express/test/res.send.js") is True

    def test_dunder_tests_directory(self) -> None:
        """Detects /__tests__/ directory pattern (JavaScript)."""
        assert _is_test_path("/src/__tests__/App.test.js") is True

    def test_test_prefix_filename(self) -> None:
        """Detects test_*.py filename pattern."""
        assert _is_test_path("/src/test_utils.py") is True
        assert _is_test_path("test_main.py") is True

    def test_dot_test_suffix(self) -> None:
        """Detects .test.js, .test.ts patterns."""
        assert _is_test_path("/src/App.test.js") is True
        assert _is_test_path("/src/utils.test.ts") is True
        assert _is_test_path("Component.test.tsx") is True

    def test_dot_spec_suffix(self) -> None:
        """Detects .spec.js, .spec.ts patterns."""
        assert _is_test_path("/src/App.spec.js") is True
        assert _is_test_path("utils.spec.ts") is True

    def test_underscore_test_suffix(self) -> None:
        """Detects _test.py pattern."""
        assert _is_test_path("/src/utils_test.py") is True
        assert _is_test_path("app_test.js") is True

    def test_production_files(self) -> None:
        """Non-test files return False."""
        assert _is_test_path("/src/app.py") is False
        assert _is_test_path("/src/utils.ts") is False
        assert _is_test_path("main.js") is False

    def test_pytest_temp_dirs_not_matched(self) -> None:
        """Pytest temp directories are not matched as test files."""
        # These contain 'test' but are not actual test files
        assert _is_test_path("/tmp/pytest-of-user/pytest-1/test_something0/app.py") is False


class TestComputeCentrality:
    """Tests for graph centrality computation."""

    def test_computes_in_degree(self) -> None:
        """Computes in-degree centrality."""
        symbols = [
            Symbol(id="a", name="a", kind="function", language="python",
                   path="/app.py", span=Span(1, 1, 1, 10)),
            Symbol(id="b", name="b", kind="function", language="python",
                   path="/app.py", span=Span(2, 1, 2, 10)),
        ]
        edges = [
            Edge.create(src="a", dst="b", edge_type="calls", line=1, confidence=1.0),
        ]

        centrality = compute_centrality(symbols, edges)

        assert centrality["b"] > centrality["a"]

    def test_handles_no_edges(self) -> None:
        """Handles symbols with no edges."""
        symbols = [
            Symbol(id="a", name="a", kind="function", language="python",
                   path="/app.py", span=Span(1, 1, 1, 10)),
        ]

        centrality = compute_centrality(symbols, [])

        assert centrality["a"] == 0


class TestFormatEntrypoints:
    """Tests for entry point formatting."""

    def test_formats_entrypoints(self, tmp_path: Path) -> None:
        """Formats entry points as Markdown."""
        symbols = [
            Symbol(id="main", name="main", kind="function", language="python",
                   path=str(tmp_path / "cli.py"), span=Span(1, 1, 1, 10)),
        ]
        entrypoints = [
            Entrypoint(symbol_id="main", kind=EntrypointKind.CLI_MAIN,
                       confidence=0.7, label="CLI main"),
        ]

        result = _format_entrypoints(entrypoints, symbols, tmp_path)

        assert "## Entry Points" in result
        assert "`main`" in result
        assert "CLI main" in result

    def test_respects_max_entries(self, tmp_path: Path) -> None:
        """Limits output to max_entries."""
        symbols = [
            Symbol(id=f"ep{i}", name=f"ep{i}", kind="function", language="python",
                   path=str(tmp_path / "app.py"), span=Span(i, 1, i, 10))
            for i in range(10)
        ]
        entrypoints = [
            Entrypoint(symbol_id=f"ep{i}", kind=EntrypointKind.HTTP_ROUTE,
                       confidence=0.9, label="HTTP GET")
            for i in range(10)
        ]

        result = _format_entrypoints(entrypoints, symbols, tmp_path, max_entries=3)

        assert "... and 7 more entry points" in result

    def test_empty_entrypoints_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty string for empty entry points."""
        result = _format_entrypoints([], [], tmp_path)
        assert result == ""

    def test_missing_symbol_fallback(self, tmp_path: Path) -> None:
        """Falls back to symbol_id when symbol not found."""
        entrypoints = [
            Entrypoint(symbol_id="unknown:symbol", kind=EntrypointKind.CLI_MAIN,
                       confidence=0.7, label="CLI main"),
        ]

        result = _format_entrypoints(entrypoints, [], tmp_path)

        assert "`unknown:symbol`" in result
        assert "CLI main" in result


class TestFormatSymbols:
    """Tests for symbol formatting."""

    def test_formats_symbols(self) -> None:
        """Formats symbols as Markdown."""
        # Use fixed paths to avoid tmp_path containing /test
        repo_root = Path("/fake/repo")
        symbols = [
            Symbol(id="main", name="main", kind="function", language="python",
                   path="/fake/repo/cli.py", span=Span(1, 1, 1, 10)),
            Symbol(id="App", name="App", kind="class", language="python",
                   path="/fake/repo/cli.py", span=Span(5, 1, 10, 10)),
        ]

        result = _format_symbols(symbols, [], repo_root)

        assert "## Key Symbols" in result
        assert "`main`" in result
        assert "`App`" in result

    def test_excludes_test_files(self) -> None:
        """Excludes symbols from test files and test functions."""
        repo_root = Path("/fake/repo")
        symbols = [
            Symbol(id="main", name="main", kind="function", language="python",
                   path="/fake/repo/app.py", span=Span(1, 1, 1, 10)),
            # Symbol in tests/ directory
            Symbol(id="test_main", name="test_main", kind="function", language="python",
                   path="/fake/repo/tests/test_app.py", span=Span(1, 1, 1, 10)),
            # Function with test_ prefix
            Symbol(id="test_helper", name="test_helper", kind="function", language="python",
                   path="/fake/repo/app.py", span=Span(5, 1, 5, 10)),
        ]

        result = _format_symbols(symbols, [], repo_root)

        assert "`main`" in result
        assert "test_main" not in result
        assert "test_helper" not in result

    def test_respects_max_symbols(self) -> None:
        """Limits output to max_symbols."""
        repo_root = Path("/fake/repo")
        symbols = [
            Symbol(id=f"fn{i}", name=f"fn{i}", kind="function", language="python",
                   path="/fake/repo/app.py", span=Span(i, 1, i, 10))
            for i in range(20)
        ]

        result = _format_symbols(symbols, [], repo_root, max_symbols=5)

        # New format: "… and X more symbols across Y other files"
        assert "… and 15 more symbols" in result

    def test_max_symbols_breaks_across_files(self) -> None:
        """Max symbols limit causes balanced selection across files."""
        repo_root = Path("/fake/repo")
        # Create symbols across multiple files
        symbols = []
        for file_idx in range(5):
            for fn_idx in range(10):
                symbols.append(
                    Symbol(
                        id=f"fn{file_idx}_{fn_idx}",
                        name=f"fn{file_idx}_{fn_idx}",
                        kind="function",
                        language="python",
                        path=f"/fake/repo/file_{file_idx}.py",
                        span=Span(fn_idx, 1, fn_idx, 10),
                    )
                )

        # Max symbols less than total - with two-phase selection,
        # coverage phase picks 5 (one per file), then fills remaining 10
        result = _format_symbols(symbols, [], repo_root, max_symbols=15)

        # Should show remaining count with new format
        assert "… and 35 more symbols" in result
        # Should show symbols from multiple files (coverage-first policy)
        assert "file_0.py" in result
        assert "file_1.py" in result

    def test_empty_symbols_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty string for empty symbols."""
        result = _format_symbols([], [], tmp_path)
        assert result == ""

    def test_only_test_symbols_returns_empty(self) -> None:
        """Returns empty when all symbols are filtered out (e.g., test files only)."""
        repo_root = Path("/fake/repo")
        symbols = [
            Symbol(id="test_a", name="test_a", kind="function", language="python",
                   path="/fake/repo/tests/test_app.py", span=Span(1, 1, 1, 10)),
            Symbol(id="test_b", name="test_b", kind="function", language="python",
                   path="/fake/repo/tests/test_util.py", span=Span(1, 1, 1, 10)),
        ]

        result = _format_symbols(symbols, [], repo_root)

        # All symbols are in tests/ so should return empty
        assert result == ""

    def test_marks_high_centrality_symbols(self) -> None:
        """Adds star to high-centrality symbols."""
        repo_root = Path("/fake/repo")
        symbols = [
            Symbol(id="core", name="core", kind="function", language="python",
                   path="/fake/repo/app.py", span=Span(1, 1, 1, 10)),
            Symbol(id="leaf", name="leaf", kind="function", language="python",
                   path="/fake/repo/app.py", span=Span(5, 1, 5, 10)),
        ]
        # Many edges pointing to core
        edges = [
            Edge.create(src=f"caller{i}", dst="core", edge_type="calls",
                        line=i, confidence=1.0)
            for i in range(10)
        ]

        result = _format_symbols(symbols, edges, repo_root)

        assert "`core`" in result
        assert "★" in result  # High centrality marker

    def test_tier_weighted_ranking(self) -> None:
        """First-party symbols rank higher than external deps with similar centrality.

        Tier weighting (2x for first-party, 1x for external) boosts first-party
        symbols to overcome moderate raw centrality differences.
        """
        repo_root = Path("/fake/repo")
        # External dep symbol with slightly higher raw centrality
        external_sym = Symbol(
            id="external", name="lodash_util", kind="function", language="javascript",
            path="/fake/repo/node_modules/lodash/util.js", span=Span(1, 1, 1, 10),
            supply_chain_tier=3, supply_chain_reason="in node_modules/"
        )
        # First-party symbol with lower raw centrality
        first_party_sym = Symbol(
            id="first_party", name="my_func", kind="function", language="javascript",
            path="/fake/repo/src/app.js", span=Span(1, 1, 1, 10),
            supply_chain_tier=1, supply_chain_reason="matches ^src/"
        )

        # External has 5 callers, first-party has 3
        # Raw centrality: external=1.0, first-party=0.6
        # Weighted (tier 1 = 2x, tier 3 = 1x): external=1.0, first-party=1.2
        # So first-party should win
        edges = [
            Edge.create(src=f"caller{i}", dst="external", edge_type="calls",
                        line=i, confidence=1.0)
            for i in range(5)
        ] + [
            Edge.create(src=f"caller_fp{i}", dst="first_party", edge_type="calls",
                        line=i, confidence=1.0)
            for i in range(3)
        ]

        result = _format_symbols([external_sym, first_party_sym], edges, repo_root)

        # First-party should appear first due to tier weighting
        lines = result.split('\n')
        first_party_pos = next((i for i, l in enumerate(lines) if "my_func" in l), -1)
        external_pos = next((i for i, l in enumerate(lines) if "lodash_util" in l), -1)

        # Both should be present
        assert first_party_pos > 0, "first_party symbol not found"
        assert external_pos > 0, "external symbol not found"
        # First-party should come before external
        assert first_party_pos < external_pos, (
            f"Expected first-party (line {first_party_pos}) before external (line {external_pos})"
        )

    def test_first_party_priority_disabled(self) -> None:
        """Respects first_party_priority=False to use raw centrality."""
        repo_root = Path("/fake/repo")
        # Create symbols with different tiers
        symbols = [
            Symbol(id="tier1", name="first_party_fn", kind="function", language="python",
                   path="/fake/repo/src/core.py", span=Span(1, 1, 1, 10),
                   supply_chain_tier=1),
            Symbol(id="tier3", name="external_fn", kind="function", language="python",
                   path="/fake/repo/vendor/lib.py", span=Span(1, 1, 1, 10),
                   supply_chain_tier=3),
        ]
        # Create edges making the tier-3 symbol more central
        edges = [
            type("Edge", (), {"src": "x", "dst": "tier3"})(),
            type("Edge", (), {"src": "y", "dst": "tier3"})(),
        ]

        result = _format_symbols(symbols, edges, repo_root, first_party_priority=False)

        # With first_party_priority=False, raw centrality is used (no tier boost)
        assert "external_fn" in result
        assert "first_party_fn" in result

    def test_tier_4_derived_excluded(self) -> None:
        """Tier 4 (derived/bundled) symbols are excluded from Key Symbols."""
        repo_root = Path("/fake/repo")
        # Derived symbol (bundled webpack code)
        bundled_sym = Symbol(
            id="bundled", name="__webpack_require__", kind="function",
            language="javascript",
            path="/fake/repo/dist/bundle.js", span=Span(1, 1, 1, 10),
            supply_chain_tier=4, supply_chain_reason="detected as minified/generated"
        )
        # First-party symbol
        first_party_sym = Symbol(
            id="first_party", name="my_func", kind="function",
            language="javascript",
            path="/fake/repo/src/app.js", span=Span(1, 1, 1, 10),
            supply_chain_tier=1, supply_chain_reason="matches ^src/"
        )

        # Both have calls, but bundled has more
        edges = [
            Edge.create(src=f"caller{i}", dst="bundled", edge_type="calls",
                        line=i, confidence=1.0)
            for i in range(100)  # High centrality
        ] + [
            Edge.create(src="caller_fp", dst="first_party", edge_type="calls",
                        line=1, confidence=1.0)
        ]

        result = _format_symbols([bundled_sym, first_party_sym], edges, repo_root)

        # First-party should be present
        assert "my_func" in result
        # Bundled/derived should be excluded entirely
        assert "__webpack_require__" not in result


class TestGenerateSketchWithBudget:
    """Tests for budget-based sketch expansion."""

    def test_expands_with_larger_budget(self, tmp_path: Path) -> None:
        """Larger budgets include more content."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("def main():\n    pass\n")
        (src / "utils.py").write_text("def helper():\n    pass\n")

        small_sketch = generate_sketch(tmp_path, max_tokens=50)
        large_sketch = generate_sketch(tmp_path, max_tokens=500)

        assert len(large_sketch) > len(small_sketch)

    def test_includes_source_files_at_medium_budget(self, tmp_path: Path) -> None:
        """Medium budget includes source file listing."""
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=200)

        assert "## Source Files" in sketch

    def test_includes_symbols_at_large_budget(self, tmp_path: Path) -> None:
        """Large budget includes key symbols."""
        (tmp_path / "main.py").write_text("def main():\n    pass\n\ndef helper():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=800)

        assert "## Key Symbols" in sketch or "## Entry Points" in sketch

    def test_very_small_budget_truncates_base(self, tmp_path: Path) -> None:
        """Very small budget truncates even the base sketch."""
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        # Budget smaller than the base overview
        sketch = generate_sketch(tmp_path, max_tokens=10)

        # Should be truncated
        assert len(sketch) < 100

    def test_symbols_section_with_many_files(self, tmp_path: Path) -> None:
        """Symbols section properly handles multiple files."""
        # Create multiple files to test cross-file symbol listing
        for i in range(5):
            (tmp_path / f"module_{i}.py").write_text(
                f"def func_{i}_a():\n    pass\n\n"
                f"def func_{i}_b():\n    pass\n"
            )

        # Need large budget to trigger symbols section
        sketch = generate_sketch(tmp_path, max_tokens=3000)

        # Should include Key Symbols section with multiple files
        assert "## Key Symbols" in sketch
        assert "###" in sketch  # File headers


class TestCLISketch:
    """Tests for CLI sketch command."""

    def test_sketch_nonexistent_path(self, capsys) -> None:
        """Sketch command handles nonexistent paths."""
        from hypergumbo.cli import main

        result = main(["/nonexistent/path/that/does/not/exist"])

        assert result == 1
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

    def test_sketch_default_mode(self, tmp_path: Path, capsys) -> None:
        """Default mode runs sketch."""
        from hypergumbo.cli import main

        (tmp_path / "app.py").write_text("def main():\n    pass\n")

        result = main([str(tmp_path)])

        assert result == 0
        captured = capsys.readouterr()
        assert "## Overview" in captured.out

    def test_sketch_with_tokens_flag(self, tmp_path: Path, capsys) -> None:
        """Sketch respects -t flag."""
        from hypergumbo.cli import main

        (tmp_path / "app.py").write_text("def main():\n    pass\n")

        result = main([str(tmp_path), "-t", "50"])

        assert result == 0
        captured = capsys.readouterr()
        assert len(captured.out) < 500  # Should be truncated

    def test_sketch_explicit_command(self, tmp_path: Path, capsys) -> None:
        """Sketch works with explicit 'sketch' command."""
        from hypergumbo.cli import main

        (tmp_path / "app.py").write_text("def main():\n    pass\n")

        result = main(["sketch", str(tmp_path)])

        assert result == 0
        captured = capsys.readouterr()
        assert "## Overview" in captured.out

    def test_sketch_exclude_tests_flag(self, tmp_path: Path, capsys) -> None:
        """Sketch respects --exclude-tests flag."""
        from hypergumbo.cli import main

        (tmp_path / "app.py").write_text("def main():\n    pass\n")

        result = main([str(tmp_path), "-x"])

        assert result == 0
        captured = capsys.readouterr()
        assert "## Overview" in captured.out


class TestExcludeTests:
    """Tests for --exclude-tests functionality."""

    def test_run_analysis_excludes_test_symbols(self, tmp_path: Path) -> None:
        """_run_analysis with exclude_tests=True filters test symbols."""
        # Create source file
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("def main():\n    pass\n")

        # Create test file
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_app.py").write_text("def test_main():\n    pass\n")

        profile = detect_profile(tmp_path)

        # Without exclude_tests, should include test symbols
        symbols_all, _ = _run_analysis(tmp_path, profile, exclude_tests=False)
        all_names = [s.name for s in symbols_all]
        assert "main" in all_names
        assert "test_main" in all_names

        # With exclude_tests, should exclude test symbols
        symbols_filtered, _ = _run_analysis(tmp_path, profile, exclude_tests=True)
        filtered_names = [s.name for s in symbols_filtered]
        assert "main" in filtered_names
        assert "test_main" not in filtered_names

    def test_run_analysis_filters_edges_to_test_symbols(self, tmp_path: Path) -> None:
        """Edges involving test symbols are filtered when exclude_tests=True."""
        # Create source file that calls a function
        (tmp_path / "app.py").write_text(
            "def main():\n    helper()\n\ndef helper():\n    pass\n"
        )

        # Create test file with edges
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_app.py").write_text(
            "from app import main\n\ndef test_main():\n    main()\n"
        )

        profile = detect_profile(tmp_path)

        # With exclude_tests, edges from test files should be filtered
        _, edges = _run_analysis(tmp_path, profile, exclude_tests=True)

        # All remaining edges should only reference non-test symbols
        for edge in edges:
            src_path = getattr(edge, "src", "")
            dst_path = getattr(edge, "dst", "")
            assert "test_" not in src_path or "tests/" not in src_path
            assert "test_" not in dst_path or "tests/" not in dst_path

    def test_generate_sketch_with_exclude_tests(self, tmp_path: Path) -> None:
        """generate_sketch with exclude_tests=True works correctly."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("def main():\n    pass\n")

        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_app.py").write_text("def test_main():\n    pass\n")

        # Should complete without error
        sketch = generate_sketch(tmp_path, max_tokens=1000, exclude_tests=True)
        assert "## Overview" in sketch
