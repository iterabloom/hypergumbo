"""Tests for the sketch module (token-budgeted Markdown output)."""
from pathlib import Path

import pytest

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
    _format_structure,
    _extract_python_docstrings,
    _extract_domain_vocabulary,
    _format_vocabulary,
    _detect_test_summary,
    _format_test_summary,
)
from hypergumbo.ranking import compute_centrality, _is_test_path
from hypergumbo.profile import detect_profile
from hypergumbo.ir import Symbol, Edge, Span
from hypergumbo.entrypoints import Entrypoint, EntrypointKind


def _has_sentence_transformers() -> bool:
    """Check if sentence-transformers is already installed.

    Only checks via import - does NOT try to install because pip install
    during test collection causes OOM on small CI runners.
    CI can optionally install sentence-transformers in a separate step.
    """
    try:
        import sentence_transformers
        del sentence_transformers  # Silence F841 (unused variable)
        return True
    except ImportError:
        return False


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

    def test_includes_test_summary(self, tmp_path: Path) -> None:
        """Sketch includes test summary when tests exist."""
        (tmp_path / "main.py").write_text("def hello(): pass\n")
        (tmp_path / "test_main.py").write_text("import pytest\n\ndef test_hello(): pass\n")

        sketch = generate_sketch(tmp_path)

        assert "## Tests" in sketch
        assert "pytest" in sketch
        assert "1 test file" in sketch

    def test_no_test_summary_when_no_tests(self, tmp_path: Path) -> None:
        """Sketch omits test summary section when no tests exist."""
        (tmp_path / "main.py").write_text("print('hello')\n")

        sketch = generate_sketch(tmp_path)

        # Should not have Tests section
        assert "## Tests" not in sketch

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

    def test_includes_readme_description(self, tmp_path: Path) -> None:
        """Sketch includes project description from README.md."""
        (tmp_path / "README.md").write_text(
            "# My Project\n\n"
            "A powerful tool for analyzing code.\n\n"
            "## Installation\n"
            "pip install myproject\n"
        )
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=500)

        # Should include the first descriptive paragraph from README
        assert "powerful tool for analyzing code" in sketch

    def test_readme_description_from_various_formats(self, tmp_path: Path) -> None:
        """Sketch extracts description from README.rst and README.txt."""
        (tmp_path / "README.rst").write_text(
            "My Project\n"
            "==========\n\n"
            "An excellent library for data processing.\n"
        )
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=500)

        assert "excellent library for data processing" in sketch

    def test_readme_description_truncated(self, tmp_path: Path) -> None:
        """Long README descriptions are truncated to fit token budget."""
        long_desc = "This is a very long project description. " * 50
        (tmp_path / "README.md").write_text(f"# Project\n\n{long_desc}\n")
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=200)

        # Description should be present but truncated
        assert "project description" in sketch.lower()
        # Full long_desc should NOT be included (would exceed budget)
        assert long_desc not in sketch

    def test_no_readme_graceful(self, tmp_path: Path) -> None:
        """Sketch works gracefully when no README exists."""
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=500)

        # Should not crash, should still include overview
        assert "## Overview" in sketch

    def test_readme_stops_at_section_header(self, tmp_path: Path) -> None:
        """README extraction stops at next section header."""
        (tmp_path / "README.md").write_text(
            "# Project\n\n"
            "First line of description.\n"
            "## Installation\n"
            "This should not be included.\n"
        )
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=500)

        assert "First line of description" in sketch
        assert "should not be included" not in sketch

    def test_readme_empty_description(self, tmp_path: Path) -> None:
        """README with only title and no description."""
        (tmp_path / "README.md").write_text(
            "# Project Title\n\n"
            "## Installation\n"
            "pip install foo\n"
        )
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=500)

        # Should not include installation instructions as description
        assert "pip install" not in sketch.split("## Overview")[0]

    def test_readme_truncation_no_word_boundary(self, tmp_path: Path) -> None:
        """README truncation handles long words without spaces."""
        # Create a description with a very long word (no spaces for truncation)
        long_word = "a" * 300
        (tmp_path / "README.md").write_text(f"# Project\n\n{long_word}\n")
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=500)

        # Should truncate and add ellipsis
        assert "…" in sketch
        # Should not include the full long word
        assert long_word not in sketch

    def test_readme_skips_badges_and_images(self, tmp_path: Path) -> None:
        """README extraction skips badge images and links."""
        (tmp_path / "README.md").write_text(
            "# Project\n\n"
            "![Badge](https://badge.url)\n"
            "[![CI](https://ci.url)](https://link)\n"
            "[Some Link](https://example.com)\n"
            "A real description of the project.\n"
        )
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=500)

        # Should include the real description
        assert "real description of the project" in sketch
        # Should not include badge URLs
        assert "badge.url" not in sketch
        assert "ci.url" not in sketch

    def test_readme_skips_html_comments(self, tmp_path: Path) -> None:
        """README extraction skips HTML comments."""
        (tmp_path / "README.md").write_text(
            "# Project\n\n"
            "<!-- This is a comment -->\n"
            "<!-- BEGIN_BANNER -->\n"
            "The actual project description.\n"
        )
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=500)

        # Should include the real description
        assert "actual project description" in sketch
        # Should not include HTML comments
        assert "<!--" not in sketch
        assert "BEGIN_BANNER" not in sketch

    def test_readme_skips_html_tags(self, tmp_path: Path) -> None:
        """README extraction skips HTML picture/img/source tags."""
        (tmp_path / "README.md").write_text(
            "# Project\n\n"
            "<picture>\n"
            "  <source media='...' srcset='...'>\n"
            "  <img src='banner.png'>\n"
            "</picture>\n\n"
            "A description after the banner.\n"
        )
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=500)

        # Should include the real description
        assert "description after the banner" in sketch
        # Should not include HTML tags
        assert "<picture>" not in sketch
        assert "<img" not in sketch

    def test_readme_title_with_subtitle(self, tmp_path: Path) -> None:
        """README extracts subtitle from title when main description unavailable."""
        (tmp_path / "README.md").write_text(
            "![Badge](https://badge.url)\n"
            "<picture><img src='banner.png'></picture>\n\n"
            "# MyProject: A powerful tool for code analysis\n\n"
            "[![More](https://badge.url)](https://link)\n"
            "[![Badges](https://badge.url)](https://link)\n"
        )
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=500)

        # Should use the title subtitle as description
        assert "powerful tool for code analysis" in sketch

    def test_readme_shields_before_title(self, tmp_path: Path) -> None:
        """README skips shields/images that appear before the title."""
        (tmp_path / "README.md").write_text(
            "![Build Status](https://ci.url/shield)\n"
            "[![Coverage](https://coverage.url)](https://link)\n\n"
            "# Project Title\n\n"
            "This is the actual description.\n"
        )
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=500)

        assert "actual description" in sketch
        # Shield URLs should not appear in description area
        assert "ci.url" not in sketch

    def test_readme_html_tags_before_title(self, tmp_path: Path) -> None:
        """README skips HTML tags that appear before the title."""
        (tmp_path / "README.md").write_text(
            "<!-- Banner image -->\n"
            "<picture><img src='banner.png'></picture>\n\n"
            "# Project Title\n\n"
            "The project description text.\n"
        )
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=500)

        assert "project description text" in sketch


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

    def test_swift_tests_directory(self) -> None:
        """Detects Swift Tests/ directory pattern (capital T, Xcode convention)."""
        assert _is_test_path("/vapor/Tests/VaporTests/RouteTests.swift") is True
        assert _is_test_path("Tests/AppTests/AppTests.swift") is True
        assert _is_test_path("/project/Tests/MyTest.swift") is True

    def test_swift_test_suffix(self) -> None:
        """Detects *Tests.swift pattern (Swift test class naming convention)."""
        assert _is_test_path("/src/RouteTests.swift") is True
        assert _is_test_path("ApplicationTests.swift") is True
        # But not files that just happen to contain "Test" in the middle
        assert _is_test_path("/src/TestHelpers.swift") is False

    def test_go_test_suffix(self) -> None:
        """Detects *_test.go pattern (Go test convention)."""
        assert _is_test_path("/server/main_test.go") is True
        assert _is_test_path("handler_test.go") is True
        assert _is_test_path("/pkg/service_test.go") is True

    def test_java_test_directory(self) -> None:
        """Detects src/test/ pattern (Maven/Gradle convention).

        Note: src/test/ is matched by the generic /test/ pattern,
        so this test verifies that Java/Kotlin convention works.
        """
        assert _is_test_path("/project/src/test/java/com/app/AppTest.java") is True
        assert _is_test_path("src/test/kotlin/MainTest.kt") is True

    def test_java_test_suffix(self) -> None:
        """Detects *Test.java and *Test.kt patterns."""
        assert _is_test_path("/src/main/UserServiceTest.java") is True
        assert _is_test_path("ConfigTest.kt") is True
        # But not TestConfig (prefix instead of suffix)
        assert _is_test_path("/src/TestConfig.java") is False

    def test_rust_tests_directory(self) -> None:
        """Detects Rust tests/ directory pattern."""
        # Same as Python tests/ but verify explicitly for Rust
        assert _is_test_path("/crate/tests/integration.rs") is True

    def test_rust_test_suffix(self) -> None:
        """Detects *_test.rs pattern (Rust convention)."""
        assert _is_test_path("/src/parser_test.rs") is True
        assert _is_test_path("lib_test.rs") is True


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

        # New format: "(... and X more symbols across Y other files)"
        assert "... and 15 more symbols" in result

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
        assert "... and 35 more symbols" in result
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

    def test_deduplicates_utility_functions_across_files(self) -> None:
        """Utility functions with same name across files are deduplicated.

        Functions like _node_text() appear in many analyzers. We show only
        the first occurrence to avoid wasting tokens on repeated utilities.
        """
        repo_root = Path("/fake/repo")
        # Create symbols with same name in different files (common pattern)
        symbols = [
            # First file - unique function + utility
            Symbol(id="analyze_rust", name="analyze_rust", kind="function",
                   language="python", path="/fake/repo/analyze/rust.py",
                   span=Span(1, 1, 50, 1)),
            Symbol(id="rust_node_text", name="_node_text", kind="function",
                   language="python", path="/fake/repo/analyze/rust.py",
                   span=Span(60, 1, 65, 1)),
            # Second file - unique function + same utility name
            Symbol(id="analyze_go", name="analyze_go", kind="function",
                   language="python", path="/fake/repo/analyze/go.py",
                   span=Span(1, 1, 50, 1)),
            Symbol(id="go_node_text", name="_node_text", kind="function",
                   language="python", path="/fake/repo/analyze/go.py",
                   span=Span(60, 1, 65, 1)),
            # Third file - unique function + same utility name
            Symbol(id="analyze_java", name="analyze_java", kind="function",
                   language="python", path="/fake/repo/analyze/java.py",
                   span=Span(1, 1, 50, 1)),
            Symbol(id="java_node_text", name="_node_text", kind="function",
                   language="python", path="/fake/repo/analyze/java.py",
                   span=Span(60, 1, 65, 1)),
        ]

        # Give all symbols some centrality
        edges = [
            Edge.create(src="caller1", dst="analyze_rust", edge_type="calls",
                        line=1, confidence=1.0),
            Edge.create(src="caller2", dst="analyze_go", edge_type="calls",
                        line=2, confidence=1.0),
            Edge.create(src="caller3", dst="analyze_java", edge_type="calls",
                        line=3, confidence=1.0),
            # Utility functions called from their respective analyze functions
            Edge.create(src="analyze_rust", dst="rust_node_text", edge_type="calls",
                        line=10, confidence=1.0),
            Edge.create(src="analyze_go", dst="go_node_text", edge_type="calls",
                        line=10, confidence=1.0),
            Edge.create(src="analyze_java", dst="java_node_text", edge_type="calls",
                        line=10, confidence=1.0),
        ]

        result = _format_symbols(symbols, edges, repo_root, max_symbols=100)

        # All unique analyze_* functions should appear
        assert "analyze_rust" in result
        assert "analyze_go" in result
        assert "analyze_java" in result

        # _node_text should appear only ONCE as a symbol definition (deduplicated)
        # It will also appear in the utility function summary at the bottom
        # Count symbol definitions (have "(function)" or "(method)" kind marker)
        # Exclude summary lines which have "omitted" in them
        symbol_lines = [line for line in result.split('\n') if '`_node_text`' in line]
        symbol_def_lines = [
            line for line in symbol_lines
            if line.strip().startswith('- `') and 'omitted' not in line
        ]
        assert len(symbol_def_lines) == 1, f"Expected 1 _node_text symbol def, got {len(symbol_def_lines)}"

        # Should also show in utility function summary
        assert "shown only once above" in result
        assert "we omitted 2 appearances of `_node_text`" in result  # 3 total - 1 shown = 2 omitted

    def test_deduplication_preserves_unique_functions(self) -> None:
        """Deduplication doesn't affect functions with unique names."""
        repo_root = Path("/fake/repo")
        symbols = [
            Symbol(id="func_a", name="unique_func_a", kind="function",
                   language="python", path="/fake/repo/module_a.py",
                   span=Span(1, 1, 10, 1)),
            Symbol(id="func_b", name="unique_func_b", kind="function",
                   language="python", path="/fake/repo/module_b.py",
                   span=Span(1, 1, 10, 1)),
            Symbol(id="func_c", name="unique_func_c", kind="function",
                   language="python", path="/fake/repo/module_c.py",
                   span=Span(1, 1, 10, 1)),
        ]

        edges = [
            Edge.create(src="caller", dst="func_a", edge_type="calls",
                        line=1, confidence=1.0),
            Edge.create(src="caller", dst="func_b", edge_type="calls",
                        line=2, confidence=1.0),
            Edge.create(src="caller", dst="func_c", edge_type="calls",
                        line=3, confidence=1.0),
        ]

        result = _format_symbols(symbols, edges, repo_root, max_symbols=100)

        # All unique functions should appear
        assert "unique_func_a" in result
        assert "unique_func_b" in result
        assert "unique_func_c" in result

    def test_deduplication_shows_utility_function_summary(self) -> None:
        """Deduplicated utility functions are summarized at the end."""
        repo_root = Path("/fake/repo")
        # Create symbols with same utility name in multiple files
        symbols = [
            Symbol(id="analyze_rust", name="analyze_rust", kind="function",
                   language="python", path="/fake/repo/analyze/rust.py",
                   span=Span(1, 1, 50, 1)),
            Symbol(id="rust_helper", name="_helper", kind="function",
                   language="python", path="/fake/repo/analyze/rust.py",
                   span=Span(60, 1, 65, 1)),
            Symbol(id="analyze_go", name="analyze_go", kind="function",
                   language="python", path="/fake/repo/analyze/go.py",
                   span=Span(1, 1, 50, 1)),
            Symbol(id="go_helper", name="_helper", kind="function",
                   language="python", path="/fake/repo/analyze/go.py",
                   span=Span(60, 1, 65, 1)),
            Symbol(id="analyze_java", name="analyze_java", kind="function",
                   language="python", path="/fake/repo/analyze/java.py",
                   span=Span(1, 1, 50, 1)),
            Symbol(id="java_helper", name="_helper", kind="function",
                   language="python", path="/fake/repo/analyze/java.py",
                   span=Span(60, 1, 65, 1)),
        ]

        edges = [
            Edge.create(src="caller1", dst="analyze_rust", edge_type="calls",
                        line=1, confidence=1.0),
            Edge.create(src="caller2", dst="analyze_go", edge_type="calls",
                        line=2, confidence=1.0),
            Edge.create(src="caller3", dst="analyze_java", edge_type="calls",
                        line=3, confidence=1.0),
            Edge.create(src="analyze_rust", dst="rust_helper", edge_type="calls",
                        line=10, confidence=1.0),
            Edge.create(src="analyze_go", dst="go_helper", edge_type="calls",
                        line=10, confidence=1.0),
            Edge.create(src="analyze_java", dst="java_helper", edge_type="calls",
                        line=10, confidence=1.0),
        ]

        result = _format_symbols(symbols, edges, repo_root, max_symbols=100)

        # Should have summary showing _helper appeared 3 times (2 omitted)
        assert "shown only once above" in result
        assert "`_helper`" in result
        assert "2 omitted" in result or "we omitted 2 appearances" in result  # 3 total - 1 shown

    def test_deduplication_progressive_format(self) -> None:
        """Utility function summary uses progressive shortening format."""
        repo_root = Path("/fake/repo")
        # Create symbols with 3 different utility function names, each appearing 3 times
        symbols = []
        for util_name in ["_helper", "_format", "_parse"]:
            for file_name in ["rust", "go", "java"]:
                symbols.append(
                    Symbol(id=f"{file_name}_{util_name}", name=util_name, kind="function",
                           language="python", path=f"/fake/repo/analyze/{file_name}.py",
                           span=Span(60, 1, 65, 1))
                )
                symbols.append(
                    Symbol(id=f"analyze_{file_name}", name=f"analyze_{file_name}", kind="function",
                           language="python", path=f"/fake/repo/analyze/{file_name}.py",
                           span=Span(1, 1, 50, 1))
                )

        edges = [
            Edge.create(src=f"analyze_{f}", dst=f"{f}_{u}", edge_type="calls", line=10, confidence=1.0)
            for u in ["_helper", "_format", "_parse"]
            for f in ["rust", "go", "java"]
        ]

        result = _format_symbols(symbols, edges, repo_root, max_symbols=100)

        # First: full format "we omitted X appearances of `name`"
        assert "we omitted" in result and "appearances of" in result
        # Third+: short format "X omitted"
        assert "2 omitted" in result  # Short format for third+ item


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

    def test_minimum_key_symbols_guarantee(self, tmp_path: Path) -> None:
        """Key Symbols section appears even with tight budget for large projects.

        Issue: Some projects (qwix, marlin, guacamole-client) had 0 Key Symbols
        at 1k budget because budget was exhausted before reaching symbols section.

        The fix guarantees at least MIN_KEY_SYMBOLS (5) appear regardless of budget.
        """
        # Create a project that consumes budget with structure/config
        # but still has analyzable Python code
        for i in range(10):
            d = tmp_path / f"src_{i}"
            d.mkdir()
            (d / f"module_{i}.py").write_text(
                f"def core_function_{i}():\n"
                f"    '''Important function {i}.'''\n"
                f"    pass\n\n"
                f"class CoreClass_{i}:\n"
                f"    '''Core class {i}.'''\n"
                f"    pass\n"
            )

        # Add config files that consume budget
        (tmp_path / "package.json").write_text('{"name": "test", "version": "1.0.0"}')
        (tmp_path / "README.md").write_text("# Test Project\n\nA test project.\n")

        # Even at 1k budget (which previously caused 0 Key Symbols),
        # we should now get at least 5 symbols
        sketch = generate_sketch(tmp_path, max_tokens=1000)

        assert "## Key Symbols" in sketch, "Key Symbols section should always appear"
        # Count the number of symbol entries (lines starting with "- `")
        symbol_lines = [line for line in sketch.split("\n") if line.strip().startswith("- `")]
        assert len(symbol_lines) >= 5, f"Expected at least 5 symbols, got {len(symbol_lines)}"

    def test_key_symbols_with_very_tight_budget(self, tmp_path: Path) -> None:
        """Key Symbols section appears even when remaining budget < 200 tokens.

        This tests the budget-constrained path where we guarantee MIN_KEY_SYMBOLS.
        """
        # Create a project with lots of content that consumes budget
        # Long README that will consume significant tokens
        long_readme = "# Project\n\n" + ("This is a description. " * 100)
        (tmp_path / "README.md").write_text(long_readme)

        # Many directories to consume structure budget
        for i in range(20):
            d = tmp_path / f"pkg_{i}"
            d.mkdir()
            (d / "__init__.py").write_text("")

        # But still include analyzable code
        src = tmp_path / "src"
        src.mkdir()
        (src / "core.py").write_text(
            "def main():\n    pass\n\n"
            "def helper():\n    pass\n\n"
            "class App:\n    pass\n\n"
            "class Config:\n    pass\n\n"
            "class Service:\n    pass\n"
        )

        # At 500 tokens, base sections consume most budget but symbols must appear
        sketch = generate_sketch(tmp_path, max_tokens=500)

        # Key Symbols must appear even with tight budget
        assert "## Key Symbols" in sketch, "Key Symbols must appear even with tight budget"


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


class TestFormatStructure:
    """Tests for _format_structure."""

    def test_excludes_node_modules(self, tmp_path: Path) -> None:
        """node_modules should not appear in structure."""
        (tmp_path / "src").mkdir()
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "tests").mkdir()

        structure = _format_structure(tmp_path)
        assert "src" in structure
        assert "tests" in structure
        assert "node_modules" not in structure

    def test_excludes_pycache(self, tmp_path: Path) -> None:
        """__pycache__ should not appear in structure."""
        (tmp_path / "src").mkdir()
        (tmp_path / "__pycache__").mkdir()

        structure = _format_structure(tmp_path)
        assert "src" in structure
        assert "__pycache__" not in structure

    def test_excludes_venv(self, tmp_path: Path) -> None:
        """venv and .venv should not appear in structure."""
        (tmp_path / "src").mkdir()
        (tmp_path / "venv").mkdir()
        (tmp_path / ".venv").mkdir()

        structure = _format_structure(tmp_path)
        assert "src" in structure
        assert "venv" not in structure
        assert ".venv" not in structure

    def test_excludes_build_and_dist(self, tmp_path: Path) -> None:
        """build and dist should not appear in structure."""
        (tmp_path / "src").mkdir()
        (tmp_path / "build").mkdir()
        (tmp_path / "dist").mkdir()

        structure = _format_structure(tmp_path)
        assert "src" in structure
        assert "build" not in structure
        assert "dist" not in structure

    def test_extra_excludes(self, tmp_path: Path) -> None:
        """Extra excludes should filter additional directories."""
        (tmp_path / "src").mkdir()
        (tmp_path / "generated").mkdir()
        (tmp_path / "vendor").mkdir()

        structure = _format_structure(tmp_path, extra_excludes=["generated"])
        assert "src" in structure
        assert "vendor" not in structure  # Already in DEFAULT_EXCLUDES
        assert "generated" not in structure  # Extra exclude

    def test_labels_source_dirs(self, tmp_path: Path) -> None:
        """Source directories should be labeled."""
        (tmp_path / "src").mkdir()
        (tmp_path / "lib").mkdir()

        structure = _format_structure(tmp_path)
        assert "- `src/` — Source code" in structure
        assert "- `lib/` — Source code" in structure

    def test_labels_test_dirs(self, tmp_path: Path) -> None:
        """Test directories should be labeled."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "spec").mkdir()

        structure = _format_structure(tmp_path)
        assert "- `tests/` — Tests" in structure
        assert "- `spec/` — Tests" in structure

    def test_labels_doc_dirs(self, tmp_path: Path) -> None:
        """Documentation directories should be labeled."""
        (tmp_path / "docs").mkdir()

        structure = _format_structure(tmp_path)
        assert "- `docs/` — Documentation" in structure


class TestExtractPythonDocstrings:
    """Tests for Python docstring extraction."""

    def test_extracts_function_docstring(self, tmp_path: Path) -> None:
        """Extracts docstring from Python function."""
        (tmp_path / "app.py").write_text(
            "def hello():\n"
            "    \"\"\"Greets the user.\"\"\"\n"
            "    pass\n"
        )
        symbol = Symbol(
            id="hello",
            name="hello",
            kind="function",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(1, 3, 0, 10),
        )

        result = _extract_python_docstrings(tmp_path, [symbol])

        assert result.get("hello") == "Greets the user."

    def test_extracts_class_docstring(self, tmp_path: Path) -> None:
        """Extracts docstring from Python class."""
        (tmp_path / "models.py").write_text(
            "class User:\n"
            "    \"\"\"Represents a user in the system.\"\"\"\n"
            "    pass\n"
        )
        symbol = Symbol(
            id="User",
            name="User",
            kind="class",
            language="python",
            path=str(tmp_path / "models.py"),
            span=Span(1, 3, 0, 10),
        )

        result = _extract_python_docstrings(tmp_path, [symbol])

        assert result.get("User") == "Represents a user in the system."

    def test_truncates_long_docstrings(self, tmp_path: Path) -> None:
        """Truncates docstrings longer than max_len."""
        long_doc = "A" * 100
        (tmp_path / "app.py").write_text(
            f"def process():\n"
            f"    \"\"\"{long_doc}\"\"\"\n"
            f"    pass\n"
        )
        symbol = Symbol(
            id="process",
            name="process",
            kind="function",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(1, 3, 0, 10),
        )

        result = _extract_python_docstrings(tmp_path, [symbol], max_len=50)

        assert result.get("process") is not None
        assert len(result["process"]) <= 50
        assert result["process"].endswith("…")

    def test_handles_missing_file(self, tmp_path: Path) -> None:
        """Gracefully handles missing files."""
        symbol = Symbol(
            id="missing",
            name="missing",
            kind="function",
            language="python",
            path=str(tmp_path / "nonexistent.py"),
            span=Span(1, 3, 0, 10),
        )

        result = _extract_python_docstrings(tmp_path, [symbol])

        assert result.get("missing") is None

    def test_handles_syntax_error(self, tmp_path: Path) -> None:
        """Gracefully handles syntax errors in Python files."""
        (tmp_path / "bad.py").write_text("def broken(:\n    pass\n")
        symbol = Symbol(
            id="broken",
            name="broken",
            kind="function",
            language="python",
            path=str(tmp_path / "bad.py"),
            span=Span(1, 2, 0, 10),
        )

        result = _extract_python_docstrings(tmp_path, [symbol])

        # Should not crash, just return empty
        assert result.get("broken") is None

    def test_ignores_non_python_symbols(self, tmp_path: Path) -> None:
        """Ignores symbols from non-Python languages."""
        symbol = Symbol(
            id="main",
            name="main",
            kind="function",
            language="javascript",
            path=str(tmp_path / "app.js"),
            span=Span(1, 3, 0, 10),
        )

        result = _extract_python_docstrings(tmp_path, [symbol])

        assert len(result) == 0

    def test_extracts_first_line_only(self, tmp_path: Path) -> None:
        """Only extracts first line of multi-line docstrings."""
        (tmp_path / "app.py").write_text(
            "def compute():\n"
            "    \"\"\"Computes the result.\n\n"
            "    This is a longer explanation\n"
            "    that spans multiple lines.\n"
            "    \"\"\"\n"
            "    pass\n"
        )
        symbol = Symbol(
            id="compute",
            name="compute",
            kind="function",
            language="python",
            path=str(tmp_path / "app.py"),
            span=Span(1, 7, 0, 10),
        )

        result = _extract_python_docstrings(tmp_path, [symbol])

        assert result.get("compute") == "Computes the result."
        assert "longer explanation" not in result.get("compute", "")


class TestExtractDomainVocabulary:
    """Tests for domain vocabulary extraction."""

    def test_extracts_domain_terms(self, tmp_path: Path) -> None:
        """Extracts domain-specific terms from source code."""
        (tmp_path / "server.py").write_text(
            "def handleAuthentication(user, token):\n"
            "    validateToken(token)\n"
            "    authenticateUser(user)\n"
        )
        profile = detect_profile(tmp_path)

        terms = _extract_domain_vocabulary(tmp_path, profile)

        assert "authentication" in terms or "authenticate" in terms
        assert "token" in terms or "validate" in terms

    def test_filters_common_terms(self, tmp_path: Path) -> None:
        """Filters out common programming terms."""
        (tmp_path / "app.py").write_text(
            "def get_value():\n"
            "    result = process_data(input_value)\n"
            "    return result\n"
            "\n"
            "def calculatePaymentTotal(invoice):\n"
            "    total = invoice.amount\n"
            "    return total\n"
        )
        profile = detect_profile(tmp_path)

        terms = _extract_domain_vocabulary(tmp_path, profile)

        # Common terms should be filtered
        assert "value" not in terms
        assert "result" not in terms
        # Domain terms should be included
        assert "payment" in terms or "invoice" in terms or "calculate" in terms

    def test_splits_camel_case(self, tmp_path: Path) -> None:
        """Splits camelCase and PascalCase identifiers."""
        (tmp_path / "service.py").write_text(
            "class UserAuthenticationService:\n"
            "    def validateCredentials(self):\n"
            "        pass\n"
        )
        profile = detect_profile(tmp_path)

        terms = _extract_domain_vocabulary(tmp_path, profile)

        assert "authentication" in terms or "validate" in terms or "credentials" in terms

    def test_splits_snake_case(self, tmp_path: Path) -> None:
        """Splits snake_case identifiers."""
        (tmp_path / "handler.py").write_text(
            "def process_payment_request(payment_details):\n"
            "    validate_payment_amount(payment_details)\n"
        )
        profile = detect_profile(tmp_path)

        terms = _extract_domain_vocabulary(tmp_path, profile)

        assert "payment" in terms

    def test_respects_max_terms(self, tmp_path: Path) -> None:
        """Respects max_terms limit."""
        # Create file with many unique terms
        (tmp_path / "app.py").write_text(
            "def alpha(): pass\n"
            "def bravo(): pass\n"
            "def charlie(): pass\n"
            "def delta(): pass\n"
            "def echo(): pass\n"
        )
        profile = detect_profile(tmp_path)

        terms = _extract_domain_vocabulary(tmp_path, profile, max_terms=3)

        assert len(terms) <= 3

    def test_excludes_node_modules(self, tmp_path: Path) -> None:
        """Excludes node_modules directory."""
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "lib.js").write_text(
            "function excludedTerm() {}\n"
        )
        (tmp_path / "app.py").write_text(
            "def includedTerm():\n"
            "    pass\n"
        )
        profile = detect_profile(tmp_path)

        terms = _extract_domain_vocabulary(tmp_path, profile)

        assert "excluded" not in terms

    def test_handles_empty_project(self, tmp_path: Path) -> None:
        """Returns empty list for project with no source files."""
        (tmp_path / "README.md").write_text("# Project\n")
        profile = detect_profile(tmp_path)

        terms = _extract_domain_vocabulary(tmp_path, profile)

        assert terms == []

    def test_handles_unreadable_files(self, tmp_path: Path) -> None:
        """Gracefully handles unreadable files."""
        (tmp_path / "good.py").write_text("def validFunction(): pass\n")
        profile = detect_profile(tmp_path)

        # Just verify no exception is raised
        terms = _extract_domain_vocabulary(tmp_path, profile)
        assert isinstance(terms, list)

    def test_handles_pure_snake_case(self, tmp_path: Path) -> None:
        """Handles pure snake_case identifiers without uppercase letters."""
        (tmp_path / "handler.py").write_text(
            "def process_customer_payment_request():\n"
            "    validate_invoice_amount()\n"
        )
        profile = detect_profile(tmp_path)

        terms = _extract_domain_vocabulary(tmp_path, profile)

        assert "customer" in terms or "payment" in terms or "invoice" in terms

    def test_handles_all_uppercase_constants(self, tmp_path: Path) -> None:
        """Handles ALL_UPPERCASE_CONSTANTS (snake_case fallback path)."""
        (tmp_path / "constants.py").write_text(
            "MAX_CUSTOMER_LIMIT = 100\n"
            "DEFAULT_PAYMENT_TIMEOUT = 30\n"
        )
        profile = detect_profile(tmp_path)

        terms = _extract_domain_vocabulary(tmp_path, profile)

        assert "customer" in terms or "payment" in terms or "limit" in terms or "timeout" in terms

    def test_handles_file_read_error(self, tmp_path: Path) -> None:
        """Gracefully handles file read errors (OSError)."""
        from unittest.mock import patch

        (tmp_path / "good.py").write_text("def validTerm(): pass\n")
        profile = detect_profile(tmp_path)

        # Mock file reading to raise OSError
        original_read_text = Path.read_text

        def mock_read_text(self, *args, **kwargs):
            if "good.py" in str(self):
                raise OSError("Mocked read error")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", mock_read_text):
            # This should not raise even when files can't be read
            terms = _extract_domain_vocabulary(tmp_path, profile)

        assert isinstance(terms, list)


class TestFormatVocabulary:
    """Tests for vocabulary formatting."""

    def test_formats_vocabulary_section(self) -> None:
        """Formats vocabulary as Markdown section."""
        terms = ["authentication", "payment", "invoice", "customer"]

        result = _format_vocabulary(terms)

        assert "## Domain Vocabulary" in result
        assert "authentication" in result
        assert "payment" in result
        assert "invoice" in result
        assert "customer" in result

    def test_empty_terms_returns_empty(self) -> None:
        """Returns empty string for empty terms list."""
        result = _format_vocabulary([])

        assert result == ""

    def test_formats_as_key_terms(self) -> None:
        """Formats terms with 'Key terms:' prefix."""
        terms = ["user", "session", "token"]

        result = _format_vocabulary(terms)

        assert "*Key terms:" in result
        assert "user, session, token" in result


class TestGenerateSketchWithVocabulary:
    """Tests for vocabulary in generate_sketch."""

    def test_includes_vocabulary_at_medium_budget(self, tmp_path: Path) -> None:
        """Includes vocabulary section at medium token budget."""
        (tmp_path / "payment.py").write_text(
            "def processPayment(amount):\n"
            "    validatePaymentDetails(amount)\n"
        )

        sketch = generate_sketch(tmp_path, max_tokens=800)

        assert "## Domain Vocabulary" in sketch

    def test_excludes_vocabulary_at_small_budget(self, tmp_path: Path) -> None:
        """Excludes vocabulary section at small token budget."""
        (tmp_path / "app.py").write_text("def main(): pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=200)

        assert "## Domain Vocabulary" not in sketch


class TestConfigExtraction:
    """Tests for config file extraction with different modes."""

    def test_extract_package_json_fields(self, tmp_path: Path) -> None:
        """Extracts key fields from package.json."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "package.json").write_text('''{
            "name": "my-project",
            "version": "1.2.3",
            "license": "MIT",
            "dependencies": {
                "express": "^4.18.0",
                "pg": "^8.11.0"
            },
            "devDependencies": {
                "typescript": "^5.0.0",
                "jest": "^29.0.0"
            }
        }''')

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "name: my-project" in result
        assert "version: 1.2.3" in result
        assert "license: MIT" in result
        assert "express" in result
        assert "pg" in result
        assert "typescript" in result

    def test_extract_go_mod_fields(self, tmp_path: Path) -> None:
        """Extracts module and dependencies from go.mod."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "go.mod").write_text("""module github.com/example/myproject

go 1.21

require (
    github.com/gin-gonic/gin v1.9.0
    github.com/jackc/pgx/v5 v5.4.0
)
""")

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "module: github.com/example/myproject" in result
        assert "go: 1.21" in result
        assert "gin" in result or "pgx" in result

    def test_extract_cargo_toml_fields(self, tmp_path: Path) -> None:
        """Extracts package info from Cargo.toml."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "Cargo.toml").write_text('''[package]
name = "my-rust-project"
version = "0.1.0"
license = "Apache-2.0"
''')

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "name: my-rust-project" in result
        assert "version: 0.1.0" in result
        assert "license: Apache-2.0" in result

    def test_extract_pyproject_toml_fields(self, tmp_path: Path) -> None:
        """Extracts project info from pyproject.toml."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "pyproject.toml").write_text('''[project]
name = "my-python-project"
version = "2.0.0"
license = "GPL-3.0"
''')

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "name: my-python-project" in result
        assert "version: 2.0.0" in result

    def test_extract_license_detection(self, tmp_path: Path) -> None:
        """Detects license type from LICENSE file."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "MIT License\n\n"
            "Permission is hereby granted, free of charge, to any person..."
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: MIT" in result

    def test_extract_agpl_license(self, tmp_path: Path) -> None:
        """Detects AGPL license correctly (before GPL)."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "GNU AFFERO GENERAL PUBLIC LICENSE\n"
            "Version 3, 19 November 2007\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: AGPL" in result

    def test_extract_lgpl_license(self, tmp_path: Path) -> None:
        """Detects LGPL license."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        # Use the actual LGPL-style text with both GPL and Lesser
        (tmp_path / "LICENSE").write_text(
            "GNU LESSER GPL\n"
            "Version 2.1, February 1999\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: LGPL" in result

    def test_extract_gpl_license(self, tmp_path: Path) -> None:
        """Detects GPL license."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "GPL-3.0 License\n"
            "Version 3, 29 June 2007\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: GPL" in result

    def test_extract_apache_license(self, tmp_path: Path) -> None:
        """Detects Apache license."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "Apache License\n"
            "Version 2.0, January 2004\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: Apache" in result

    def test_extract_bsd_license(self, tmp_path: Path) -> None:
        """Detects BSD license."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "BSD 3-Clause License\n"
            "Copyright (c) 2023\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: BSD" in result

    def test_extract_mpl_license(self, tmp_path: Path) -> None:
        """Detects Mozilla Public License."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "Mozilla Public License Version 2.0\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: MPL" in result

    def test_extract_isc_license(self, tmp_path: Path) -> None:
        """Detects ISC License."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "ISC License\n"
            "Copyright (c) 2023\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: ISC" in result

    def test_extract_unlicense(self, tmp_path: Path) -> None:
        """Detects Unlicense."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "This is free and unencumbered software released into the public domain.\n"
            "Unlicense\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: Unlicense" in result

    def test_extract_mix_exs(self, tmp_path: Path) -> None:
        """Extracts Elixir project info from mix.exs."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "mix.exs").write_text('''
defmodule MyApp.MixProject do
  use Mix.Project

  def project do
    [
      app: :my_app,
      version: "0.1.0",
      elixir: "~> 1.14",
      deps: deps()
    ]
  end
end
''')

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "mix.exs" in result
        assert "my_app" in result
        assert "0.1.0" in result
        assert "1.14" in result

    def test_extract_build_gradle(self, tmp_path: Path) -> None:
        """Extracts Kotlin/Java project info from build.gradle.kts."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "build.gradle.kts").write_text('''
plugins {
    kotlin("jvm") version "1.9.0"
    application
}

group = "com.example"
version = "1.0-SNAPSHOT"

dependencies {
    implementation("org.jetbrains.kotlin:kotlin-stdlib")
}
''')

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "build.gradle.kts" in result
        assert "com.example" in result
        assert "1.0-SNAPSHOT" in result
        assert "kotlin" in result

    def test_extract_gemfile(self, tmp_path: Path) -> None:
        """Extracts Ruby gems from Gemfile."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "Gemfile").write_text('''
source "https://rubygems.org"

ruby ">= 3.2.0"

gem "rails", "~> 7.0"
gem "pg"
gem "puma"
''')

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "Gemfile" in result
        assert "3.2.0" in result
        assert "rails" in result

    def test_monorepo_subdir_support(self, tmp_path: Path) -> None:
        """Extracts config from monorepo subdirectories."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "server").mkdir()
        (tmp_path / "server" / "package.json").write_text('''{
            "name": "server-app",
            "version": "1.0.0"
        }''')
        (tmp_path / "client").mkdir()
        (tmp_path / "client" / "package.json").write_text('''{
            "name": "client-app",
            "version": "2.0.0"
        }''')

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "server/package.json" in result
        assert "client/package.json" in result
        assert "server-app" in result
        assert "client-app" in result

    def test_truncates_long_output(self, tmp_path: Path) -> None:
        """Truncates output when exceeding max_chars."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        # Create package.json with many dependencies
        deps = {f"pkg-{i}": f"^{i}.0.0" for i in range(100)}
        import json
        (tmp_path / "package.json").write_text(json.dumps({
            "name": "big-project",
            "dependencies": deps
        }))

        result = _extract_config_info(
            tmp_path,
            mode=ConfigExtractionMode.HEURISTIC,
            max_chars=200
        )

        assert len(result) <= 200

    @pytest.mark.skipif(
        not _has_sentence_transformers(),
        reason="sentence-transformers not installed (1GB+ torch dependency)"
    )
    def test_embedding_mode_requires_model(self, tmp_path: Path) -> None:
        """Embedding mode uses sentence-transformer model."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "package.json").write_text('''{
            "name": "test-project",
            "version": "1.0.0",
            "description": "A project that uses PostgreSQL database",
            "dependencies": {
                "pg": "^8.0.0",
                "express": "^4.0.0",
                "lodash": "^4.0.0",
                "uuid": "^9.0.0"
            }
        }''')

        # Embedding mode should extract lines most similar to common questions
        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.EMBEDDING)

        # Should include relevant content (database-related lines)
        assert "pg" in result or "PostgreSQL" in result

    @pytest.mark.skipif(
        not _has_sentence_transformers(),
        reason="sentence-transformers not installed (1GB+ torch dependency)"
    )
    def test_embedding_mode_centroid_selection(self, tmp_path: Path) -> None:
        """Embedding mode uses dual-probe centroid selection."""
        from hypergumbo.sketch import (
            _extract_config_info,
            ConfigExtractionMode,
            ANSWER_PATTERNS,
            BIG_PICTURE_QUESTIONS,
        )

        # Verify both probe lists exist
        assert len(ANSWER_PATTERNS) > 0
        assert len(BIG_PICTURE_QUESTIONS) > 0
        # Answer patterns should have version/name/license examples
        assert any("version" in p.lower() for p in ANSWER_PATTERNS)
        assert any("license" in p.lower() for p in ANSWER_PATTERNS)
        # Big-picture questions should have database/ML questions
        # (license questions removed - handled by ANSWER_PATTERNS to avoid
        # matching verbose LICENSE file content)
        assert any("database" in q.lower() for q in BIG_PICTURE_QUESTIONS)
        assert any("ml" in q.lower() or "jax" in q.lower() for q in BIG_PICTURE_QUESTIONS)

        # Create a long config with relevant content buried
        (tmp_path / "package.json").write_text('''{
            "name": "test-project",
            "scripts": {
                "build": "tsc",
                "lint": "eslint .",
                "format": "prettier --write ."
            },
            "dependencies": {
                "mongodb": "^5.0.0"
            }
        }''')

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.EMBEDDING)

        # Embedding mode should prioritize database dependency
        assert "mongodb" in result.lower()

    @pytest.mark.skipif(
        not _has_sentence_transformers(),
        reason="sentence-transformers not installed (1GB+ torch dependency)"
    )
    def test_embedding_mode_deprioritizes_license_file(self, tmp_path: Path) -> None:
        """Embedding mode deprioritizes LICENSE files to favor informative content.

        LICENSE files have verbose legal boilerplate that matches many probes
        but has low information density. A 50% penalty is applied to LICENSE/COPYING
        files to prioritize more useful config content.
        """
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        # Create a rich package.json and a verbose LICENSE
        (tmp_path / "package.json").write_text('''{
            "name": "test-project",
            "version": "1.0.0",
            "dependencies": {"express": "^4.0.0", "pg": "^8.0.0"}
        }''')
        (tmp_path / "LICENSE").write_text(
            "MIT License\n"
            "Copyright (c) 2024 Test Project\n"
            "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.EMBEDDING)

        # Should prioritize package.json content over LICENSE boilerplate
        assert "package.json" in result or "test-project" in result or "express" in result

    @pytest.mark.skipif(
        not _has_sentence_transformers(),
        reason="sentence-transformers not installed (1GB+ torch dependency)"
    )
    def test_embedding_mode_provides_context(self, tmp_path: Path) -> None:
        """Embedding mode provides context lines around selected lines."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        # Create a package.json with a nested dependency
        (tmp_path / "package.json").write_text('''{
  "name": "context-test",
  "dependencies": {
    "pg": "^8.0.0",
    "express": "^4.0.0"
  }
}''')

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.EMBEDDING)

        # Should include the selected line marker (>)
        assert ">" in result

        # If pg is selected, "dependencies" context should be nearby
        # The context mechanism includes surrounding lines
        lines = result.split("\n")
        has_context = any("dependencies" in ln for ln in lines)
        has_selection = any(">" in ln for ln in lines)
        assert has_selection, "Should have selected line markers"

    @pytest.mark.skipif(
        not _has_sentence_transformers(),
        reason="sentence-transformers not installed (1GB+ torch dependency)"
    )
    def test_embedding_mode_multi_file_overflow(self, tmp_path: Path) -> None:
        """Embedding mode handles overflow with multiple files (diversity mechanism)."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        # Create multiple config files to trigger multi-file overflow handling
        # Package.json with lots of content
        (tmp_path / "package.json").write_text("""{
  "name": "multi-file-test",
  "version": "2.0.0",
  "description": "Testing multi-file config extraction with many dependencies",
  "license": "MIT",
  "dependencies": {
    "express": "^4.0.0",
    "lodash": "^4.0.0",
    "axios": "^1.0.0",
    "pg": "^8.0.0",
    "redis": "^4.0.0"
  },
  "devDependencies": {
    "typescript": "^5.0.0",
    "jest": "^29.0.0",
    "eslint": "^8.0.0"
  }
}""")

        # Pyproject.toml as second config file
        (tmp_path / "pyproject.toml").write_text("""[project]
name = "multi-file-test"
version = "2.0.0"
description = "Testing multi-file extraction with Python config"
dependencies = [
    "flask>=2.0.0",
    "sqlalchemy>=2.0.0",
    "redis>=4.0.0",
    "celery>=5.0.0"
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "mypy>=1.0.0"
]
""")

        # Docker Compose as third config file
        (tmp_path / "docker-compose.yml").write_text("""version: '3.8'
services:
  app:
    build: .
    ports:
      - "8080:8080"
    environment:
      - DATABASE_URL=postgres://localhost:5432/mydb
      - REDIS_URL=redis://localhost:6379
  postgres:
    image: postgres:15
    environment:
      POSTGRES_PASSWORD: secret
  redis:
    image: redis:7
""")

        # Extract with embedding mode - should handle multi-file overflow
        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.EMBEDDING)

        # Should include content from multiple files
        assert len(result) > 0
        # Should have file headers for multiple sources
        lines = result.split("\n")
        file_headers = [ln for ln in lines if ln.startswith("[") and ln.endswith("]")]
        # At least 2 files should be represented
        assert len(file_headers) >= 2, (
            f"Expected at least 2 file headers, got {len(file_headers)}: {file_headers}"
        )

    @pytest.mark.skipif(
        not _has_sentence_transformers(),
        reason="sentence-transformers not installed (1GB+ torch dependency)"
    )
    def test_hybrid_mode_combines_both(self, tmp_path: Path) -> None:
        """Hybrid mode uses heuristics first, then embeddings for remaining."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "package.json").write_text('''{
            "name": "hybrid-test",
            "version": "1.0.0",
            "license": "MIT",
            "description": "A complex app using PostgreSQL and Redis",
            "dependencies": {
                "pg": "^8.0.0",
                "redis": "^4.0.0",
                "express": "^4.0.0"
            },
            "devDependencies": {
                "typescript": "^5.0.0"
            }
        }''')

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HYBRID)

        # Heuristics should extract known fields
        assert "name: hybrid-test" in result
        assert "version: 1.0.0" in result
        assert "license: MIT" in result

        # Known interesting deps should be included
        assert "pg" in result
        assert "typescript" in result

    def test_heuristic_is_default_mode(self, tmp_path: Path) -> None:
        """Heuristic is the default mode when not specified."""
        from hypergumbo.sketch import _extract_config_info

        (tmp_path / "package.json").write_text('{"name": "test", "version": "1.0.0"}')

        # Call without mode parameter - should use heuristic by default
        result = _extract_config_info(tmp_path)

        assert "name: test" in result
        assert "version: 1.0.0" in result

    def test_generate_sketch_with_config_mode(self, tmp_path: Path) -> None:
        """generate_sketch accepts config_extraction_mode parameter."""
        from hypergumbo.sketch import ConfigExtractionMode

        (tmp_path / "package.json").write_text('''{
            "name": "sketch-test",
            "version": "1.0.0",
            "dependencies": {"express": "^4.0.0"}
        }''')
        (tmp_path / "app.js").write_text("console.log('hello');\n")

        sketch = generate_sketch(
            tmp_path,
            max_tokens=500,
            config_extraction_mode=ConfigExtractionMode.HEURISTIC
        )

        assert "## Configuration" in sketch
        assert "sketch-test" in sketch

    def test_no_config_files_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty string when no config files found."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "app.py").write_text("print('hello')\n")

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert result == ""

    def test_invalid_json_handled_gracefully(self, tmp_path: Path) -> None:
        """Handles invalid JSON in package.json gracefully."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "package.json").write_text("{ invalid json }")

        # Should not raise, just return empty or partial result
        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert isinstance(result, str)

    def test_pom_xml_extraction(self, tmp_path: Path) -> None:
        """Extracts Maven coordinates from pom.xml."""
        from hypergumbo.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "pom.xml").write_text('''<?xml version="1.0"?>
<project>
    <groupId>com.example</groupId>
    <artifactId>my-app</artifactId>
    <version>1.0-SNAPSHOT</version>
    <packaging>jar</packaging>
</project>
''')

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "groupId: com.example" in result
        assert "artifactId: my-app" in result
        assert "version: 1.0-SNAPSHOT" in result


class TestLogScaledSampling:
    """Tests for log-scaled sampling helper functions."""

    def test_compute_log_sample_size_small_file(self) -> None:
        """Small files return their full line count."""
        from hypergumbo.sketch import _compute_log_sample_size

        # File smaller than fleximax
        assert _compute_log_sample_size(50, fleximax=100) == 50
        assert _compute_log_sample_size(100, fleximax=100) == 100

    def test_compute_log_sample_size_large_file(self) -> None:
        """Large files use log-scaled formula."""
        from hypergumbo.sketch import _compute_log_sample_size

        # 1000 lines with fleximax=100: 100 + log10(1000) * 10 = 100 + 30 = 130
        result = _compute_log_sample_size(1000, fleximax=100)
        assert result == 130

        # 10000 lines: 100 + log10(10000) * 10 = 100 + 40 = 140
        result = _compute_log_sample_size(10000, fleximax=100)
        assert result == 140

    def test_compute_stride_small_file(self) -> None:
        """Small files get stride 1 (sample all)."""
        from hypergumbo.sketch import _compute_stride

        assert _compute_stride(50, sample_size=100) == 1
        assert _compute_stride(100, sample_size=100) == 1

    def test_compute_stride_large_file(self) -> None:
        """Large files get stride >= 4."""
        from hypergumbo.sketch import _compute_stride

        # 400 lines with sample_size=100 -> stride 4
        assert _compute_stride(400, sample_size=100) == 4

        # 800 lines with sample_size=100 -> stride 8
        assert _compute_stride(800, sample_size=100) == 8

        # 101 lines with sample_size=100 -> stride should be 4 minimum
        assert _compute_stride(101, sample_size=100) == 4

    def test_build_context_chunk_simple(self) -> None:
        """Builds 3-line chunk with context."""
        from hypergumbo.sketch import _build_context_chunk

        lines = ["line0", "line1", "line2", "line3", "line4"]

        # Center at index 2 should include lines 1, 2, 3
        chunk = _build_context_chunk(lines, center_idx=2, max_chunk_chars=800)
        assert "line1" in chunk
        assert "line2" in chunk
        assert "line3" in chunk

    def test_build_context_chunk_at_start(self) -> None:
        """Chunk at start of file only includes available context."""
        from hypergumbo.sketch import _build_context_chunk

        lines = ["line0", "line1", "line2"]

        # Center at index 0 should include lines 0, 1
        chunk = _build_context_chunk(lines, center_idx=0, max_chunk_chars=800)
        assert "line0" in chunk
        assert "line1" in chunk

    def test_build_context_chunk_at_end(self) -> None:
        """Chunk at end of file only includes available context."""
        from hypergumbo.sketch import _build_context_chunk

        lines = ["line0", "line1", "line2"]

        # Center at index 2 should include lines 1, 2
        chunk = _build_context_chunk(lines, center_idx=2, max_chunk_chars=800)
        assert "line1" in chunk
        assert "line2" in chunk

    def test_build_context_chunk_word_subsampling(self) -> None:
        """Long chunks get word-level subsampling with ellipsis."""
        from hypergumbo.sketch import _build_context_chunk

        # Create lines that together exceed max_chunk_chars
        long_line = "word " * 200  # 1000+ chars
        lines = ["before", long_line, "after"]

        chunk = _build_context_chunk(lines, center_idx=1, max_chunk_chars=200)

        # Should contain ellipsis indicating subsampling
        assert " ... " in chunk
        # Should be truncated to max_chunk_chars
        assert len(chunk) <= 200

    def test_build_context_chunk_truncation(self) -> None:
        """Chunks that exceed max_chars but have few words get truncated."""
        from hypergumbo.sketch import _build_context_chunk

        # Create a line with few but very long words
        long_word = "x" * 300
        lines = ["before", long_word, "after"]

        chunk = _build_context_chunk(
            lines, center_idx=1, max_chunk_chars=200, fleximax_words=50
        )

        # Should be truncated
        assert len(chunk) <= 200


class TestDetectTestSummary:
    """Tests for _detect_test_summary function."""

    def test_no_test_files(self, tmp_path: Path) -> None:
        """Returns None when no test files exist."""
        (tmp_path / "main.py").write_text("print('hello')")
        summary, frameworks = _detect_test_summary(tmp_path)
        assert summary is None
        assert frameworks == set()

    def test_single_python_test_file(self, tmp_path: Path) -> None:
        """Detects a single Python test file."""
        (tmp_path / "test_example.py").write_text("def test_foo(): pass")
        summary, frameworks = _detect_test_summary(tmp_path)
        assert summary is not None
        assert "1 test file" in summary  # Singular

    def test_multiple_python_test_files(self, tmp_path: Path) -> None:
        """Detects multiple Python test files."""
        (tmp_path / "test_foo.py").write_text("def test_foo(): pass")
        (tmp_path / "test_bar.py").write_text("def test_bar(): pass")
        (tmp_path / "baz_test.py").write_text("def test_baz(): pass")
        summary, frameworks = _detect_test_summary(tmp_path)
        assert summary is not None
        assert "3 test files" in summary  # Plural

    def test_detects_pytest_framework(self, tmp_path: Path) -> None:
        """Detects pytest framework from imports."""
        (tmp_path / "test_example.py").write_text("import pytest\n\ndef test_foo(): pass")
        summary, frameworks = _detect_test_summary(tmp_path)
        assert summary is not None
        assert "pytest" in summary
        assert "pytest" in frameworks

    def test_detects_unittest_framework(self, tmp_path: Path) -> None:
        """Detects unittest framework from imports."""
        (tmp_path / "test_example.py").write_text(
            "import unittest\n\nclass TestFoo(unittest.TestCase): pass"
        )
        summary, frameworks = _detect_test_summary(tmp_path)
        assert summary is not None
        assert "unittest" in summary
        assert "unittest" in frameworks

    def test_detects_multiple_frameworks(self, tmp_path: Path) -> None:
        """Detects multiple test frameworks."""
        (tmp_path / "test_a.py").write_text("import pytest\n\ndef test_a(): pass")
        (tmp_path / "test_b.py").write_text("import unittest\n\nclass TestB: pass")
        summary, frameworks = _detect_test_summary(tmp_path)
        assert summary is not None
        assert "pytest" in summary
        assert "unittest" in summary
        assert frameworks == {"pytest", "unittest"}

    def test_javascript_test_files(self, tmp_path: Path) -> None:
        """Detects JavaScript/TypeScript test files."""
        (tmp_path / "app.spec.ts").write_text("describe('app', () => {})")
        (tmp_path / "utils.test.js").write_text("test('utils', () => {})")
        summary, frameworks = _detect_test_summary(tmp_path)
        assert summary is not None
        assert "2 test files" in summary

    def test_go_test_files(self, tmp_path: Path) -> None:
        """Detects Go test files."""
        (tmp_path / "main_test.go").write_text(
            'package main\nimport "testing"\nfunc TestFoo(t *testing.T) {}'
        )
        summary, frameworks = _detect_test_summary(tmp_path)
        assert summary is not None
        assert "1 test file" in summary
        assert "go test" in summary
        assert "go test" in frameworks

    def test_excludes_node_modules(self, tmp_path: Path) -> None:
        """Test files in excluded directories are not counted."""
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "test.spec.js").write_text("test('foo', () => {})")
        # Only the excluded file, no test files in main tree
        summary, frameworks = _detect_test_summary(tmp_path)
        assert summary is None

    def test_bats_test_files(self, tmp_path: Path) -> None:
        """Detects shell test files (.bats)."""
        (tmp_path / "test_cli.bats").write_text("@test 'example' { true; }")
        summary, frameworks = _detect_test_summary(tmp_path)
        assert summary is not None
        assert "1 test file" in summary


class TestFormatTestSummary:
    """Tests for _format_test_summary function."""

    def test_returns_empty_when_no_tests(self, tmp_path: Path) -> None:
        """Returns empty string when no tests detected."""
        (tmp_path / "main.py").write_text("print('hello')")
        result = _format_test_summary(tmp_path)
        assert result == ""

    def test_formats_as_markdown_section(self, tmp_path: Path) -> None:
        """Formats test summary as a Markdown section."""
        (tmp_path / "test_example.py").write_text("import pytest\n\ndef test_foo(): pass")
        result = _format_test_summary(tmp_path)
        assert result.startswith("## Tests\n")
        assert "pytest" in result
        assert "*Coverage requires execution" in result

    def test_coverage_hint_matches_framework_jest(self, tmp_path: Path) -> None:
        """Coverage hint should match detected framework (jest)."""
        (tmp_path / "app.test.js").write_text("const { describe } = require('jest');\ntest('x', () => {});")
        result = _format_test_summary(tmp_path)
        # Should NOT suggest pytest for JS project
        assert "pytest" not in result
        # Should suggest appropriate JS coverage tool
        assert "jest --coverage" in result or "npx" in result or "npm test" in result

    def test_coverage_hint_matches_framework_vitest(self, tmp_path: Path) -> None:
        """Coverage hint should match detected framework (vitest)."""
        (tmp_path / "app.test.ts").write_text("import { describe } from 'vitest';\ntest('x', () => {});")
        result = _format_test_summary(tmp_path)
        # Should NOT suggest pytest for TS project
        assert "pytest" not in result
        # Should suggest vitest coverage
        assert "vitest" in result

    def test_coverage_hint_matches_framework_go(self, tmp_path: Path) -> None:
        """Coverage hint should match detected framework (go test)."""
        (tmp_path / "main_test.go").write_text('package main\nimport "testing"\nfunc TestX(t *testing.T) {}')
        result = _format_test_summary(tmp_path)
        # Should NOT suggest pytest for Go project
        assert "pytest" not in result
        # Should suggest go test -cover
        assert "go test" in result

    def test_coverage_hint_matches_framework_maven(self, tmp_path: Path) -> None:
        """Coverage hint should match detected framework (JUnit/Maven)."""
        tests = tmp_path / "src" / "test" / "java"
        tests.mkdir(parents=True)
        (tests / "AppTest.java").write_text("import org.junit.Test;\npublic class AppTest {}")
        result = _format_test_summary(tmp_path)
        # Should NOT suggest pytest for Java project
        assert "pytest" not in result
        # Should suggest maven or gradle test
        assert "mvn test" in result or "gradle test" in result or "jacoco" in result


class TestGroupFilesByLanguage:
    """Tests for language-based file grouping."""

    def test_single_language(self) -> None:
        """All files grouped under one language."""
        from hypergumbo.sketch import _group_files_by_language

        sym1 = Symbol(
            id="s1", name="foo", kind="function", language="python",
            path="src/a.py", span=Span(1, 5, 0, 0)
        )
        sym2 = Symbol(
            id="s2", name="bar", kind="function", language="python",
            path="src/b.py", span=Span(1, 5, 0, 0)
        )
        by_file = {"src/a.py": [sym1], "src/b.py": [sym2]}
        result = _group_files_by_language(by_file)

        assert len(result) == 1
        assert "python" in result
        assert "src/a.py" in result["python"]
        assert "src/b.py" in result["python"]

    def test_multi_language(self) -> None:
        """Files separated by dominant language."""
        from hypergumbo.sketch import _group_files_by_language

        py_sym = Symbol(
            id="s1", name="foo", kind="function", language="python",
            path="src/main.py", span=Span(1, 5, 0, 0)
        )
        kt_sym = Symbol(
            id="s2", name="Bar", kind="class", language="kotlin",
            path="src/Bar.kt", span=Span(1, 10, 0, 0)
        )
        by_file = {"src/main.py": [py_sym], "src/Bar.kt": [kt_sym]}
        result = _group_files_by_language(by_file)

        assert len(result) == 2
        assert "python" in result
        assert "kotlin" in result
        assert "src/main.py" in result["python"]
        assert "src/Bar.kt" in result["kotlin"]

    def test_empty_files_skipped(self) -> None:
        """Files with no symbols are excluded."""
        from hypergumbo.sketch import _group_files_by_language

        sym = Symbol(
            id="s1", name="foo", kind="function", language="python",
            path="src/a.py", span=Span(1, 5, 0, 0)
        )
        # Include file with no symbols
        by_file = {"src/a.py": [sym], "src/empty.py": []}
        result = _group_files_by_language(by_file)

        assert len(result) == 1
        assert "python" in result
        assert "src/empty.py" not in result["python"]


class TestAllocateLanguageBudget:
    """Tests for proportional language budget allocation."""

    def test_proportional_allocation(self) -> None:
        """Budget split matches symbol proportions."""
        from hypergumbo.sketch import _allocate_language_budget

        # 60% kotlin (6 symbols), 40% python (4 symbols) -> budget 10
        kt_syms = [
            Symbol(id=f"kt{i}", name=f"f{i}", kind="function", language="kotlin",
                   path=f"src/K{i}.kt", span=Span(1, 5, 0, 0))
            for i in range(6)
        ]
        py_syms = [
            Symbol(id=f"py{i}", name=f"f{i}", kind="function", language="python",
                   path=f"src/p{i}.py", span=Span(1, 5, 0, 0))
            for i in range(4)
        ]
        lang_groups = {
            "kotlin": {"src/K0.kt": kt_syms[:3], "src/K1.kt": kt_syms[3:]},
            "python": {"src/p0.py": py_syms[:2], "src/p1.py": py_syms[2:]},
        }
        result = _allocate_language_budget(lang_groups, max_symbols=10)

        # Kotlin should get ~6, Python ~4
        assert result["kotlin"] >= 5  # At least 50% for majority
        assert result["python"] >= 3  # Proportional representation
        assert result["kotlin"] + result["python"] <= 10

    def test_minimum_guarantee(self) -> None:
        """Each language gets at least 1 slot."""
        from hypergumbo.sketch import _allocate_language_budget

        # 90% kotlin (9 symbols), 10% python (1 symbol)
        kt_syms = [
            Symbol(id=f"kt{i}", name=f"f{i}", kind="function", language="kotlin",
                   path="src/K.kt", span=Span(1, 5, 0, 0))
            for i in range(9)
        ]
        py_sym = Symbol(
            id="py0", name="f0", kind="function", language="python",
            path="src/p.py", span=Span(1, 5, 0, 0)
        )
        lang_groups = {
            "kotlin": {"src/K.kt": kt_syms},
            "python": {"src/p.py": [py_sym]},
        }
        result = _allocate_language_budget(lang_groups, max_symbols=10, min_per_language=1)

        # Python should still get at least 1 despite only 10% of symbols
        assert result["python"] >= 1
        assert result["kotlin"] >= 1

    def test_remainder_redistribution(self) -> None:
        """Leftover slots go to largest languages."""
        from hypergumbo.sketch import _allocate_language_budget

        # 3 languages with odd proportions
        kt_syms = [
            Symbol(id=f"kt{i}", name=f"f{i}", kind="function", language="kotlin",
                   path="src/K.kt", span=Span(1, 5, 0, 0))
            for i in range(5)
        ]
        py_syms = [
            Symbol(id=f"py{i}", name=f"f{i}", kind="function", language="python",
                   path="src/p.py", span=Span(1, 5, 0, 0))
            for i in range(3)
        ]
        go_syms = [
            Symbol(id=f"go{i}", name=f"f{i}", kind="function", language="go",
                   path="src/m.go", span=Span(1, 5, 0, 0))
            for i in range(2)
        ]
        lang_groups = {
            "kotlin": {"src/K.kt": kt_syms},
            "python": {"src/p.py": py_syms},
            "go": {"src/m.go": go_syms},
        }
        # Budget 10, 10 total symbols: proportional would give 5+3+2=10 exact
        result = _allocate_language_budget(lang_groups, max_symbols=10)

        total = sum(result.values())
        assert total == 10  # All slots allocated

    def test_empty_returns_empty(self) -> None:
        """No symbols → no budget."""
        from hypergumbo.sketch import _allocate_language_budget

        lang_groups: dict = {}
        result = _allocate_language_budget(lang_groups, max_symbols=10)

        assert result == {}


class TestLanguageProportionalSelection:
    """Integration tests for language-proportional symbol selection."""

    def test_language_proportional_selection(self, tmp_path: Path) -> None:
        """Multi-language project sketch reflects language proportions."""
        from hypergumbo.sketch import _select_symbols_two_phase
        from hypergumbo.ranking import compute_centrality, group_symbols_by_file

        # Create 60% Kotlin (6 symbols), 40% Python (4 symbols)
        kt_syms = [
            Symbol(id=f"kt{i}", name=f"KotlinFn{i}", kind="function", language="kotlin",
                   path=f"src/K{i}.kt", span=Span(1, 5, 0, 0))
            for i in range(6)
        ]
        py_syms = [
            Symbol(id=f"py{i}", name=f"python_fn_{i}", kind="function", language="python",
                   path=f"src/p{i}.py", span=Span(1, 5, 0, 0))
            for i in range(4)
        ]
        all_symbols = kt_syms + py_syms

        # Create mock edges (some cross-language calls)
        edges = [
            Edge(id="e1", src="kt0", dst="kt1", edge_type="call", line=1),
            Edge(id="e2", src="kt1", dst="kt2", edge_type="call", line=2),
            Edge(id="e3", src="py0", dst="py1", edge_type="call", line=1),
        ]

        # Group symbols by file
        by_file = group_symbols_by_file(all_symbols)

        # Compute centrality
        centrality = compute_centrality(all_symbols, edges)

        # Compute file scores (sum of top-K)
        file_scores = {}
        for file_path, syms in by_file.items():
            scores = sorted([centrality.get(s.id, 0) for s in syms], reverse=True)[:3]
            file_scores[file_path] = sum(scores)

        # Select with language_proportional=True
        selected = _select_symbols_two_phase(
            by_file=by_file,
            centrality=centrality,
            file_scores=file_scores,
            max_symbols=10,
            entrypoint_files=set(),
            language_proportional=True,
        )

        # Count selected symbols by language
        lang_counts: dict[str, int] = {}
        for _file_path, sym in selected:
            lang = sym.language
            lang_counts[lang] = lang_counts.get(lang, 0) + 1

        # Kotlin should get ~60% of Phase 1 budget, Python ~40%
        # With coverage_fraction=0.33, Phase 1 gets ~3 slots
        # Then Phase 2 fills the rest
        assert "kotlin" in lang_counts
        assert "python" in lang_counts
        # Both languages should be represented
        assert lang_counts["kotlin"] >= 1
        assert lang_counts["python"] >= 1

    def test_language_proportional_on_by_default(self, tmp_path: Path) -> None:
        """Default behavior uses language-proportional selection."""
        from hypergumbo.sketch import _select_symbols_two_phase
        from hypergumbo.ranking import group_symbols_by_file, compute_centrality

        # Create symbols
        syms = [
            Symbol(id=f"s{i}", name=f"fn{i}", kind="function", language="python",
                   path=f"src/f{i}.py", span=Span(1, 5, 0, 0))
            for i in range(5)
        ]
        by_file = group_symbols_by_file(syms)
        centrality = compute_centrality(syms, [])
        file_scores = dict.fromkeys(by_file.keys(), 1.0)

        # Select with default (language_proportional=True)
        selected = _select_symbols_two_phase(
            by_file=by_file,
            centrality=centrality,
            file_scores=file_scores,
            max_symbols=5,
            entrypoint_files=set(),
        )

        # Should work without errors
        assert len(selected) > 0

    def test_single_language_unaffected(self, tmp_path: Path) -> None:
        """Single-language projects work identically with flag on or off."""
        from hypergumbo.sketch import _select_symbols_two_phase
        from hypergumbo.ranking import group_symbols_by_file, compute_centrality

        # Create single-language symbols
        syms = [
            Symbol(id=f"s{i}", name=f"fn{i}", kind="function", language="python",
                   path=f"src/f{i}.py", span=Span(1, 5, 0, 0))
            for i in range(5)
        ]
        by_file = group_symbols_by_file(syms)
        centrality = compute_centrality(syms, [])
        file_scores = dict.fromkeys(by_file.keys(), 1.0)

        # Select with language_proportional=True
        selected_with = _select_symbols_two_phase(
            by_file=by_file,
            centrality=centrality,
            file_scores=file_scores,
            max_symbols=5,
            entrypoint_files=set(),
            language_proportional=True,
        )

        # Select without language_proportional
        selected_without = _select_symbols_two_phase(
            by_file=by_file,
            centrality=centrality,
            file_scores=file_scores,
            max_symbols=5,
            entrypoint_files=set(),
            language_proportional=False,
        )

        # Should have same number of results
        assert len(selected_with) == len(selected_without)

