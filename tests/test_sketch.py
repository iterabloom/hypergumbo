"""Tests for the sketch module (token-budgeted Markdown output)."""
import pytest
from pathlib import Path

from hypergumbo.sketch import (
    generate_sketch,
    estimate_tokens,
    truncate_to_tokens,
)
from hypergumbo.profile import detect_profile


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
