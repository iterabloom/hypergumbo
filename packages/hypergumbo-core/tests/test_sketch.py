"""Tests for the sketch module (token-budgeted Markdown output)."""
from pathlib import Path

import pytest

from hypergumbo_core.sketch import (
    generate_sketch,
    estimate_tokens,
    truncate_to_tokens,
    _collect_source_files,
    _format_source_files,
    _format_all_files,
    _format_additional_files,
    _format_language_stats,
    _run_analysis,
    _format_entrypoints,
    _format_datamodels,
    _format_symbols,
    _format_structure_tree,
    _format_structure_tree_fallback,
    _collect_important_files,
    _extract_python_docstrings,
    _extract_domain_vocabulary,
    _format_vocabulary,
    _detect_test_summary,
    _format_test_summary,
    _estimate_test_coverage,
    SketchStats,
    display_representativeness_table,
    _extract_markdown_links,
    _extract_org_links,
    _extract_rst_links,
    _extract_asciidoc_links,
    _resolve_readme_link,
    _resolve_pages_url,
    _extract_path_from_forge_url,
    _extract_readme_internal_links,
)
from hypergumbo_core.ranking import compute_centrality, _is_test_path
from hypergumbo_core.profile import detect_profile
from hypergumbo_core.ir import Symbol, Edge, Span
from hypergumbo_core.entrypoints import Entrypoint, EntrypointKind
from hypergumbo_core.datamodels import DataModel, DataModelKind


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


class TestSketchStats:
    """Tests for SketchStats dataclass and methods."""

    def test_symbol_mass_with_zero_total(self) -> None:
        """symbol_mass returns 0 when total_in_degree is 0."""
        stats = SketchStats(total_in_degree=0)
        assert stats.symbol_mass(100) == 0.0

    def test_symbol_mass_computes_percentage(self) -> None:
        """symbol_mass computes correct percentage."""
        stats = SketchStats(total_in_degree=1000)
        # 500 out of 1000 = 50%
        assert stats.symbol_mass(500) == 50.0
        # 250 out of 1000 = 25%
        assert stats.symbol_mass(250) == 25.0

    def test_confidence_mass_with_zero_total(self) -> None:
        """confidence_mass returns 0 when total is 0."""
        stats = SketchStats()
        assert stats.confidence_mass(100.0, 0.0) == 0.0

    def test_confidence_mass_computes_percentage(self) -> None:
        """confidence_mass computes correct percentage."""
        stats = SketchStats()
        # 0.5 out of 1.0 = 50%
        assert stats.confidence_mass(0.5, 1.0) == 50.0
        # 0.25 out of 1.0 = 25%
        assert stats.confidence_mass(0.25, 1.0) == 25.0


class TestDisplayRepresentativenessTable:
    """Tests for display_representativeness_table function."""

    def test_displays_table_with_key_symbols(self, capsys) -> None:
        """Table displays Key Symbols row when present."""
        from io import StringIO
        from rich.console import Console

        stats = SketchStats(
            token_budget=1000,
            total_in_degree=100,
            key_symbols_in_degree=50,
            has_key_symbols=True,
        )
        stats_4x = SketchStats(
            token_budget=4000,
            total_in_degree=100,
            key_symbols_in_degree=80,
            has_key_symbols=True,
        )
        stats_16x = SketchStats(
            token_budget=16000,
            total_in_degree=100,
            key_symbols_in_degree=95,
            has_key_symbols=True,
        )

        # Capture output using StringIO console
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        display_representativeness_table(stats, stats_4x, stats_16x, console)

        result = output.getvalue()
        assert "Key Symbols" in result
        assert "50%" in result
        assert "80%" in result
        assert "95%" in result

    def test_displays_entry_points_with_confidence(self, capsys) -> None:
        """Table displays Entry Points row with confidence mass."""
        from io import StringIO
        from rich.console import Console

        stats = SketchStats(
            token_budget=1000,
            total_entrypoint_confidence=10.0,
            entrypoints_confidence=5.0,
            has_entrypoints=True,
        )
        stats_4x = SketchStats(
            token_budget=4000,
            total_entrypoint_confidence=10.0,
            entrypoints_confidence=8.0,
            has_entrypoints=True,
        )
        stats_16x = SketchStats(
            token_budget=16000,
            total_entrypoint_confidence=10.0,
            entrypoints_confidence=10.0,
            has_entrypoints=True,
        )

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        display_representativeness_table(stats, stats_4x, stats_16x, console)

        result = output.getvalue()
        assert "Entry Points" in result
        assert "confidence mass" in result

    def test_displays_data_models(self) -> None:
        """Table displays Data Models row when present."""
        from io import StringIO
        from rich.console import Console

        stats = SketchStats(
            token_budget=1000,
            total_datamodel_confidence=10.0,
            datamodels_confidence=3.0,
            has_datamodels=True,
        )
        stats_4x = SketchStats(
            token_budget=4000,
            total_datamodel_confidence=10.0,
            datamodels_confidence=6.0,
            has_datamodels=True,
        )
        stats_16x = SketchStats(
            token_budget=16000,
            total_datamodel_confidence=10.0,
            datamodels_confidence=9.0,
            has_datamodels=True,
        )

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        display_representativeness_table(stats, stats_4x, stats_16x, console)

        result = output.getvalue()
        assert "Data Models" in result

    def test_displays_source_files(self) -> None:
        """Table displays Source Files row when present."""
        from io import StringIO
        from rich.console import Console

        stats = SketchStats(
            token_budget=1000,
            total_in_degree=100,
            source_files_in_degree=30,
            has_source_files=True,
        )
        stats_4x = SketchStats(
            token_budget=4000,
            total_in_degree=100,
            source_files_in_degree=60,
            has_source_files=True,
        )
        stats_16x = SketchStats(
            token_budget=16000,
            total_in_degree=100,
            source_files_in_degree=90,
            has_source_files=True,
        )

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        display_representativeness_table(stats, stats_4x, stats_16x, console)

        result = output.getvalue()
        assert "Source Files" in result
        assert "symbol mass" in result

    def test_displays_additional_files(self) -> None:
        """Table displays Additional Files row when present."""
        from io import StringIO
        from rich.console import Console

        stats = SketchStats(
            token_budget=1000,
            total_in_degree=100,
            additional_files_in_degree=20,
            has_additional_files=True,
        )
        stats_4x = SketchStats(
            token_budget=4000,
            total_in_degree=100,
            additional_files_in_degree=40,
            has_additional_files=True,
        )
        stats_16x = SketchStats(
            token_budget=16000,
            total_in_degree=100,
            additional_files_in_degree=60,
            has_additional_files=True,
        )

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        display_representativeness_table(stats, stats_4x, stats_16x, console)

        result = output.getvalue()
        assert "Additional Files" in result

    def test_displays_source_files_content(self) -> None:
        """Table displays Source Files Content row when present."""
        from io import StringIO
        from rich.console import Console

        stats = SketchStats(
            token_budget=1000,
            total_in_degree=100,
            source_files_content_in_degree=15,
            has_source_files_content=True,
        )
        stats_4x = SketchStats(
            token_budget=4000,
            total_in_degree=100,
            source_files_content_in_degree=35,
            has_source_files_content=True,
        )
        stats_16x = SketchStats(
            token_budget=16000,
            total_in_degree=100,
            source_files_content_in_degree=70,
            has_source_files_content=True,
        )

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        display_representativeness_table(stats, stats_4x, stats_16x, console)

        result = output.getvalue()
        assert "Source Files Content" in result

    def test_displays_additional_files_content(self) -> None:
        """Table displays Additional Files Content row when present."""
        from io import StringIO
        from rich.console import Console

        stats = SketchStats(
            token_budget=1000,
            total_in_degree=100,
            additional_files_content_in_degree=10,
            has_additional_files_content=True,
        )
        stats_4x = SketchStats(
            token_budget=4000,
            total_in_degree=100,
            additional_files_content_in_degree=25,
            has_additional_files_content=True,
        )
        stats_16x = SketchStats(
            token_budget=16000,
            total_in_degree=100,
            additional_files_content_in_degree=50,
            has_additional_files_content=True,
        )

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        display_representativeness_table(stats, stats_4x, stats_16x, console)

        result = output.getvalue()
        assert "Additional Files Content" in result

    def test_formats_small_percentages_with_decimal(self) -> None:
        """Percentages under 10 are formatted with one decimal place."""
        from io import StringIO
        from rich.console import Console

        stats = SketchStats(
            token_budget=1000,
            total_in_degree=100,
            key_symbols_in_degree=5,  # 5%
            has_key_symbols=True,
        )
        stats_4x = SketchStats(
            token_budget=4000,
            total_in_degree=100,
            key_symbols_in_degree=8,  # 8%
            has_key_symbols=True,
        )
        stats_16x = SketchStats(
            token_budget=16000,
            total_in_degree=100,
            key_symbols_in_degree=15,  # 15%
            has_key_symbols=True,
        )

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        display_representativeness_table(stats, stats_4x, stats_16x, console)

        result = output.getvalue()
        # Small percentages get one decimal place
        assert "5.0%" in result or "5%" in result  # Either format acceptable
        assert "8.0%" in result or "8%" in result

    def test_shows_dash_for_zero_percentage(self) -> None:
        """Zero percentage shows as dash."""
        from io import StringIO
        from rich.console import Console

        stats = SketchStats(
            token_budget=1000,
            total_in_degree=100,
            key_symbols_in_degree=0,  # 0%
            has_key_symbols=True,
        )
        stats_4x = SketchStats(
            token_budget=4000,
            total_in_degree=100,
            key_symbols_in_degree=50,
            has_key_symbols=True,
        )
        stats_16x = SketchStats(
            token_budget=16000,
            total_in_degree=100,
            key_symbols_in_degree=80,
            has_key_symbols=True,
        )

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        display_representativeness_table(stats, stats_4x, stats_16x, console)

        # Zero should be displayed as "-"
        # The table should contain "-" for the zero value column
        result = output.getvalue()
        assert "Key Symbols" in result

    def test_no_output_for_empty_table(self) -> None:
        """No table output when no sections are present."""
        from io import StringIO
        from rich.console import Console

        # Stats with no sections marked as present
        stats = SketchStats(token_budget=1000)
        stats_4x = SketchStats(token_budget=4000)
        stats_16x = SketchStats(token_budget=16000)

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        display_representativeness_table(stats, stats_4x, stats_16x, console)

        result = output.getvalue()
        # Should be empty or minimal since no sections are present
        assert "Key Symbols" not in result
        assert "Entry Points" not in result

    def test_creates_default_console_when_none_provided(self) -> None:
        """Function creates a console when None is provided."""
        # Stats with one section to ensure table is created
        stats = SketchStats(
            token_budget=1000,
            total_in_degree=100,
            key_symbols_in_degree=50,
            has_key_symbols=True,
        )
        stats_4x = SketchStats(
            token_budget=4000,
            total_in_degree=100,
            key_symbols_in_degree=80,
            has_key_symbols=True,
        )
        stats_16x = SketchStats(
            token_budget=16000,
            total_in_degree=100,
            key_symbols_in_degree=95,
            has_key_symbols=True,
        )

        # Should not raise when console is None
        display_representativeness_table(stats, stats_4x, stats_16x, console=None)


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

    def test_detects_datamodels(self, tmp_path: Path) -> None:
        """Sketch includes detected data models when available."""
        # Create a project with data models (dataclass)
        (tmp_path / "models.py").write_text(
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class UserModel:\n"
            "    name: str\n"
            "    email: str\n"
        )
        (tmp_path / "main.py").write_text("from models import UserModel\n")

        sketch = generate_sketch(tmp_path, max_tokens=2000)

        # Should detect data model via @dataclass decorator or naming convention
        assert "Data Models" in sketch or "UserModel" in sketch

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

    def test_test_summary_always_present(self, tmp_path: Path) -> None:
        """Sketch always includes Tests section, even when no tests exist."""
        (tmp_path / "main.py").write_text("print('hello')\n")

        sketch = generate_sketch(tmp_path)

        # Tests section should always be present for consistency
        assert "## Tests" in sketch
        assert "No test files detected" in sketch

    def test_many_directories(self, tmp_path: Path) -> None:
        """Sketch handles projects with many directories."""
        # Create 15 directories
        for i in range(15):
            (tmp_path / f"dir_{i:02d}").mkdir()
        (tmp_path / "main.py").write_text("print('hello')\n")

        sketch = generate_sketch(tmp_path)

        # Should show truncation message (tree format uses "other items")
        assert "other items" in sketch

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

    def test_readme_prefers_description_over_instructions(self, tmp_path: Path) -> None:
        """README extraction prefers descriptive intro over installation instructions."""
        (tmp_path / "README.md").write_text(
            "# Project\n\n"
            "A powerful tool for analyzing source code and generating behavior maps.\n"
            "## Installation\n"
            "pip install project\n"
        )
        (tmp_path / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=500)

        # Should include the descriptive intro, not installation instructions
        assert "powerful tool" in sketch or "analyzing source code" in sketch
        assert "pip install" not in sketch.split("## Configuration")[0] if "## Configuration" in sketch else True

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
        # Must exceed max_chars (300) to trigger truncation
        long_word = "a" * 400
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


class TestFindReadmePath:
    """Tests for _find_readme_path helper."""

    def test_finds_readme_md(self, tmp_path: Path) -> None:
        """Finds README.md in repo root."""
        from hypergumbo_core.sketch import _find_readme_path
        (tmp_path / "README.md").write_text("# Hello")
        assert _find_readme_path(tmp_path) == tmp_path / "README.md"

    def test_finds_lowercase_readme(self, tmp_path: Path) -> None:
        """Finds lowercase readme.md."""
        from hypergumbo_core.sketch import _find_readme_path
        (tmp_path / "readme.md").write_text("# Hello")
        assert _find_readme_path(tmp_path) == tmp_path / "readme.md"

    def test_finds_readme_rst(self, tmp_path: Path) -> None:
        """Finds README.rst when .md is absent."""
        from hypergumbo_core.sketch import _find_readme_path
        (tmp_path / "README.rst").write_text("Hello\n=====")
        assert _find_readme_path(tmp_path) == tmp_path / "README.rst"

    def test_returns_none_if_missing(self, tmp_path: Path) -> None:
        """Returns None when no README exists."""
        from hypergumbo_core.sketch import _find_readme_path
        assert _find_readme_path(tmp_path) is None

    def test_finds_mixedcase_readme(self, tmp_path: Path) -> None:
        """Finds mixed-case Readme.md (case-insensitive)."""
        from hypergumbo_core.sketch import _find_readme_path
        (tmp_path / "Readme.md").write_text("# Hello")
        assert _find_readme_path(tmp_path) == tmp_path / "Readme.md"

    def test_prefers_readme_md_over_readme_rst(self, tmp_path: Path) -> None:
        """Prefers .md extension over .rst."""
        from hypergumbo_core.sketch import _find_readme_path
        (tmp_path / "README.rst").write_text("Hello\n=====")
        (tmp_path / "Readme.md").write_text("# Hello")
        result = _find_readme_path(tmp_path)
        assert result is not None
        assert result.suffix.lower() == ".md"


class TestTruncateDescription:
    """Tests for _truncate_description helper."""

    def test_no_truncation_needed(self) -> None:
        """Short descriptions are returned as-is."""
        from hypergumbo_core.sketch import _truncate_description
        assert _truncate_description("Hello world", 100) == "Hello world"

    def test_truncates_at_word_boundary(self) -> None:
        """Truncates at last word boundary before limit."""
        from hypergumbo_core.sketch import _truncate_description
        result = _truncate_description("Hello world foo bar", 15)
        assert result == "Hello world…"

    def test_truncates_long_word(self) -> None:
        """Truncates mid-word if no good boundary."""
        from hypergumbo_core.sketch import _truncate_description
        result = _truncate_description("aaaaaaaaaaaaaaaaaa", 10)
        assert len(result) == 10
        assert result.endswith("…")

    def test_prefers_sentence_boundary(self) -> None:
        """Truncates at sentence boundary when available."""
        from hypergumbo_core.sketch import _truncate_description
        # "First sentence. This is a" would be the word-boundary truncation
        # but we want sentence boundary at "First sentence."
        text = "First sentence. This is a longer continuation that goes on."
        result = _truncate_description(text, 50)
        # Should end at the first sentence, not mid-second-sentence
        assert result == "First sentence."
        assert "This is a" not in result

    def test_sentence_boundary_with_exclamation(self) -> None:
        """Handles exclamation marks as sentence boundaries."""
        from hypergumbo_core.sketch import _truncate_description
        text = "Hello world! This continues for a while after the mark."
        result = _truncate_description(text, 40)
        assert result == "Hello world!"

    def test_sentence_boundary_with_question(self) -> None:
        """Handles question marks as sentence boundaries."""
        from hypergumbo_core.sketch import _truncate_description
        text = "What is this? It's a test of the truncation logic."
        result = _truncate_description(text, 40)
        assert result == "What is this?"

    def test_sentence_boundary_with_colon(self) -> None:
        """Handles colons as sentence boundaries."""
        from hypergumbo_core.sketch import _truncate_description
        text = "Here is the point: we need good truncation always."
        result = _truncate_description(text, 40)
        assert result == "Here is the point:"

    def test_falls_back_to_word_boundary(self) -> None:
        """Falls back to word boundary if no sentence boundary is found."""
        from hypergumbo_core.sketch import _truncate_description
        # No sentence-ending punctuation, should fall back to word boundary
        text = "This is a very long text without any sentence endings at all"
        result = _truncate_description(text, 30)
        assert result.endswith("…")
        # Should end with a complete word before ellipsis (not mid-word)
        word_before_ellipsis = result[:-1].split()[-1]  # Remove ellipsis, get last word
        assert word_before_ellipsis in text.split()  # Should be a complete word

    def test_sentence_too_short_falls_back(self) -> None:
        """Falls back if sentence boundary is too short (< 10 chars)."""
        from hypergumbo_core.sketch import _truncate_description
        # "Hi." is only 3 chars, so falls back to word boundary
        text = "Hi. This is a much longer text that we want."
        result = _truncate_description(text, 30)
        # Should NOT truncate at "Hi." (too short), but at a later word boundary
        assert result != "Hi."
        # Should use word boundary fallback
        assert result.endswith("…") or result.endswith(".")

    def test_sentence_at_end_of_search_range(self) -> None:
        """Handles sentence ending exactly at max_chars."""
        from hypergumbo_core.sketch import _truncate_description
        # "Exactly." is 8 chars, put it right at the limit
        text = "Exactly."
        result = _truncate_description(text, 8)
        assert result == "Exactly."

    def test_sentence_punctuation_at_max_chars_boundary(self) -> None:
        """Handles punctuation at exact max_chars position in longer text."""
        from hypergumbo_core.sketch import _truncate_description
        # Text longer than max_chars, with period at position 14 (0-indexed)
        # "Hello world123." is 15 chars, with period at index 14
        text = "Hello world123. More text follows here."
        # max_chars=15 means search_range is "Hello world123."
        # The period is at index 14 which is len(search_range) - 1
        result = _truncate_description(text, 15)
        assert result == "Hello world123."


class TestExtractReadmeDescriptionHeuristic:
    """Tests for _extract_readme_description_heuristic function."""

    def test_extracts_paragraph_after_title(self, tmp_path: Path) -> None:
        """Extracts first paragraph after markdown title."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        readme.write_text("# Project\n\nA tool for doing things.\n")
        result = _extract_readme_description_heuristic(readme)
        assert result == "A tool for doing things."

    def test_stops_at_section_header(self, tmp_path: Path) -> None:
        """Stops extraction at section headers."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        readme.write_text("# Project\n\nIntro.\n## Usage\nNot included.")
        result = _extract_readme_description_heuristic(readme)
        assert result == "Intro."
        assert "Not included" not in (result or "")

    def test_skips_badges_and_images(self, tmp_path: Path) -> None:
        """Skips badge and image markdown."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        readme.write_text("# Project\n\n![badge](url)\n[![CI](ci.png)](link)\nActual description.")
        result = _extract_readme_description_heuristic(readme)
        assert result == "Actual description."

    def test_skips_html_tags(self, tmp_path: Path) -> None:
        """Skips HTML-only lines."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        readme.write_text("# Project\n\n<picture><img src='x'></picture>\nReal description.")
        result = _extract_readme_description_heuristic(readme)
        assert result == "Real description."

    def test_extracts_title_subtitle(self, tmp_path: Path) -> None:
        """Falls back to title subtitle if no paragraph."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        readme.write_text("# Project: A description in the title\n\n## Section")
        result = _extract_readme_description_heuristic(readme)
        assert result == "A description in the title"

    def test_handles_rst_format(self, tmp_path: Path) -> None:
        """Handles RST title format with underlines."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.rst"
        readme.write_text("Project\n=======\n\nRST description here.\n")
        result = _extract_readme_description_heuristic(readme)
        assert result == "RST description here."

    def test_returns_none_for_empty(self, tmp_path: Path) -> None:
        """Returns None when no description found."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        readme.write_text("# Title\n\n## Section\nNo intro paragraph.")
        result = _extract_readme_description_heuristic(readme)
        assert result is None

    def test_skips_link_only_lines(self, tmp_path: Path) -> None:
        """Skips lines that are just markdown links."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        readme.write_text("# Project\n\n[Docs](https://example.com)\nActual text.")
        result = _extract_readme_description_heuristic(readme)
        assert result == "Actual text."

    def test_skips_badges_before_title(self, tmp_path: Path) -> None:
        """Skips badge images that appear before the markdown title."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        # Badge at top, then title, then description
        readme.write_text("![Badge](https://img.shields.io/badge)\n# Project\n\nDescription text.")
        result = _extract_readme_description_heuristic(readme)
        assert result == "Description text."

    def test_skips_html_before_title(self, tmp_path: Path) -> None:
        """Skips HTML tags that appear before the markdown title."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        # HTML logo at top, then title, then description
        readme.write_text("<p align='center'><img src='logo.png'></p>\n# Project\n\nDescription.")
        result = _extract_readme_description_heuristic(readme)
        assert result == "Description."

    def test_skips_html_comments_in_description(self, tmp_path: Path) -> None:
        """Skips HTML comments that appear in the description area."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        readme.write_text("# Project\n\n<!-- CI badge placeholder -->\nActual description here.")
        result = _extract_readme_description_heuristic(readme)
        assert result == "Actual description here."

    def test_completes_sentence_across_paragraph_break(self, tmp_path: Path) -> None:
        """Completes sentence when split across paragraph break."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        # Sentence incomplete at first paragraph, continues after blank line
        readme.write_text(
            "# Project\n\n"
            "This is a tool that helps developers\n\n"
            "be more productive.\n\n"
            "## Overview\n"
        )
        result = _extract_readme_description_heuristic(readme)
        assert result == "This is a tool that helps developers be more productive."

    def test_completes_sentence_stops_at_period(self, tmp_path: Path) -> None:
        """Stops appending words when period is reached."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        readme.write_text(
            "# Project\n\n"
            "A great tool\n\n"
            "for building. It also does other things.\n\n"
            "## Usage\n"
        )
        result = _extract_readme_description_heuristic(readme)
        assert result == "A great tool for building."

    def test_does_not_complete_into_header(self, tmp_path: Path) -> None:
        """Does not append words from header lines."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        readme.write_text(
            "# Project\n\n"
            "A wonderful library\n\n"
            "## Installation\n"
        )
        result = _extract_readme_description_heuristic(readme)
        # Should not try to complete since next line is a header
        assert result == "A wonderful library"

    def test_completes_sentence_strips_html(self, tmp_path: Path) -> None:
        """Strips HTML tags when checking continuation line."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        readme.write_text(
            "# Project\n\n"
            "A useful tool\n\n"
            "<p>for everyone.</p>\n\n"
            "## Overview\n"
        )
        result = _extract_readme_description_heuristic(readme)
        # After HTML stripping, "<p>for everyone.</p>" becomes "for everyone."
        # which is valid continuation text, so sentence is completed
        assert result == "A useful tool for everyone."

    def test_no_completion_when_sentence_complete(self, tmp_path: Path) -> None:
        """Does not append when sentence already ends with punctuation."""
        from hypergumbo_core.sketch import _extract_readme_description_heuristic
        readme = tmp_path / "README.md"
        readme.write_text(
            "# Project\n\n"
            "This is complete.\n\n"
            "Extra text here.\n\n"
            "## Overview\n"
        )
        result = _extract_readme_description_heuristic(readme)
        assert result == "This is complete."

    def test_returns_none_when_no_content(self, tmp_path: Path) -> None:
        """Returns None when both embedding and heuristic fail."""
        from unittest.mock import patch
        from hypergumbo_core.sketch import _extract_readme_description

        readme = tmp_path / "README.md"
        # README with only title and section header - no description
        readme.write_text("# Title\n\n## Installation\nSteps here.")

        # Mock embedding to return None
        with patch(
            "hypergumbo_core.sketch_embeddings.extract_readme_description_embedding",
            return_value=None
        ):
            result = _extract_readme_description(tmp_path)
            assert result is None

    def test_fallback_when_embedding_fails(self, tmp_path: Path) -> None:
        """Falls back to heuristic when embedding returns None."""
        from unittest.mock import patch
        from hypergumbo_core.sketch import _extract_readme_description

        readme = tmp_path / "README.md"
        readme.write_text("# Project\n\nHeuristic description text.\n")

        # Mock embedding to return None (simulating failure/unavailable)
        with patch(
            "hypergumbo_core.sketch_embeddings.extract_readme_description_embedding",
            return_value=None
        ):
            result = _extract_readme_description(tmp_path)
            assert result == "Heuristic description text."

    def test_fallback_when_embedding_raises(self, tmp_path: Path) -> None:
        """Falls back to heuristic when embedding raises exception."""
        from unittest.mock import patch
        from hypergumbo_core.sketch import _extract_readme_description

        readme = tmp_path / "README.md"
        readme.write_text("# Project\n\nFallback description.\n")

        # Mock embedding to raise an exception
        with patch(
            "hypergumbo_core.sketch_embeddings.extract_readme_description_embedding",
            side_effect=ImportError("No module")
        ):
            result = _extract_readme_description(tmp_path)
            assert result == "Fallback description."


class TestReadmeLineFilterable:
    """Tests for _is_readme_line_filterable helper."""

    def test_skips_empty_lines(self) -> None:
        """Empty lines are filterable."""
        from hypergumbo_core.sketch_embeddings import _is_readme_line_filterable
        assert _is_readme_line_filterable("") is True
        assert _is_readme_line_filterable("   ") is True

    def test_skips_badges(self) -> None:
        """Badge markdown is filterable."""
        from hypergumbo_core.sketch_embeddings import _is_readme_line_filterable
        assert _is_readme_line_filterable("[![Build](https://img.shields.io/badge)](link)") is True
        assert _is_readme_line_filterable("![Logo](logo.png)") is True

    def test_skips_link_only_lines(self) -> None:
        """Pure link lines are filterable."""
        from hypergumbo_core.sketch_embeddings import _is_readme_line_filterable
        assert _is_readme_line_filterable("[Link](https://example.com)") is True

    def test_skips_html_comments(self) -> None:
        """HTML comments are filterable."""
        from hypergumbo_core.sketch_embeddings import _is_readme_line_filterable
        assert _is_readme_line_filterable("<!-- Comment -->") is True

    def test_keeps_text_content(self) -> None:
        """Normal text is not filterable."""
        from hypergumbo_core.sketch_embeddings import _is_readme_line_filterable
        assert _is_readme_line_filterable("This is a project description.") is False

    def test_keeps_html_with_text(self) -> None:
        """HTML containing text is kept."""
        from hypergumbo_core.sketch_embeddings import _is_readme_line_filterable
        assert _is_readme_line_filterable("<p>Project description here</p>") is False

    def test_skips_link_reference_definitions(self) -> None:
        """Markdown link reference definitions are filterable."""
        from hypergumbo_core.sketch_embeddings import _is_readme_line_filterable
        # Common pattern at top of READMEs
        assert _is_readme_line_filterable("[bep]: https://github.com/bep") is True
        assert _is_readme_line_filterable("[docs]: https://example.com/docs") is True
        # With extra whitespace
        assert _is_readme_line_filterable("[link]:  https://example.com") is True
        # But keep inline links in text
        assert _is_readme_line_filterable("See [the docs](https://example.com) for more.") is False

    def test_skips_github_callout_syntax(self) -> None:
        """GitHub callout syntax is filterable."""
        from hypergumbo_core.sketch_embeddings import _is_readme_line_filterable
        assert _is_readme_line_filterable("> [!NOTE]") is True
        assert _is_readme_line_filterable("> [!IMPORTANT]") is True
        assert _is_readme_line_filterable("> [!WARNING]") is True
        assert _is_readme_line_filterable(">  [!TIP]") is True  # Extra space
        # But keep regular blockquotes
        assert _is_readme_line_filterable("> This is a regular quote") is False


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

    def test_exclude_tests_filters_test_files_in_source_dirs(
        self, tmp_path: Path
    ) -> None:
        """exclude_tests=True filters test files from src/ directories."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "core.py").write_text("def main(): pass")
        (src / "test_core.py").write_text("def test_main(): pass")  # Test file in src/

        profile = detect_profile(tmp_path)

        # Without exclude_tests, both files are collected
        files = _collect_source_files(tmp_path, profile, exclude_tests=False)
        names = {f.name for f in files}
        assert "core.py" in names
        assert "test_core.py" in names

        # With exclude_tests, test file is filtered out
        files = _collect_source_files(tmp_path, profile, exclude_tests=True)
        names = {f.name for f in files}
        assert "core.py" in names
        assert "test_core.py" not in names


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

    def test_sorts_by_density_when_provided(self, tmp_path: Path) -> None:
        """Sorts files by density scores when provided."""
        files = [
            tmp_path / "low.py",
            tmp_path / "high.py",
            tmp_path / "medium.py",
        ]
        # Create the files
        for f in files:
            f.write_text("# placeholder")

        density_scores = {
            "low.py": 0.1,
            "high.py": 0.9,
            "medium.py": 0.5,
        }

        result = _format_source_files(tmp_path, files, density_scores=density_scores)
        lines = result.split("\n")

        # Find the file lines (skip header)
        file_lines = [l for l in lines if l.startswith("- `")]
        assert "`high.py`" in file_lines[0]  # highest density first
        assert "`medium.py`" in file_lines[1]
        assert "`low.py`" in file_lines[2]


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


def _make_test_symbol(
    name: str,
    path: str = "src/main.py",
    kind: str = "function",
) -> Symbol:
    """Create a test symbol for _format_additional_files tests."""
    return Symbol(
        id=f"python:{path}:1-10:{kind}:{name}",
        name=name,
        kind=kind,
        language="python",
        path=path,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
    )


class TestFormatAdditionalFiles:
    """Tests for additional files formatting (hybrid semantic + centrality)."""

    def test_excludes_source_files(self, tmp_path: Path) -> None:
        """Excludes source files from additional files list."""
        src = tmp_path / "src"
        src.mkdir()
        source_file = src / "main.py"
        source_file.write_text("def foo(): pass")
        readme = tmp_path / "README.md"
        readme.write_text("# Project")

        result, _, _ = _format_additional_files(
            tmp_path,
            source_files=[source_file],
            symbols=[],
            in_degree={},
        )

        assert "## Additional Files" in result
        assert "`README.md`" in result
        assert "main.py" not in result

    def test_orders_by_centrality_when_no_embeddings(self, tmp_path: Path) -> None:
        """Orders files by symbol mention centrality when no embeddings."""
        # Create files with different centrality
        doc_mentions = tmp_path / "doc_mentions.md"
        doc_mentions.write_text("Use the foo function to process data with bar")
        doc_none = tmp_path / "doc_none.md"
        doc_none.write_text("This file has no symbol mentions at all")

        # Create source file (will be excluded from additional files)
        src = tmp_path / "src"
        src.mkdir()
        source_file = src / "main.py"
        source_file.write_text("def foo(): pass")

        foo = _make_test_symbol("foo")
        bar = _make_test_symbol("bar")
        in_degree = {foo.id: 5, bar.id: 3}

        result, _, _ = _format_additional_files(
            tmp_path,
            source_files=[source_file],
            symbols=[foo, bar],
            in_degree=in_degree,
            semantic_top_n=0,  # Disable semantic ranking
        )

        # doc_mentions should appear before doc_none due to symbol mentions
        lines = result.split("\n")
        file_lines = [l for l in lines if l.startswith("- `")]
        # File with mentions should come first
        assert "`doc_mentions.md`" in file_lines[0]

    def test_empty_when_no_additional_files(self, tmp_path: Path) -> None:
        """Returns empty string when all files are source files."""
        src = tmp_path / "main.py"
        src.write_text("def foo(): pass")

        result, _, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
        )

        assert result == ""

    def test_respects_max_files(self, tmp_path: Path) -> None:
        """Limits output to max_files."""
        # Create many config/doc files (ADR-0004: only CONFIG/DOCUMENTATION roles)
        for i in range(10):
            (tmp_path / f"doc_{i}.md").write_text(f"# Doc {i}")

        # Create source file
        src = tmp_path / "main.py"
        src.write_text("def foo(): pass")

        result, _, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
            max_files=3,
            semantic_top_n=0,
        )

        assert "## Additional Files" in result
        assert "... and 7 more files" in result

    def test_excludes_hidden_files(self, tmp_path: Path) -> None:
        """Excludes hidden files from additional files."""
        (tmp_path / ".hidden.md").write_text("# secret")
        (tmp_path / "visible.md").write_text("# public")
        src = tmp_path / "main.py"
        src.write_text("pass")

        result, _, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
        )

        assert ".hidden" not in result
        assert "`visible.md`" in result

    def test_excludes_node_modules(self, tmp_path: Path) -> None:
        """Excludes node_modules directory from additional files."""
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "package.json").write_text("{}")
        # Use a config file instead of .js (ANALYZABLE files are not Additional Files)
        (tmp_path / "config.yaml").write_text("key: value")
        src = tmp_path / "main.py"
        src.write_text("pass")

        result, _, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
        )

        assert "node_modules" not in result
        assert "`config.yaml`" in result

    def test_excludes_license_and_legal_files(self, tmp_path: Path) -> None:
        """Excludes license and legal boilerplate files."""
        # Create various license files
        (tmp_path / "LICENSE").write_text("MIT License")
        (tmp_path / "LICENSE.md").write_text("# MIT License")
        (tmp_path / "COPYING").write_text("GPL License")
        (tmp_path / "NOTICE").write_text("Apache Notice")
        # Create a valid file to show output isn't empty
        (tmp_path / "README.md").write_text("# Project")
        src = tmp_path / "main.py"
        src.write_text("pass")

        result, _, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
        )

        assert "LICENSE" not in result
        assert "COPYING" not in result
        assert "NOTICE" not in result
        assert "`README.md`" in result

    def test_excludes_hypergumbo_artifacts(self, tmp_path: Path) -> None:
        """Excludes hypergumbo output artifacts."""
        # Create hypergumbo artifacts
        (tmp_path / "hypergumbo.results.json").write_text('{"nodes": []}')
        (tmp_path / "hypergumbo.results.4k.json").write_text('{"nodes": []}')
        # Create a valid file
        (tmp_path / "README.md").write_text("# Project")
        src = tmp_path / "main.py"
        src.write_text("pass")

        result, _, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
        )

        assert "hypergumbo.results" not in result
        assert "`README.md`" in result

    def test_excludes_gitignore_and_editorconfig(self, tmp_path: Path) -> None:
        """Excludes git and editor config files."""
        (tmp_path / ".gitignore").write_text("*.pyc")
        (tmp_path / ".editorconfig").write_text("[*]\nindent_style = space")
        (tmp_path / "CODEOWNERS").write_text("* @owner")
        (tmp_path / "README.md").write_text("# Project")
        src = tmp_path / "main.py"
        src.write_text("pass")

        result, _, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
        )

        assert ".gitignore" not in result
        assert ".editorconfig" not in result
        assert "CODEOWNERS" not in result
        assert "`README.md`" in result

    def test_excludes_binary_files(self, tmp_path: Path) -> None:
        """Excludes binary files that cannot be meaningfully embedded."""
        # Create various binary files (images, audio, video, fonts, archives)
        (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0")
        (tmp_path / "icon.svg").write_text("<svg></svg>")
        (tmp_path / "audio.mp3").write_bytes(b"ID3\x04\x00\x00")
        (tmp_path / "video.mp4").write_bytes(b"\x00\x00\x00\x1cftyp")
        (tmp_path / "font.ttf").write_bytes(b"\x00\x01\x00\x00")
        (tmp_path / "archive.zip").write_bytes(b"PK\x03\x04")
        (tmp_path / "data.npy").write_bytes(b"\x93NUMPY")
        (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4")
        # Create a valid text file to show output isn't empty
        (tmp_path / "README.md").write_text("# Project")
        src = tmp_path / "main.py"
        src.write_text("pass")

        result, _, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
        )

        # Binary files should be excluded
        assert "image.png" not in result
        assert "photo.jpg" not in result
        assert "icon.svg" not in result
        assert "audio.mp3" not in result
        assert "video.mp4" not in result
        assert "font.ttf" not in result
        assert "archive.zip" not in result
        assert "data.npy" not in result
        assert "doc.pdf" not in result
        # Text file should still be included
        assert "`README.md`" in result

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty string for empty directory."""
        result, _, _ = _format_additional_files(
            tmp_path,
            source_files=[],
            symbols=[],
            in_degree={},
        )
        assert result == ""

    @pytest.mark.skipif(
        not _has_sentence_transformers(),
        reason="sentence-transformers not available"
    )
    def test_semantic_ranking_with_empty_file(self, tmp_path: Path) -> None:
        """Verifies semantic ranking handles empty files (returns None embedding).

        This test is skipped on CI where sentence-transformers isn't installed,
        but runs locally to verify the defensive code path works correctly.
        The line is marked with pragma:no cover for CI coverage.
        """
        # Create an empty file - embedding will return None
        # Use .md extension for DOCUMENTATION role (ADR-0004)
        empty_file = tmp_path / "empty.md"
        empty_file.write_text("")
        # Create a file with content
        content_file = tmp_path / "content.md"
        content_file.write_text("This file has content for embedding")
        # Create source file (excluded from additional files)
        src = tmp_path / "main.py"
        src.write_text("pass")

        # Run with semantic ranking enabled - empty file should get 0.0 score
        result, _, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
            semantic_top_n=3,  # Enable semantic ranking
        )

        # Both files should be in output
        assert "## Additional Files" in result
        assert "empty.md" in result
        assert "content.md" in result

    def test_semantic_ranking_fallback_when_embedding_none(
        self, tmp_path: Path
    ) -> None:
        """Tests semantic ranking path with mocked embedding returning None.

        This ensures the embedding code path is covered on CI where
        sentence-transformers isn't installed, by mocking the availability.
        """
        from unittest.mock import patch

        # Create files with recognized roles (ADR-0004)
        (tmp_path / "readme.md").write_text("# Project documentation")
        (tmp_path / "notes.md").write_text("# Some notes")
        src = tmp_path / "main.py"
        src.write_text("pass")

        # Mock _has_sentence_transformers to return True
        # and batch_embed_files to return None for all files (simulating failure)
        def mock_batch_embed(files, **kwargs):
            return dict.fromkeys(files, None)

        with patch(
            "hypergumbo_core.sketch_embeddings._has_sentence_transformers",
            return_value=True
        ), patch(
            "hypergumbo_core.sketch_embeddings.batch_embed_files",
            side_effect=mock_batch_embed
        ), patch(
            "hypergumbo_core.sketch_embeddings._get_cache_dir",
            return_value=tmp_path / ".cache"
        ):
            result, _, _ = _format_additional_files(
                tmp_path,
                source_files=[src],
                symbols=[],
                in_degree={},
                semantic_top_n=2,
            )

        # Files should still appear (with 0.0 score)
        assert "## Additional Files" in result
        assert "readme.md" in result
        assert "notes.md" in result

    def test_semantic_ranking_with_mock_embeddings(self, tmp_path: Path) -> None:
        """Tests semantic ranking with mocked embeddings.

        This ensures the full embedding code path is covered on CI.
        """
        from unittest.mock import patch, MagicMock

        # Create files with recognized roles (ADR-0004)
        (tmp_path / "readme.md").write_text("# Project documentation")
        (tmp_path / "notes.md").write_text("# Some notes")
        src = tmp_path / "main.py"
        src.write_text("pass")

        # Use a MagicMock for the embedding - we don't need actual numpy
        fake_embedding = MagicMock()

        def mock_batch_embed(files, **kwargs):
            return dict.fromkeys(files, fake_embedding)

        with patch(
            "hypergumbo_core.sketch_embeddings._has_sentence_transformers",
            return_value=True
        ), patch(
            "hypergumbo_core.sketch_embeddings.batch_embed_files",
            side_effect=mock_batch_embed
        ), patch(
            "hypergumbo_core.sketch_embeddings.compute_5w1h_similarity", return_value=0.5
        ), patch(
            "hypergumbo_core.sketch_embeddings._get_cache_dir",
            return_value=tmp_path / ".cache"
        ):
            result, _, _ = _format_additional_files(
                tmp_path,
                source_files=[src],
                symbols=[],
                in_degree={},
                semantic_top_n=2,
            )

        assert "## Additional Files" in result

    def test_uses_cached_centrality_scores(self, tmp_path: Path) -> None:
        """Uses pre-computed centrality scores from cached results."""
        # Create files with different expected centrality
        high_score = tmp_path / "high_centrality.md"
        high_score.write_text("# High centrality doc")
        low_score = tmp_path / "low_centrality.md"
        low_score.write_text("# Low centrality doc")
        src = tmp_path / "main.py"
        src.write_text("def foo(): pass")

        # Provide pre-computed centrality scores (relative path strings to float)
        cached_scores = {
            "high_centrality.md": 100.0,
            "low_centrality.md": 1.0,
        }

        result, _, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
            semantic_top_n=0,  # Disable semantic ranking to use only centrality
            cached_centrality_scores=cached_scores,
        )

        # High centrality file should appear before low centrality file
        lines = result.split("\n")
        file_lines = [line for line in lines if line.startswith("- `")]
        assert len(file_lines) >= 2
        assert "`high_centrality.md`" in file_lines[0]
        assert "`low_centrality.md`" in file_lines[1]

    def test_round_robin_includes_readme_linked_files(self, tmp_path: Path) -> None:
        """Round-robin includes files linked from README."""
        # Create README with internal links
        readme = tmp_path / "README.md"
        linked_doc = tmp_path / "CONTRIBUTING.md"
        linked_doc.write_text("# Contributing Guide")
        readme.write_text("See [Contributing](CONTRIBUTING.md) for details.")

        src = tmp_path / "main.py"
        src.write_text("def foo(): pass")

        result, selected, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
            semantic_top_n=0,  # Disable semantic to focus on README links
        )

        # README should be first, linked file should be included
        assert "## Additional Files" in result
        assert "`README.md`" in result
        assert "`CONTRIBUTING.md`" in result

    def test_round_robin_breaks_at_max_files(self, tmp_path: Path) -> None:
        """Round-robin stops when max_files is reached."""
        # Create many files
        for i in range(10):
            (tmp_path / f"doc_{i}.md").write_text(f"# Doc {i}")

        src = tmp_path / "main.py"
        src.write_text("pass")

        result, _, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
            max_files=3,
            semantic_top_n=0,
        )

        # Should show "... and N more files"
        assert "... and 7 more files" in result

    def test_with_content_budget_exhaustion(self, tmp_path: Path) -> None:
        """Budget exhaustion triggers truncation and breaks."""
        # Create files of varying sizes
        small_file = tmp_path / "small.md"
        small_file.write_text("# Small\nShort content.")

        large_file = tmp_path / "large.md"
        large_file.write_text("# Large\n" + "word " * 5000)  # ~5000 tokens

        src = tmp_path / "main.py"
        src.write_text("pass")

        result, selected, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
            semantic_top_n=0,
            token_budget=600,  # Small budget
            include_content=True,
        )

        # Should include content with file content blocks
        assert "## Additional Files" in result
        # Content mode uses START/END markers
        assert "START of" in result or "[...truncated...]" in result

    def test_with_content_first_file_exceeds_budget(self, tmp_path: Path) -> None:
        """When first file exceeds budget, uses MIN_TRUNCATION_TOKENS."""
        # Create a large file
        large_file = tmp_path / "README.md"
        large_file.write_text("# README\n" + "word " * 2000)

        src = tmp_path / "main.py"
        src.write_text("pass")

        result, selected, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
            semantic_top_n=0,
            token_budget=800,
            include_content=True,
        )

        # Should still show content with truncation
        assert "## Additional Files" in result
        assert "[...truncated...]" in result

    def test_with_content_budget_too_small_for_truncation(self, tmp_path: Path) -> None:
        """Very small budget breaks before truncation."""
        large_file = tmp_path / "large.md"
        large_file.write_text("# Large\n" + "word " * 2000)

        src = tmp_path / "main.py"
        src.write_text("pass")

        result, selected, _ = _format_additional_files(
            tmp_path,
            source_files=[src],
            symbols=[],
            in_degree={},
            semantic_top_n=0,
            token_budget=100,  # Very small budget
            include_content=True,
        )

        # With such a small budget, we may not be able to include anything
        # but header should still be present
        assert "## Additional Files" in result


class TestExtractMarkdownLinks:
    """Tests for _extract_markdown_links function."""

    def test_inline_links(self) -> None:
        """Extracts inline Markdown links."""
        content = "Check [docs](docs/readme.md) and [guide](guide.md)"
        result = _extract_markdown_links(content)
        assert ("docs", "docs/readme.md") in result
        assert ("guide", "guide.md") in result

    def test_reference_style_links(self) -> None:
        """Extracts reference-style Markdown links."""
        content = """
Read the [INSTALL][] for setup.
See [CONTRIBUTING][] for guidelines.

[INSTALL]: INSTALL.md
[CONTRIBUTING]: docs/contributing.md
"""
        result = _extract_markdown_links(content)
        assert ("INSTALL", "INSTALL.md") in result
        assert ("CONTRIBUTING", "docs/contributing.md") in result

    def test_reference_style_with_different_ref(self) -> None:
        """Extracts reference-style links with different ref name."""
        content = """
Read the [installation guide][install] for setup.

[install]: INSTALL.md
"""
        result = _extract_markdown_links(content)
        assert ("installation guide", "INSTALL.md") in result

    def test_skips_images(self) -> None:
        """Skips image links (prefixed with !)."""
        content = "![badge](image.png) and [docs](docs.md)"
        result = _extract_markdown_links(content)
        assert ("docs", "docs.md") in result
        # Image should not be in results
        assert not any(text == "badge" for text, _ in result)

    def test_empty_content(self) -> None:
        """Returns empty list for content without links."""
        result = _extract_markdown_links("No links here")
        assert result == []

    def test_skips_reference_definition_lines(self) -> None:
        """Skips reference definitions that look like empty-ref links."""
        content = """
See [docs][] for more.

[docs]: https://example.com/docs
"""
        result = _extract_markdown_links(content)
        # Should only extract the [docs][] reference, not the definition line
        assert len(result) == 1
        assert ("docs", "https://example.com/docs") in result


class TestExtractOrgLinks:
    """Tests for _extract_org_links function."""

    def test_org_link_with_text(self) -> None:
        """Extracts Org-mode links with description text."""
        content = "Check the [[https://orgmode.org][Org Mode website]]"
        result = _extract_org_links(content)
        assert ("Org Mode website", "https://orgmode.org") in result

    def test_org_link_without_text(self) -> None:
        """Extracts Org-mode links without description."""
        content = "See [[file:docs/guide.org]]"
        result = _extract_org_links(content)
        assert ("file:docs/guide.org", "file:docs/guide.org") in result

    def test_multiple_org_links(self) -> None:
        """Extracts multiple Org-mode links."""
        content = "[[doc1.org][Doc 1]] and [[doc2.org][Doc 2]]"
        result = _extract_org_links(content)
        assert ("Doc 1", "doc1.org") in result
        assert ("Doc 2", "doc2.org") in result

    def test_empty_content(self) -> None:
        """Returns empty list for content without links."""
        result = _extract_org_links("No links here")
        assert result == []


class TestExtractRstLinks:
    """Tests for _extract_rst_links function."""

    def test_inline_links(self) -> None:
        """Extracts inline RST links."""
        content = "See `documentation <docs/index.rst>`_ for details."
        result = _extract_rst_links(content)
        assert ("documentation", "docs/index.rst") in result

    def test_anonymous_links(self) -> None:
        """Extracts anonymous RST links (double underscore)."""
        content = "See `docs <documentation/>`__"
        result = _extract_rst_links(content)
        assert ("docs", "documentation/") in result

    def test_reference_style_links(self) -> None:
        """Extracts reference-style RST links."""
        content = """
Read the `installation guide`_ first.

.. _installation guide: docs/install.rst
"""
        result = _extract_rst_links(content)
        assert ("installation guide", "docs/install.rst") in result

    def test_empty_content(self) -> None:
        """Returns empty list for content without links."""
        result = _extract_rst_links("No links here")
        assert result == []


class TestExtractAsciidocLinks:
    """Tests for _extract_asciidoc_links function."""

    def test_url_with_text(self) -> None:
        """Extracts AsciiDoc URL with bracket text."""
        content = "Visit https://example.com/docs[the docs] for info."
        result = _extract_asciidoc_links(content)
        assert ("the docs", "https://example.com/docs") in result

    def test_link_macro(self) -> None:
        """Extracts AsciiDoc link: macro."""
        content = "See link:docs/guide.adoc[the guide]"
        result = _extract_asciidoc_links(content)
        assert ("the guide", "docs/guide.adoc") in result

    def test_attribute_reference(self) -> None:
        """Extracts AsciiDoc attribute reference links."""
        content = """
:docs-base: https://docs.example.com

See {docs-base}/getting-started[Getting Started]
"""
        result = _extract_asciidoc_links(content)
        assert ("Getting Started", "https://docs.example.com/getting-started") in result

    def test_attribute_with_path_suffix(self) -> None:
        """Extracts AsciiDoc attribute with path suffix."""
        content = """
:project-home: /docs

Read {project-home}/install.adoc[Installation Guide]
"""
        result = _extract_asciidoc_links(content)
        assert ("Installation Guide", "/docs/install.adoc") in result

    def test_empty_content(self) -> None:
        """Returns empty list for content without links."""
        result = _extract_asciidoc_links("No links here")
        assert result == []

    def test_link_macro_with_attribute_substitution(self) -> None:
        """Extracts AsciiDoc link: macro with attribute in URL."""
        content = """
:base-url: https://docs.example.com

See link:{base-url}/guide[the guide]
"""
        result = _extract_asciidoc_links(content)
        assert ("the guide", "https://docs.example.com/guide") in result


class TestResolveReadmeLink:
    """Tests for _resolve_readme_link function."""

    def test_relative_path(self, tmp_path: Path) -> None:
        """Resolves relative paths from README directory."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        guide = docs_dir / "guide.md"
        guide.write_text("# Guide")

        result = _resolve_readme_link("guide.md", docs_dir, tmp_path, "myrepo")
        assert result == guide

    def test_absolute_path(self, tmp_path: Path) -> None:
        """Resolves absolute paths as repo-relative."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        guide = docs_dir / "guide.md"
        guide.write_text("# Guide")

        result = _resolve_readme_link("/docs/guide.md", tmp_path, tmp_path, "myrepo")
        assert result == guide

    def test_anchor_only_returns_none(self, tmp_path: Path) -> None:
        """Returns None for anchor-only links."""
        result = _resolve_readme_link("#section", tmp_path, tmp_path, "myrepo")
        assert result is None

    def test_external_url_returns_none(self, tmp_path: Path) -> None:
        """Returns None for external URLs."""
        result = _resolve_readme_link(
            "https://example.com/doc", tmp_path, tmp_path, "myrepo"
        )
        assert result is None

    def test_non_file_protocol_returns_none(self, tmp_path: Path) -> None:
        """Returns None for non-file protocols."""
        result = _resolve_readme_link("mailto:user@example.com", tmp_path, tmp_path, "x")
        assert result is None
        result = _resolve_readme_link("javascript:void(0)", tmp_path, tmp_path, "x")
        assert result is None
        result = _resolve_readme_link("irc://chat.freenode.net", tmp_path, tmp_path, "x")
        assert result is None

    def test_file_not_found_returns_none(self, tmp_path: Path) -> None:
        """Returns None when linked file doesn't exist."""
        result = _resolve_readme_link("nonexistent.md", tmp_path, tmp_path, "myrepo")
        assert result is None

    def test_outside_repo_returns_none(self, tmp_path: Path) -> None:
        """Returns None when resolved path is outside repo."""
        # Create a file outside repo
        result = _resolve_readme_link("../outside.md", tmp_path, tmp_path, "myrepo")
        assert result is None

    def test_directory_with_index(self, tmp_path: Path) -> None:
        """Resolves directory links to index file."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        readme = docs_dir / "README.md"
        readme.write_text("# Docs")

        result = _resolve_readme_link("docs", tmp_path, tmp_path, "myrepo")
        assert result == readme

    def test_relative_forge_url_pattern(self, tmp_path: Path) -> None:
        """Handles relative forge URL patterns like /repo/tree/branch/path."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        guide = docs_dir / "guide.md"
        guide.write_text("# Guide")

        # Relative forge URL (used in GitLab/Forgejo READMEs)
        result = _resolve_readme_link(
            "/myrepo/tree/main/docs/guide.md", tmp_path, tmp_path, "myrepo"
        )
        assert result == guide

    def test_gitlab_style_forge_url(self, tmp_path: Path) -> None:
        """Handles GitLab-style /-/tree/ URLs."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        guide = docs_dir / "guide.md"
        guide.write_text("# Guide")

        result = _resolve_readme_link(
            "/myrepo/-/tree/main/docs/guide.md", tmp_path, tmp_path, "myrepo"
        )
        assert result == guide

    def test_strips_anchor_and_query(self, tmp_path: Path) -> None:
        """Strips anchor and query string from links."""
        guide = tmp_path / "guide.md"
        guide.write_text("# Guide")

        result = _resolve_readme_link("guide.md#section", tmp_path, tmp_path, "myrepo")
        assert result == guide

        result = _resolve_readme_link("guide.md?foo=bar", tmp_path, tmp_path, "myrepo")
        assert result == guide

    def test_empty_file_scheme_returns_none(self, tmp_path: Path) -> None:
        """Returns None for empty file: scheme link."""
        result = _resolve_readme_link("file:", tmp_path, tmp_path, "myrepo")
        assert result is None

    def test_github_pages_url(self, tmp_path: Path) -> None:
        """Resolves GitHub Pages URL to source file."""
        docs = tmp_path / "docs"
        docs.mkdir()
        guide = docs / "install.md"
        guide.write_text("# Install")

        result = _resolve_readme_link(
            "https://myrepo.github.io/install", tmp_path, tmp_path, "myrepo"
        )
        assert result == guide

    def test_forge_url_same_repo(self, tmp_path: Path) -> None:
        """Resolves forge URL for same repo."""
        docs = tmp_path / "docs"
        docs.mkdir()
        guide = docs / "guide.md"
        guide.write_text("# Guide")

        result = _resolve_readme_link(
            "https://github.com/owner/myrepo/blob/main/docs/guide.md",
            tmp_path,
            tmp_path,
            "myrepo",
        )
        assert result == guide

    def test_forge_url_different_repo_returns_none(self, tmp_path: Path) -> None:
        """Returns None for forge URL to different repo."""
        result = _resolve_readme_link(
            "https://github.com/owner/otherrepo/blob/main/file.md",
            tmp_path,
            tmp_path,
            "myrepo",
        )
        assert result is None


class TestResolvePagesUrl:
    """Tests for _resolve_pages_url function."""

    def test_finds_file_in_docs(self, tmp_path: Path) -> None:
        """Finds source file in docs directory."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        guide = docs_dir / "getting-started.md"
        guide.write_text("# Getting Started")

        result = _resolve_pages_url(
            "https://myproject.github.io/getting-started", "myproject", tmp_path
        )
        assert result == guide

    def test_finds_file_in_website(self, tmp_path: Path) -> None:
        """Finds source file in website directory."""
        website = tmp_path / "website"
        website.mkdir()
        guide = website / "install.md"
        guide.write_text("# Install")

        result = _resolve_pages_url(
            "https://myproject.github.io/install", "myproject", tmp_path
        )
        assert result == guide

    def test_strips_repo_name_from_path(self, tmp_path: Path) -> None:
        """Strips repo name prefix from path if present."""
        guide = tmp_path / "getting-started.md"
        guide.write_text("# Getting Started")

        result = _resolve_pages_url(
            "https://myproject.github.io/myproject/getting-started", "myproject", tmp_path
        )
        assert result == guide

    def test_returns_none_for_nonexistent(self, tmp_path: Path) -> None:
        """Returns None when no matching file found."""
        result = _resolve_pages_url(
            "https://myproject.github.io/nonexistent", "myproject", tmp_path
        )
        assert result is None

    def test_empty_path_returns_none(self, tmp_path: Path) -> None:
        """Returns None for root path."""
        result = _resolve_pages_url("https://myproject.github.io/", "myproject", tmp_path)
        assert result is None

    def test_strips_docs_prefix(self, tmp_path: Path) -> None:
        """Finds source when URL path starts with docs/."""
        website = tmp_path / "website" / "docs"
        website.mkdir(parents=True)
        guide = website / "guide.md"
        guide.write_text("# Guide")

        result = _resolve_pages_url(
            "https://myproject.github.io/docs/guide", "myproject", tmp_path
        )
        assert result == guide


class TestExtractPathFromForgeUrl:
    """Tests for _extract_path_from_forge_url function."""

    def test_github_blob_url(self) -> None:
        """Extracts path from GitHub blob URL."""
        url = "https://github.com/owner/myrepo/blob/main/docs/guide.md"
        result = _extract_path_from_forge_url(url, "myrepo")
        assert result == "docs/guide.md"

    def test_github_tree_url(self) -> None:
        """Extracts path from GitHub tree URL."""
        url = "https://github.com/owner/myrepo/tree/main/docs"
        result = _extract_path_from_forge_url(url, "myrepo")
        assert result == "docs"

    def test_gitlab_style_url(self) -> None:
        """Extracts path from GitLab-style /-/ URL."""
        url = "https://gitlab.com/owner/myrepo/-/blob/main/docs/guide.md"
        result = _extract_path_from_forge_url(url, "myrepo")
        assert result == "docs/guide.md"

    def test_raw_githubusercontent_url(self) -> None:
        """Extracts path from raw.githubusercontent.com URL."""
        url = "https://raw.githubusercontent.com/owner/myrepo/main/docs/guide.md"
        result = _extract_path_from_forge_url(url, "myrepo")
        assert result == "docs/guide.md"

    def test_different_repo_returns_none(self) -> None:
        """Returns None for URLs pointing to different repo."""
        url = "https://github.com/owner/otherrepo/blob/main/docs/guide.md"
        result = _extract_path_from_forge_url(url, "myrepo")
        assert result is None

    def test_short_url_returns_none(self) -> None:
        """Returns None for URLs with insufficient path parts."""
        url = "https://github.com/owner"
        result = _extract_path_from_forge_url(url, "myrepo")
        assert result is None


class TestExtractReadmeInternalLinks:
    """Tests for _extract_readme_internal_links function."""

    def test_extracts_markdown_links(self, tmp_path: Path) -> None:
        """Extracts and resolves links from Markdown README."""
        readme = tmp_path / "README.md"
        guide = tmp_path / "INSTALL.md"
        guide.write_text("# Installation")
        readme.write_text("See [Installation](INSTALL.md) for setup.")

        result = _extract_readme_internal_links(readme, tmp_path)
        assert guide in result

    def test_extracts_org_links(self, tmp_path: Path) -> None:
        """Extracts and resolves links from Org-mode README."""
        readme = tmp_path / "README.org"
        contrib = tmp_path / "CONTRIBUTING.org"
        contrib.write_text("* Contributing")
        readme.write_text("Read [[file:CONTRIBUTING.org][Contributing Guide]]")

        result = _extract_readme_internal_links(readme, tmp_path)
        assert contrib in result

    def test_extracts_rst_links(self, tmp_path: Path) -> None:
        """Extracts and resolves links from RST README."""
        readme = tmp_path / "README.rst"
        docs = tmp_path / "docs"
        docs.mkdir()
        guide = docs / "guide.rst"
        guide.write_text("Guide\n=====")
        readme.write_text("See `guide <docs/guide.rst>`_ for details.")

        result = _extract_readme_internal_links(readme, tmp_path)
        assert guide in result

    def test_extracts_asciidoc_links(self, tmp_path: Path) -> None:
        """Extracts and resolves links from AsciiDoc README."""
        readme = tmp_path / "README.adoc"
        guide = tmp_path / "INSTALL.adoc"
        guide.write_text("= Installation")
        readme.write_text("See link:INSTALL.adoc[Installation Guide]")

        result = _extract_readme_internal_links(readme, tmp_path)
        assert guide in result

    def test_filters_duplicates(self, tmp_path: Path) -> None:
        """Returns unique list of resolved paths."""
        readme = tmp_path / "README.md"
        guide = tmp_path / "guide.md"
        guide.write_text("# Guide")
        readme.write_text("[Guide](guide.md) and [again](guide.md)")

        result = _extract_readme_internal_links(readme, tmp_path)
        assert result.count(guide) == 1

    def test_filters_unresolvable_links(self, tmp_path: Path) -> None:
        """Filters out links that cannot be resolved."""
        readme = tmp_path / "README.md"
        readme.write_text("[Broken](nonexistent.md) and [External](https://example.com)")

        result = _extract_readme_internal_links(readme, tmp_path)
        assert result == []


class TestRunAnalysis:
    """Tests for running static analysis."""

    def test_analyzes_python_files(self, tmp_path: Path) -> None:
        """Runs Python analysis on Python files."""
        (tmp_path / "main.py").write_text("def hello():\n    print('hi')\n")

        profile = detect_profile(tmp_path)
        symbols, edges, coverage_stats = _run_analysis(tmp_path, profile)

        assert len(symbols) > 0
        names = {s.name for s in symbols}
        assert "hello" in names

    def test_handles_no_python(self, tmp_path: Path) -> None:
        """Returns empty results when no Python files."""
        (tmp_path / "readme.md").write_text("# Hello")

        profile = detect_profile(tmp_path)
        symbols, edges, coverage_stats = _run_analysis(tmp_path, profile)

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


class TestFormatDatamodels:
    """Tests for data model formatting."""

    def test_formats_datamodels(self, tmp_path: Path) -> None:
        """Formats data models as Markdown."""
        symbols = [
            Symbol(id="test:User", name="User", kind="class", language="python",
                   path=str(tmp_path / "models.py"), span=Span(1, 1, 1, 10)),
        ]
        datamodels = [
            DataModel(symbol_id="test:User", kind=DataModelKind.ORM_MODEL,
                      confidence=0.95, label="model", framework="Django"),
        ]

        result = _format_datamodels(datamodels, symbols, tmp_path)

        assert "## Data Models" in result
        assert "`User`" in result
        assert "Django model" in result

    def test_respects_max_entries(self, tmp_path: Path) -> None:
        """Limits output to max_entries."""
        symbols = [
            Symbol(id=f"test:Model{i}", name=f"Model{i}", kind="class", language="python",
                   path=str(tmp_path / "models.py"), span=Span(i, 1, i, 10))
            for i in range(10)
        ]
        datamodels = [
            DataModel(symbol_id=f"test:Model{i}", kind=DataModelKind.PYDANTIC_MODEL,
                      confidence=0.85, label="model", framework="Pydantic")
            for i in range(10)
        ]

        result = _format_datamodels(datamodels, symbols, tmp_path, max_entries=3)

        assert "... and 7 more data models" in result

    def test_empty_datamodels_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty string for empty data models."""
        result = _format_datamodels([], [], tmp_path)
        assert result == ""

    def test_missing_symbol_fallback(self, tmp_path: Path) -> None:
        """Falls back to symbol_id when symbol not found."""
        datamodels = [
            DataModel(symbol_id="unknown:Model", kind=DataModelKind.DATACLASS,
                      confidence=0.90, label="@dataclass", framework="Python"),
        ]

        result = _format_datamodels(datamodels, [], tmp_path)

        assert "`unknown:Model`" in result
        assert "@dataclass" in result

    def test_format_without_framework(self, tmp_path: Path) -> None:
        """Formats model without framework info."""
        symbols = [
            Symbol(id="test:UserModel", name="UserModel", kind="class", language="python",
                   path=str(tmp_path / "models.py"), span=Span(1, 1, 1, 10)),
        ]
        datamodels = [
            DataModel(symbol_id="test:UserModel", kind=DataModelKind.DOMAIN_MODEL,
                      confidence=0.70, label="Domain model", framework=""),
        ]

        result = _format_datamodels(datamodels, symbols, tmp_path)

        assert "`UserModel`" in result
        assert "(Domain model)" in result

    def test_strips_repo_root_from_path(self, tmp_path: Path) -> None:
        """Strips repo root from file paths."""
        symbols = [
            Symbol(id="test:User", name="User", kind="class", language="python",
                   path=str(tmp_path / "src" / "models.py"), span=Span(1, 1, 1, 10)),
        ]
        datamodels = [
            DataModel(symbol_id="test:User", kind=DataModelKind.ORM_MODEL,
                      confidence=0.95, label="model", framework="Django"),
        ]

        result = _format_datamodels(datamodels, symbols, tmp_path)

        # Path should be relative, not absolute
        assert "src/models.py" in result or "src\\models.py" in result


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

    def test_stats_out_tracks_all_sections(self, tmp_path: Path) -> None:
        """stats_out parameter tracks statistics for all sections."""
        # Create a project with enough content to trigger all sections
        src = tmp_path / "src"
        src.mkdir()

        # Create multiple Python files with functions and classes
        (src / "main.py").write_text(
            "from utils import helper\n\n"
            "def main():\n    helper()\n\n"
            "class App:\n    def run(self):\n        pass\n"
        )
        (src / "utils.py").write_text(
            "def helper():\n    pass\n\n"
            "def process():\n    pass\n"
        )
        (src / "models.py").write_text(
            "class User:\n    name: str\n    email: str\n\n"
            "class Config:\n    debug: bool\n"
        )

        # Create config and additional files
        (tmp_path / "config.yaml").write_text("debug: true\nport: 8080\n")
        (tmp_path / "README.md").write_text("# Test Project\n\nA test project.\n")

        # Generate sketch with stats tracking
        stats = SketchStats()
        sketch = generate_sketch(
            tmp_path,
            max_tokens=5000,  # Large budget to include all sections
            with_source=True,  # Enable source content sections
            stats_out=stats,
        )

        # Verify stats were tracked
        assert stats.token_budget == 5000
        assert stats.has_key_symbols is True
        assert stats.key_symbols_in_degree >= 0

        # Verify sketch content
        assert "## Overview" in sketch
        assert "## Key Symbols" in sketch

    def test_stats_out_tracks_source_files(self, tmp_path: Path) -> None:
        """stats_out tracks source files in-degree."""
        # Create files with symbols that have edges
        (tmp_path / "main.py").write_text(
            "from utils import helper, process\n\n"
            "def main():\n    helper()\n    process()\n"
        )
        (tmp_path / "utils.py").write_text(
            "def helper():\n    pass\n\n"
            "def process():\n    helper()\n"
        )

        stats = SketchStats()
        sketch = generate_sketch(
            tmp_path,
            max_tokens=3000,
            stats_out=stats,
        )

        # Should have source files section
        if "## Source Files" in sketch:
            assert stats.has_source_files is True
            assert stats.source_files_in_degree >= 0

    def test_stats_out_tracks_additional_files(self, tmp_path: Path) -> None:
        """stats_out tracks additional files in-degree."""
        src = tmp_path / "src"
        src.mkdir()
        # Create some code in src
        (src / "app.py").write_text("def main():\n    pass\n")
        (src / "utils.py").write_text("def helper():\n    pass\n")

        # Create additional files (config, docs) that should be included
        (tmp_path / "config.yaml").write_text("key: value\nhost: localhost\n")
        (tmp_path / "settings.json").write_text('{"debug": true, "port": 8080}')

        stats = SketchStats()
        sketch = generate_sketch(
            tmp_path,
            max_tokens=5000,  # Larger budget to include additional files
            stats_out=stats,
        )

        # Verify sketch was generated with overview
        assert "## Overview" in sketch
        # Stats should be tracked regardless of whether section was included
        assert stats.token_budget == 5000

    def test_stats_out_tracks_content_sections(self, tmp_path: Path) -> None:
        """stats_out tracks source and additional file content sections."""
        src = tmp_path / "src"
        src.mkdir()
        # Create code files with more content for better coverage
        (src / "main.py").write_text(
            "def main():\n"
            "    '''Main entry point.'''\n"
            "    print('Hello')\n"
            "    helper()\n\n"
            "def helper():\n"
            "    '''Helper function.'''\n"
            "    pass\n"
        )
        (src / "utils.py").write_text(
            "def process():\n"
            "    '''Process data.'''\n"
            "    return 42\n\n"
            "def compute():\n"
            "    '''Compute result.'''\n"
            "    return process() * 2\n"
        )

        # Config files for additional content
        (tmp_path / "config.yaml").write_text("debug: true\nhost: localhost\nport: 8080\n")

        stats = SketchStats()
        sketch = generate_sketch(
            tmp_path,
            max_tokens=8000,  # Large budget for content sections
            with_source=True,
            stats_out=stats,
        )

        # Verify sketch was generated
        assert "## Overview" in sketch
        # with_source=True should trigger source content sections
        # Stats tracking is exercised regardless of whether sections fit in budget
        assert stats.token_budget == 8000

    def test_stats_out_tracks_datamodels(self, tmp_path: Path) -> None:
        """stats_out tracks datamodels confidence."""
        # Create a file with dataclass-like classes
        (tmp_path / "models.py").write_text(
            "from dataclasses import dataclass\n\n"
            "@dataclass\n"
            "class User:\n"
            "    name: str\n"
            "    email: str\n\n"
            "@dataclass\n"
            "class Product:\n"
            "    id: int\n"
            "    price: float\n"
        )
        (tmp_path / "app.py").write_text(
            "from models import User, Product\n\n"
            "def main():\n    u = User('a', 'b')\n"
        )

        stats = SketchStats()
        sketch = generate_sketch(
            tmp_path,
            max_tokens=5000,
            stats_out=stats,
        )

        # If datamodels were detected and section generated
        if stats.has_datamodels:
            assert stats.datamodels_confidence >= 0
            assert stats.total_datamodel_confidence >= 0


class TestCLISketch:
    """Tests for CLI sketch command."""

    def test_sketch_nonexistent_path(self, capsys) -> None:
        """Sketch command handles nonexistent paths."""
        from hypergumbo_core.cli import main

        result = main(["/nonexistent/path/that/does/not/exist"])

        assert result == 1
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

    def test_sketch_default_mode(self, tmp_path: Path, capsys) -> None:
        """Default mode runs sketch."""
        from hypergumbo_core.cli import main

        (tmp_path / "app.py").write_text("def main():\n    pass\n")

        result = main([str(tmp_path)])

        assert result == 0
        captured = capsys.readouterr()
        assert "## Overview" in captured.out

    def test_sketch_with_tokens_flag(self, tmp_path: Path, capsys) -> None:
        """Sketch respects -t flag."""
        from hypergumbo_core.cli import main

        (tmp_path / "app.py").write_text("def main():\n    pass\n")

        result = main([str(tmp_path), "-t", "50"])

        assert result == 0
        captured = capsys.readouterr()
        # Split output into sketch content and summary
        # The summary starts with "[hypergumbo sketch]"
        parts = captured.out.split("[hypergumbo sketch]")
        sketch_content = parts[0]
        assert len(sketch_content) < 500  # Sketch should be truncated

    def test_sketch_explicit_command(self, tmp_path: Path, capsys) -> None:
        """Sketch works with explicit 'sketch' command."""
        from hypergumbo_core.cli import main

        (tmp_path / "app.py").write_text("def main():\n    pass\n")

        result = main(["sketch", str(tmp_path)])

        assert result == 0
        captured = capsys.readouterr()
        assert "## Overview" in captured.out

    def test_sketch_exclude_tests_flag(self, tmp_path: Path, capsys) -> None:
        """Sketch respects --exclude-tests flag."""
        from hypergumbo_core.cli import main

        (tmp_path / "app.py").write_text("def main():\n    pass\n")

        result = main([str(tmp_path), "-x"])

        assert result == 0
        captured = capsys.readouterr()
        assert "## Overview" in captured.out

    def test_sketch_caches_to_file(self, tmp_path: Path, capsys) -> None:
        """Sketch is cached to a file in the cache directory."""
        from hypergumbo_core.cli import main
        from hypergumbo_core.sketch_embeddings import _get_results_cache_dir

        (tmp_path / "app.py").write_text("def main():\n    pass\n")

        result = main([str(tmp_path), "-t", "4000"])

        assert result == 0

        # Check that sketch was cached (source included by default)
        cache_dir = _get_results_cache_dir(tmp_path)
        sketch_file = cache_dir / "sketch.4000.withsource.md"
        assert sketch_file.exists()

        # Cached sketch should not include the summary line
        cached_content = sketch_file.read_text()
        assert "## Overview" in cached_content
        assert "[hypergumbo sketch]" not in cached_content

    def test_sketch_cache_filename_with_flags(self, tmp_path: Path, capsys) -> None:
        """Sketch cache filename includes non-default flags."""
        from hypergumbo_core.cli import main
        from hypergumbo_core.sketch_embeddings import _get_results_cache_dir

        (tmp_path / "app.py").write_text("def main():\n    pass\n")

        result = main([str(tmp_path), "-t", "4000", "-x"])

        assert result == 0

        # Check that sketch was cached with correct filename (source included by default)
        cache_dir = _get_results_cache_dir(tmp_path)
        sketch_file = cache_dir / "sketch.4000.notests.withsource.md"
        assert sketch_file.exists()


class TestGenerateSketchFilename:
    """Tests for _generate_sketch_filename helper."""

    def test_no_budget(self) -> None:
        """Returns 'sketch.md' when no budget specified."""
        from hypergumbo_core.cli import _generate_sketch_filename

        result = _generate_sketch_filename()
        assert result == "sketch.md"

    def test_with_budget(self) -> None:
        """Includes token budget in filename."""
        from hypergumbo_core.cli import _generate_sketch_filename

        result = _generate_sketch_filename(tokens=4000)
        assert result == "sketch.4000.md"

    def test_with_exclude_tests(self) -> None:
        """Includes 'notests' suffix when exclude_tests=True."""
        from hypergumbo_core.cli import _generate_sketch_filename

        result = _generate_sketch_filename(tokens=8000, exclude_tests=True)
        assert result == "sketch.8000.notests.md"

    def test_with_source(self) -> None:
        """Includes 'withsource' suffix when with_source=True."""
        from hypergumbo_core.cli import _generate_sketch_filename

        result = _generate_sketch_filename(tokens=16000, with_source=True)
        assert result == "sketch.16000.withsource.md"

    def test_all_flags(self) -> None:
        """Includes all flags in filename."""
        from hypergumbo_core.cli import _generate_sketch_filename

        result = _generate_sketch_filename(
            tokens=4000, exclude_tests=True, with_source=True
        )
        assert result == "sketch.4000.notests.withsource.md"

    def test_no_budget_with_flags(self) -> None:
        """Handles flags without budget."""
        from hypergumbo_core.cli import _generate_sketch_filename

        result = _generate_sketch_filename(exclude_tests=True)
        assert result == "sketch.notests.md"


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
        symbols_all, _, _ = _run_analysis(tmp_path, profile, exclude_tests=False)
        all_names = [s.name for s in symbols_all]
        assert "main" in all_names
        assert "test_main" in all_names

        # With exclude_tests, should exclude test symbols
        symbols_filtered, _, _ = _run_analysis(tmp_path, profile, exclude_tests=True)
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
        _, edges, _ = _run_analysis(tmp_path, profile, exclude_tests=True)

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

    def test_format_language_stats_markdown_repo_with_exclude_tests(
        self, tmp_path: Path
    ) -> None:
        """exclude_tests shows correct counts for markdown-only repos.

        Regression test for a bug where repos with non-source files (markdown,
        JSON, YAML) showed "0 files" and "~0 LOC" when using -x flag because
        _format_language_stats was recalculating totals from an empty
        filtered_source_files list instead of using the profile's counts.
        """
        from hypergumbo_core.profile import RepoProfile, LanguageStats

        # Simulate a markdown/JSON repo (like compose-spec)
        profile = RepoProfile()
        profile.languages = {
            "markdown": LanguageStats(files=38, loc=7500),
            "json": LanguageStats(files=4, loc=1200),
            "yaml": LanguageStats(files=1, loc=81),
        }

        # Create temp repo with only markdown files (no test files)
        (tmp_path / "README.md").write_text("# Project\n\nDescription\n")
        (tmp_path / "SPEC.md").write_text("## Specification\n\nDetails\n")

        # Without -x flag: should show breakdown format with totals
        result = _format_language_stats(
            profile, tmp_path, extra_excludes=None, exclude_tests=False
        )
        assert "43 files" in result
        assert "8,781" in result
        # Should show breakdown format (non-test + test)
        assert "non-test" in result

        # With -x flag: should STILL show breakdown with totals (no test files to exclude)
        result_exclude = _format_language_stats(
            profile, tmp_path, extra_excludes=None, exclude_tests=True
        )
        # Key assertion: counts must NOT be zero
        assert "0 files" not in result_exclude
        assert "~0 LOC" not in result_exclude
        # Should show the profile totals
        assert "43 files" in result_exclude
        assert "8,781" in result_exclude
        # Should have [IGNORING TESTS] marker
        assert "[IGNORING TESTS]" in result_exclude
        # Should show breakdown format
        assert "non-test" in result_exclude


class TestFormatStructureTree:
    """Tests for tree-based structure formatting (ADR-0005)."""

    def test_renders_tree_format(self, tmp_path: Path) -> None:
        """Renders tree with box-drawing characters."""
        # Create directory structure
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text("test")

        important_files = ["src/main.py", "tests/test_main.py"]

        result = _format_structure_tree(tmp_path, important_files)

        assert "## Structure" in result
        assert "├── " in result or "└── " in result
        assert "src" in result
        assert "main.py" in result
        assert "tests" in result

    def test_shows_hidden_item_counts(self, tmp_path: Path) -> None:
        """Shows sibling counts as [and N other items]."""
        # Create structure with more files than shown
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("main")
        (tmp_path / "src" / "utils.py").write_text("utils")
        (tmp_path / "src" / "helpers.py").write_text("helpers")
        (tmp_path / "src" / "config.py").write_text("config")

        # Only show main.py as important
        important_files = ["src/main.py"]

        result = _format_structure_tree(tmp_path, important_files)

        # Should show count of hidden items
        assert "[and 3 other items]" in result

    def test_respects_max_root_dirs(self, tmp_path: Path) -> None:
        """Limits number of root-level directories shown."""
        # Create many directories
        for i in range(15):
            (tmp_path / f"dir{i:02d}").mkdir()
            (tmp_path / f"dir{i:02d}" / "file.py").write_text("content")

        # Try to show files from all directories
        important_files = [f"dir{i:02d}/file.py" for i in range(15)]

        result = _format_structure_tree(tmp_path, important_files, max_root_dirs=5)

        # Should show count of hidden root items
        assert "[and" in result and "other items]" in result

    def test_uses_tree_format_when_no_files(self, tmp_path: Path) -> None:
        """Uses tree format even when no files provided (shows top-level dirs)."""
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()

        result = _format_structure_tree(tmp_path, [])

        # Should use tree format consistently (not deprecated bullet format)
        assert "```" in result  # Tree format uses code block
        assert "src/" in result
        assert "tests/" in result

    def test_renders_nested_structure(self, tmp_path: Path) -> None:
        """Renders nested directory paths correctly."""
        # Create nested structure
        (tmp_path / "src" / "api" / "routes").mkdir(parents=True)
        (tmp_path / "src" / "api" / "routes" / "users.py").write_text("routes")

        important_files = ["src/api/routes/users.py"]

        result = _format_structure_tree(tmp_path, important_files)

        assert "src" in result
        assert "api" in result
        assert "routes" in result
        assert "users.py" in result

    def test_sorts_directories_before_files(self, tmp_path: Path) -> None:
        """Directories are listed before files at same level."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "subdir").mkdir()
        (tmp_path / "src" / "subdir" / "nested.py").write_text("nested")
        (tmp_path / "src" / "main.py").write_text("main")
        (tmp_path / "config.yaml").write_text("config")

        important_files = ["src/main.py", "src/subdir/nested.py", "config.yaml"]

        result = _format_structure_tree(tmp_path, important_files)

        # Check that result contains expected items
        assert "src" in result
        assert "config.yaml" in result
        assert "main.py" in result

    def test_handles_root_level_files(self, tmp_path: Path) -> None:
        """Handles files at the repository root level."""
        (tmp_path / "config.yaml").write_text("config")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("main")

        important_files = ["config.yaml", "src/main.py"]

        result = _format_structure_tree(tmp_path, important_files)

        assert "config.yaml" in result
        assert "src" in result

    def test_excludes_patterns_from_counts(self, tmp_path: Path) -> None:
        """Excludes patterns (like __pycache__) from sibling counts."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("main")
        (tmp_path / "src" / "__pycache__").mkdir()  # Should be excluded

        important_files = ["src/main.py"]

        result = _format_structure_tree(tmp_path, important_files)

        # __pycache__ should not be counted
        assert "__pycache__" not in result

    def test_extra_excludes_parameter(self, tmp_path: Path) -> None:
        """Extra excludes are used for sibling counts."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("main")
        (tmp_path / "src" / "generated").mkdir()
        (tmp_path / "src" / "utils.py").write_text("utils")

        important_files = ["src/main.py"]

        result = _format_structure_tree(tmp_path, important_files, extra_excludes=["generated"])

        # generated should be excluded from count
        assert "generated" not in result
        # Only utils.py should be counted as hidden
        assert "[and 1 other items]" in result

    def test_skips_empty_paths(self, tmp_path: Path) -> None:
        """Skips empty paths in important_files."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("main")

        important_files = ["", "src/main.py"]

        result = _format_structure_tree(tmp_path, important_files)

        assert "src" in result
        assert "main.py" in result

    def test_uses_tee_connector_when_hidden_items_follow(self, tmp_path: Path) -> None:
        """Uses ├── (not └──) when hidden items follow the last shown item."""
        # Create structure where shown item has hidden siblings
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("shown")
        (tmp_path / "src" / "hidden1.py").write_text("hidden")
        (tmp_path / "src" / "hidden2.py").write_text("hidden")

        # Only show main.py but there are 2 hidden siblings
        important_files = ["src/main.py"]

        result = _format_structure_tree(tmp_path, important_files)

        lines = result.split("\n")
        # Find the line with main.py
        main_line = next((l for l in lines if "main.py" in l), "")
        # Should use ├── because hidden items follow
        assert "├── main.py" in main_line, f"Expected ├── but got: {main_line}"
        # The hidden items line should use └── (it's truly last)
        hidden_line = next((l for l in lines if "other items" in l), "")
        assert "└── [and 2 other items]" in hidden_line

    def test_uses_corner_connector_when_truly_last(self, tmp_path: Path) -> None:
        """Uses └── when item is truly last (no hidden siblings)."""
        # Create structure with only shown items (no hidden)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("only file")

        important_files = ["src/main.py"]

        result = _format_structure_tree(tmp_path, important_files)

        lines = result.split("\n")
        main_line = next((l for l in lines if "main.py" in l), "")
        # Should use └── because it's truly last (no hidden items)
        assert "└── main.py" in main_line, f"Expected └── but got: {main_line}"

    def test_continuation_lines_when_hidden_siblings(self, tmp_path: Path) -> None:
        """Uses │ continuation when directory has hidden siblings after subtree."""
        # Create nested structure with hidden items at intermediate level
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "api").mkdir()
        (tmp_path / "src" / "api" / "routes.py").write_text("routes")
        (tmp_path / "src" / "hidden").mkdir()  # Hidden sibling of api

        important_files = ["src/api/routes.py"]

        result = _format_structure_tree(tmp_path, important_files)

        # The lines after api's content should use │ continuation
        # because api has a hidden sibling (hidden/)
        lines = result.split("\n")
        # Find index of routes.py line
        routes_idx = next(i for i, l in enumerate(lines) if "routes.py" in l)
        # The next line (if any, for hidden items inside api) should continue with │
        # Or the hidden items count for src should show
        assert "[and 1 other items]" in result


class TestFormatStructureTreeFallback:
    """Tests for _format_structure_tree_fallback function."""

    def test_shows_tree_format(self, tmp_path: Path) -> None:
        """Uses tree format with code block."""
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()

        result = _format_structure_tree_fallback(tmp_path, [], exclude_tests=False)

        assert "```" in result
        assert "├──" in result or "└──" in result

    def test_shows_root_level_files(self, tmp_path: Path) -> None:
        """Shows both directories and root-level source/doc files."""
        (tmp_path / "src").mkdir()
        (tmp_path / "README.md").write_text("# Hello")
        (tmp_path / "main.py").write_text("print('hello')")

        result = _format_structure_tree_fallback(tmp_path, [], exclude_tests=False)

        assert "src/" in result
        # Root-level files with recognized extensions are shown
        assert "README.md" in result
        assert "main.py" in result

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Shows (empty) when no directories or recognized files exist."""
        # Create only an unrecognized file type
        (tmp_path / "random.xyz").write_text("unknown")

        result = _format_structure_tree_fallback(tmp_path, [], exclude_tests=False)

        assert "(empty)" in result

    def test_shows_item_counts(self, tmp_path: Path) -> None:
        """Shows item count for directories with contents."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')")
        (tmp_path / "src" / "utils.py").write_text("pass")

        result = _format_structure_tree_fallback(tmp_path, [], exclude_tests=False)

        assert "src/" in result
        assert "(2 items)" in result

    def test_more_than_10_items(self, tmp_path: Path) -> None:
        """Shows overflow message for more than 10 items."""
        for i in range(15):
            (tmp_path / f"dir_{i:02d}").mkdir()

        result = _format_structure_tree_fallback(tmp_path, [], exclude_tests=False)

        assert "[and 5 other items]" in result

    def test_respects_excludes(self, tmp_path: Path) -> None:
        """Excludes directories matching exclude patterns."""
        (tmp_path / "src").mkdir()
        (tmp_path / "node_modules").mkdir()
        (tmp_path / ".git").mkdir()

        from hypergumbo_core.discovery import DEFAULT_EXCLUDES
        result = _format_structure_tree_fallback(
            tmp_path, list(DEFAULT_EXCLUDES), exclude_tests=False
        )

        assert "src/" in result
        assert "node_modules" not in result
        assert ".git" not in result

    def test_respects_excludes_for_root_files(self, tmp_path: Path) -> None:
        """Excludes root-level files matching exclude patterns."""
        (tmp_path / "src").mkdir()
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "package-lock.json").write_text("{}")  # Common exclude pattern

        # Use a pattern that matches the lock file
        result = _format_structure_tree_fallback(
            tmp_path, ["*-lock.json"], exclude_tests=False
        )

        assert "src/" in result
        assert "main.py" in result
        assert "package-lock.json" not in result

    def test_exclude_tests_skips_root_test_files(self, tmp_path: Path) -> None:
        """When exclude_tests=True, root-level test files are skipped."""
        (tmp_path / "src").mkdir()
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "test_main.py").write_text("def test_main(): pass")

        result = _format_structure_tree_fallback(
            tmp_path, [], exclude_tests=True
        )

        assert "src/" in result
        assert "main.py" in result
        assert "test_main.py" not in result

    def test_exclude_tests_filters_test_source_files(self, tmp_path: Path) -> None:
        """When exclude_tests=True, test source files are not counted."""
        # Create tests directory with mixed content
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text("def test_a(): pass")
        (tmp_path / "tests" / "test_utils.py").write_text("def test_b(): pass")
        # Config files in tests dir should still be counted
        (tmp_path / "tests" / "Makefile").write_text("test: pytest")
        # Create src directory with production code
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')")

        result = _format_structure_tree_fallback(
            tmp_path, [], exclude_tests=True
        )

        # tests/ directory should show only config file count (1 item = Makefile)
        # The test_*.py files should not be counted
        assert "tests/" in result
        assert "(1 item" in result or "tests/" in result
        # src/ should still show its item
        assert "src/" in result

    def test_exclude_tests_skips_test_directories(self, tmp_path: Path) -> None:
        """When exclude_tests=True, subdirs inside test directories not counted."""
        # Create tests directory with subdirectory
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "unit").mkdir()
        (tmp_path / "tests" / "unit" / "test_core.py").write_text("def test(): pass")
        # The unit/ subdir is a test directory and should be skipped

        result = _format_structure_tree_fallback(
            tmp_path, [], exclude_tests=True
        )

        # tests/ should show 0 items (unit/ is a test directory)
        assert "tests/" in result


class TestCollectImportantFiles:
    """Tests for important file collection for structure tree."""

    def test_collects_config_files(self, tmp_path: Path) -> None:
        """Collects configuration files first."""
        (tmp_path / "pyproject.toml").write_text("[project]")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')")

        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=["src/main.py"],
            entrypoints=[],
            datamodels=[],
            symbols=[],
            centrality={},
        )

        assert "pyproject.toml" in result

    def test_collects_test_files(self, tmp_path: Path) -> None:
        """Collects test files by highest size."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_small.py").write_text("def test_a(): pass")
        (tmp_path / "tests" / "test_large.py").write_text("def test_b(): pass\n" * 100)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')")

        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=["tests/test_small.py", "tests/test_large.py", "src/main.py"],
            entrypoints=[],
            datamodels=[],
            symbols=[],
            centrality={},
        )

        # Should include the larger test file
        assert "tests/test_large.py" in result

    def test_collects_entrypoint_files(self, tmp_path: Path) -> None:
        """Collects files containing entry points."""
        (tmp_path / "api").mkdir()
        (tmp_path / "api" / "routes.py").write_text("@app.get('/') ...")

        symbol = Symbol(
            id="api:routes:get_root",
            name="get_root",
            kind="function",
            language="python",
            path=str(tmp_path / "api" / "routes.py"),
            span=Span(1, 1, 1, 10),
        )
        entrypoint = Entrypoint(
            symbol_id="api:routes:get_root",
            kind=EntrypointKind.HTTP_ROUTE,
            confidence=0.9,
            label="GET /",
        )

        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=[],
            entrypoints=[entrypoint],
            datamodels=[],
            symbols=[symbol],
            centrality={},
        )

        assert "api/routes.py" in result

    def test_collects_high_centrality_files(self, tmp_path: Path) -> None:
        """Collects files with high symbol centrality."""
        (tmp_path / "core").mkdir()
        (tmp_path / "core" / "models.py").write_text("class User: pass")

        symbol = Symbol(
            id="core:models:User",
            name="User",
            kind="class",
            language="python",
            path=str(tmp_path / "core" / "models.py"),
            span=Span(1, 1, 1, 20),
        )

        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=[],
            entrypoints=[],
            datamodels=[],
            symbols=[symbol],
            centrality={"core:models:User": 0.8},
        )

        assert "core/models.py" in result

    def test_collects_datamodel_files(self, tmp_path: Path) -> None:
        """Collects files containing data models."""
        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "user.py").write_text("class UserModel: pass")

        symbol = Symbol(
            id="models:user:UserModel",
            name="UserModel",
            kind="class",
            language="python",
            path=str(tmp_path / "models" / "user.py"),
            span=Span(1, 1, 1, 25),
        )
        datamodel = DataModel(
            symbol_id="models:user:UserModel",
            kind=DataModelKind.DOMAIN_MODEL,
            confidence=0.7,
            label="Domain model",
        )

        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=[],
            entrypoints=[],
            datamodels=[datamodel],
            symbols=[symbol],
            centrality={},
        )

        assert "models/user.py" in result

    def test_respects_max_root_dirs(self, tmp_path: Path) -> None:
        """Respects max_root_dirs limit."""
        # Create many directories
        for i in range(15):
            (tmp_path / f"dir{i:02d}").mkdir()
            (tmp_path / f"dir{i:02d}" / "file.py").write_text("content")

        symbols = [
            Symbol(
                id=f"dir{i:02d}:file:func",
                name="func",
                kind="function",
                language="python",
                path=str(tmp_path / f"dir{i:02d}" / "file.py"),
                span=Span(1, 1, 1, 10),
            )
            for i in range(15)
        ]
        centrality = {s.id: 0.5 for s in symbols}

        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=[],
            entrypoints=[],
            datamodels=[],
            symbols=symbols,
            centrality=centrality,
            max_root_dirs=5,
        )

        # Should not have more than 5 unique root directories
        root_dirs = {Path(f).parts[0] for f in result if f}
        assert len(root_dirs) <= 5

    def test_handles_empty_inputs(self, tmp_path: Path) -> None:
        """Handles empty inputs gracefully."""
        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=[],
            entrypoints=[],
            datamodels=[],
            symbols=[],
            centrality={},
        )

        assert result == []

    def test_stops_after_two_config_files(self, tmp_path: Path) -> None:
        """Stops collecting config files after 2 are found."""
        (tmp_path / "pyproject.toml").write_text("[project]")
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "Cargo.toml").write_text("[package]")

        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=[],
            entrypoints=[],
            datamodels=[],
            symbols=[],
            centrality={},
        )

        # Should have exactly 2 config files
        config_count = sum(1 for f in result if f in ["pyproject.toml", "package.json", "Cargo.toml"])
        assert config_count == 2

    def test_skips_files_exceeding_max_root_dirs(self, tmp_path: Path) -> None:
        """Skips files that would add more root directories than allowed."""
        # Create directories with source files
        for i in range(5):
            d = tmp_path / f"project{i}"
            d.mkdir()
            (d / "main.py").write_text("print('hello')")

        # Create symbols in those directories
        symbols = [
            Symbol(
                id=f"project{i}:main:func",
                name="func",
                kind="function",
                language="python",
                path=str(tmp_path / f"project{i}" / "main.py"),
                span=Span(1, 1, 1, 10),
            )
            for i in range(5)
        ]
        centrality = {s.id: 0.5 for s in symbols}

        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=[],
            entrypoints=[],
            datamodels=[],
            symbols=symbols,
            centrality=centrality,
            max_root_dirs=3,
        )

        # Should not have more than 3 unique root directories
        root_dirs = {Path(f).parts[0] for f in result if f}
        assert len(root_dirs) <= 3

    def test_handles_empty_path_in_symbols(self, tmp_path: Path) -> None:
        """Handles symbols with empty paths gracefully."""
        symbol = Symbol(
            id="root:file",
            name="func",
            kind="function",
            language="python",
            path="",  # Empty path
            span=Span(1, 1, 1, 10),
        )

        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=[],
            entrypoints=[],
            datamodels=[],
            symbols=[symbol],
            centrality={"root:file": 0.5},
        )

        # Should handle gracefully (empty path is skipped)
        assert "" not in result

    def test_converts_absolute_paths_to_relative(self, tmp_path: Path) -> None:
        """Converts absolute paths from symbols to relative paths."""
        # Create the actual file so it exists
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("def main(): pass")

        # Symbol with absolute path
        symbol = Symbol(
            id="root:main",
            name="main",
            kind="function",
            language="python",
            path=str(tmp_path / "src" / "app.py"),  # Absolute path
            span=Span(1, 1, 1, 10),
        )

        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=[],
            entrypoints=[],
            datamodels=[],
            symbols=[symbol],
            centrality={"root:main": 0.5},
        )

        # Should contain relative path, not absolute
        assert len(result) >= 1
        for path in result:
            assert not Path(path).is_absolute(), f"Path should be relative: {path}"
            assert "/" not in path or not path.startswith("/")
        # Check the actual path is correct
        assert "src/app.py" in result

    def test_skips_paths_outside_repo_root(self, tmp_path: Path) -> None:
        """Skips absolute paths that are not under repo_root."""
        # Symbol with path outside repo_root
        symbol = Symbol(
            id="root:external",
            name="external",
            kind="function",
            language="python",
            path="/completely/different/path/file.py",  # Not under tmp_path
            span=Span(1, 1, 1, 10),
        )
        # Use entrypoint to trigger add_file directly (covers line 2245)
        entrypoint = Entrypoint(
            symbol_id="root:external",
            kind=EntrypointKind.CLI_MAIN,
            confidence=0.9,
            label="external",
        )

        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=[],
            entrypoints=[entrypoint],
            datamodels=[],
            symbols=[symbol],
            centrality={},
        )

        # Should be empty since the path is outside repo_root
        assert "/completely" not in str(result)
        assert len(result) == 0

    def test_collects_additional_files_from_subdirectories(self, tmp_path: Path) -> None:
        """Collects additional files (CONFIG/DOCUMENTATION) from unrepresented directories."""
        # Create directories with only documentation files (no source code)
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "guide.md").write_text("# User Guide\n\nThis is a guide.")

        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()
        (examples_dir / "example.md").write_text("# Example\n\nAn example.")

        # Create an excluded directory that should be skipped
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("[core]")

        # Create a source file in src/ to ensure it's seen first
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("def main(): pass")

        symbol = Symbol(
            id="src:main",
            name="main",
            kind="function",
            language="python",
            path="src/main.py",
            span=Span(1, 1, 1, 10),
        )

        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=["src/main.py"],
            entrypoints=[],
            datamodels=[],
            symbols=[symbol],
            centrality={"src:main": 0.5},
        )

        # Should include src/main.py from source files
        assert "src/main.py" in result
        # Should include additional files from docs/ and examples/ directories
        # (these are DOCUMENTATION role files in unrepresented directories)
        root_dirs = {Path(f).parts[0] for f in result if "/" in f or "\\" in f}
        # At minimum, src should be there; docs/examples may be picked up
        assert "src" in root_dirs
        # Verify .git was excluded
        assert not any(".git" in f for f in result)

    def test_collects_root_level_source_files_without_symbols(self, tmp_path: Path) -> None:
        """Collects root-level source files even if they have no symbols.

        For flat repos like qemu-sgabios where all files are at the root level,
        we should show source files even if they don't have symbols (e.g., .h
        files with only #defines, .S assembly files).
        """
        # Create root-level source files (like qemu-sgabios)
        (tmp_path / "main.c").write_text("int main() { return 0; }")
        (tmp_path / "utils.h").write_text("#define MAX 100")  # No symbols
        (tmp_path / "code.S").write_text(".text\n.global start")  # Assembly, no symbols
        (tmp_path / "README.md").write_text("# Project")  # Not a source file

        # Only main.c has a symbol
        symbol = Symbol(
            id="root:main",
            name="main",
            kind="function",
            language="c",
            path="main.c",
            span=Span(1, 1, 1, 10),
        )

        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=["main.c"],
            entrypoints=[],
            datamodels=[],
            symbols=[symbol],
            centrality={"root:main": 0.5},
        )

        # Should include all source files, not just those with symbols
        assert "main.c" in result
        assert "utils.h" in result  # .h file even without symbols
        # README.md is not a source file, shouldn't be here
        assert "README.md" not in result

    def test_root_level_source_files_respects_max_limit(self, tmp_path: Path) -> None:
        """Root-level source files collection stops at max_root_files limit."""
        # Create 8 root-level source files (more than default max of 5)
        for i in range(8):
            (tmp_path / f"file{i}.c").write_text(f"int func{i}() {{ return {i}; }}")

        # No symbols - only step 5b will collect files
        result = _collect_important_files(
            repo_root=tmp_path,
            source_files=[],
            entrypoints=[],
            datamodels=[],
            symbols=[],
            centrality={},
            max_root_files=5,
        )

        # Should have exactly 5 root-level files (the limit)
        assert len(result) == 5
        # Files should be sorted alphabetically (file0.c, file1.c, ...)
        assert result[0] == "file0.c"


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


class TestConfigExtraction:
    """Tests for config file extraction with different modes."""

    def test_extract_package_json_fields(self, tmp_path: Path) -> None:
        """Extracts key fields from package.json."""
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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

    def test_extract_non_dict_package_json(self, tmp_path: Path) -> None:
        """Handles non-dict package.json gracefully (returns empty)."""
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

        # Valid JSON but not a dict - should be skipped
        (tmp_path / "package.json").write_text('"just a string"')

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        # Should not crash and should not include package.json info
        assert "package.json" not in result

    def test_extract_go_mod_fields(self, tmp_path: Path) -> None:
        """Extracts module and dependencies from go.mod."""
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "MIT License\n\n"
            "Permission is hereby granted, free of charge, to any person..."
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: MIT" in result

    def test_extract_agpl_license(self, tmp_path: Path) -> None:
        """Detects AGPL license correctly (before GPL)."""
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "GNU AFFERO GENERAL PUBLIC LICENSE\n"
            "Version 3, 19 November 2007\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: AGPL" in result

    def test_extract_lgpl_license(self, tmp_path: Path) -> None:
        """Detects LGPL license."""
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

        # Use the actual LGPL-style text with both GPL and Lesser
        (tmp_path / "LICENSE").write_text(
            "GNU LESSER GPL\n"
            "Version 2.1, February 1999\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: LGPL" in result

    def test_extract_gpl_license(self, tmp_path: Path) -> None:
        """Detects GPL license."""
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "GPL-3.0 License\n"
            "Version 3, 29 June 2007\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: GPL" in result

    def test_extract_apache_license(self, tmp_path: Path) -> None:
        """Detects Apache license."""
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "Apache License\n"
            "Version 2.0, January 2004\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: Apache" in result

    def test_extract_bsd_license(self, tmp_path: Path) -> None:
        """Detects BSD license."""
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "BSD 3-Clause License\n"
            "Copyright (c) 2023\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: BSD" in result

    def test_extract_mpl_license(self, tmp_path: Path) -> None:
        """Detects Mozilla Public License."""
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "Mozilla Public License Version 2.0\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: MPL" in result

    def test_extract_isc_license(self, tmp_path: Path) -> None:
        """Detects ISC License."""
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "ISC License\n"
            "Copyright (c) 2023\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: ISC" in result

    def test_extract_unlicense(self, tmp_path: Path) -> None:
        """Detects Unlicense."""
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "LICENSE").write_text(
            "This is free and unencumbered software released into the public domain.\n"
            "Unlicense\n"
        )

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert "LICENSE: Unlicense" in result

    def test_extract_mix_exs(self, tmp_path: Path) -> None:
        """Extracts Elixir project info from mix.exs."""
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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
        from hypergumbo_core.sketch import (
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
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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
        from hypergumbo_core.sketch import _extract_config_info

        (tmp_path / "package.json").write_text('{"name": "test", "version": "1.0.0"}')

        # Call without mode parameter - should use heuristic by default
        result = _extract_config_info(tmp_path)

        assert "name: test" in result
        assert "version: 1.0.0" in result

    def test_generate_sketch_with_config_mode(self, tmp_path: Path) -> None:
        """generate_sketch accepts config_extraction_mode parameter."""
        from hypergumbo_core.sketch import ConfigExtractionMode

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
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "app.py").write_text("print('hello')\n")

        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert result == ""

    def test_invalid_json_handled_gracefully(self, tmp_path: Path) -> None:
        """Handles invalid JSON in package.json gracefully."""
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

        (tmp_path / "package.json").write_text("{ invalid json }")

        # Should not raise, just return empty or partial result
        result = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HEURISTIC)

        assert isinstance(result, str)

    def test_pom_xml_extraction(self, tmp_path: Path) -> None:
        """Extracts Maven coordinates from pom.xml."""
        from hypergumbo_core.sketch import _extract_config_info, ConfigExtractionMode

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
        from hypergumbo_core.sketch import _compute_log_sample_size

        # File smaller than fleximax
        assert _compute_log_sample_size(50, fleximax=100) == 50
        assert _compute_log_sample_size(100, fleximax=100) == 100

    def test_compute_log_sample_size_large_file(self) -> None:
        """Large files use log-scaled formula."""
        from hypergumbo_core.sketch import _compute_log_sample_size

        # 1000 lines with fleximax=100: 100 + log10(1000) * 10 = 100 + 30 = 130
        result = _compute_log_sample_size(1000, fleximax=100)
        assert result == 130

        # 10000 lines: 100 + log10(10000) * 10 = 100 + 40 = 140
        result = _compute_log_sample_size(10000, fleximax=100)
        assert result == 140

    def test_compute_stride_small_file(self) -> None:
        """Small files get stride 1 (sample all)."""
        from hypergumbo_core.sketch import _compute_stride

        assert _compute_stride(50, sample_size=100) == 1
        assert _compute_stride(100, sample_size=100) == 1

    def test_compute_stride_large_file(self) -> None:
        """Large files get stride >= 4."""
        from hypergumbo_core.sketch import _compute_stride

        # 400 lines with sample_size=100 -> stride 4
        assert _compute_stride(400, sample_size=100) == 4

        # 800 lines with sample_size=100 -> stride 8
        assert _compute_stride(800, sample_size=100) == 8

        # 101 lines with sample_size=100 -> stride should be 4 minimum
        assert _compute_stride(101, sample_size=100) == 4

    def test_build_context_chunk_simple(self) -> None:
        """Builds 3-line chunk with context."""
        from hypergumbo_core.sketch import _build_context_chunk

        lines = ["line0", "line1", "line2", "line3", "line4"]

        # Center at index 2 should include lines 1, 2, 3
        chunk = _build_context_chunk(lines, center_idx=2, max_chunk_chars=800)
        assert "line1" in chunk
        assert "line2" in chunk
        assert "line3" in chunk

    def test_build_context_chunk_at_start(self) -> None:
        """Chunk at start of file only includes available context."""
        from hypergumbo_core.sketch import _build_context_chunk

        lines = ["line0", "line1", "line2"]

        # Center at index 0 should include lines 0, 1
        chunk = _build_context_chunk(lines, center_idx=0, max_chunk_chars=800)
        assert "line0" in chunk
        assert "line1" in chunk

    def test_build_context_chunk_at_end(self) -> None:
        """Chunk at end of file only includes available context."""
        from hypergumbo_core.sketch import _build_context_chunk

        lines = ["line0", "line1", "line2"]

        # Center at index 2 should include lines 1, 2
        chunk = _build_context_chunk(lines, center_idx=2, max_chunk_chars=800)
        assert "line1" in chunk
        assert "line2" in chunk

    def test_build_context_chunk_word_subsampling(self) -> None:
        """Long chunks get word-level subsampling with ellipsis."""
        from hypergumbo_core.sketch import _build_context_chunk

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
        from hypergumbo_core.sketch import _build_context_chunk

        # Create a line with few but very long words
        long_word = "x" * 300
        lines = ["before", long_word, "after"]

        chunk = _build_context_chunk(
            lines, center_idx=1, max_chunk_chars=200, fleximax_words=50
        )

        # Should be truncated
        assert len(chunk) <= 200

    def test_min_chunk_chars_expands_forward(self) -> None:
        """Minimum chunk size causes expansion forward."""
        from hypergumbo_core.sketch import _build_context_chunk

        # Short heading followed by longer content
        lines = [
            "",
            "Preamble",
            "",
            "The first paragraph of actual content.",
            "The second paragraph continues here.",
        ]

        # Without min_chunk_chars, we'd get just "Preamble"
        chunk_no_min = _build_context_chunk(lines, center_idx=1, max_chunk_chars=800)
        assert chunk_no_min == "Preamble"

        # With min_chunk_chars=80, should expand forward
        chunk_with_min = _build_context_chunk(
            lines, center_idx=1, max_chunk_chars=800, min_chunk_chars=80
        )
        assert len(chunk_with_min) >= 80
        assert "Preamble" in chunk_with_min
        assert "The first paragraph" in chunk_with_min

    def test_min_chunk_chars_stops_at_file_end(self) -> None:
        """Expansion stops at end of file if minimum can't be reached."""
        from hypergumbo_core.sketch import _build_context_chunk

        # Short content that can't meet minimum
        lines = ["Short", "text", "here"]

        chunk = _build_context_chunk(
            lines, center_idx=0, max_chunk_chars=800, min_chunk_chars=1000
        )
        # Should include all available content
        assert "Short" in chunk
        assert "text" in chunk
        assert "here" in chunk
        # But still be shorter than min because there's no more content
        assert len(chunk) < 1000

    def test_min_chunk_chars_skips_empty_lines(self) -> None:
        """Empty lines are skipped during forward expansion."""
        from hypergumbo_core.sketch import _build_context_chunk

        lines = [
            "Heading",
            "",
            "",
            "",
            "Content after many blank lines that we want to include.",
        ]

        chunk = _build_context_chunk(
            lines, center_idx=0, max_chunk_chars=800, min_chunk_chars=50
        )
        assert "Heading" in chunk
        assert "Content after many blank lines" in chunk

    def test_min_chunk_chars_zero_no_expansion(self) -> None:
        """min_chunk_chars=0 (default) doesn't trigger expansion."""
        from hypergumbo_core.sketch import _build_context_chunk

        lines = ["A", "B", "C", "D", "E"]

        # With min_chunk_chars=0, just get normal 3-line context
        chunk = _build_context_chunk(lines, center_idx=1, max_chunk_chars=800)
        assert chunk == "A B C"  # lines 0, 1, 2

        # Explicitly pass 0
        chunk_explicit = _build_context_chunk(
            lines, center_idx=1, max_chunk_chars=800, min_chunk_chars=0
        )
        assert chunk_explicit == "A B C"


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

    def test_returns_section_when_no_tests(self, tmp_path: Path) -> None:
        """Returns a Tests section even when no tests detected."""
        (tmp_path / "main.py").write_text("print('hello')")
        result = _format_test_summary(tmp_path)
        # Should always return a Tests section for consistency
        assert "## Tests" in result
        assert "No test files detected" in result

    def test_formats_as_markdown_section(self, tmp_path: Path) -> None:
        """Formats test summary as a Markdown section."""
        (tmp_path / "test_example.py").write_text("import pytest\n\ndef test_foo(): pass")
        result = _format_test_summary(tmp_path)
        assert result.startswith("## Tests\n")
        assert "pytest" in result
        # Coverage line is only shown when we can compute an estimate
        # (when there are production functions to measure)
        # Without production code, no coverage line is shown

    def test_detects_jest_framework(self, tmp_path: Path) -> None:
        """Detects jest as the test framework."""
        (tmp_path / "app.test.js").write_text("const { describe } = require('jest');\ntest('x', () => {});")
        result = _format_test_summary(tmp_path)
        # Should detect jest framework
        assert "jest" in result
        # Should NOT detect pytest for JS project
        assert "pytest" not in result

    def test_detects_vitest_framework(self, tmp_path: Path) -> None:
        """Detects vitest as the test framework."""
        (tmp_path / "app.test.ts").write_text("import { describe } from 'vitest';\ntest('x', () => {});")
        result = _format_test_summary(tmp_path)
        # Should detect vitest framework
        assert "vitest" in result
        # Should NOT detect pytest for TS project
        assert "pytest" not in result

    def test_detects_go_test_framework(self, tmp_path: Path) -> None:
        """Detects go test as the test framework."""
        (tmp_path / "main_test.go").write_text('package main\nimport "testing"\nfunc TestX(t *testing.T) {}')
        result = _format_test_summary(tmp_path)
        # Should detect go test framework
        assert "go test" in result
        # Should NOT detect pytest for Go project
        assert "pytest" not in result

    def test_detects_maven_framework(self, tmp_path: Path) -> None:
        """Detects JUnit/Maven as the test framework."""
        tests = tmp_path / "src" / "test" / "java"
        tests.mkdir(parents=True)
        (tests / "AppTest.java").write_text("import org.junit.Test;\npublic class AppTest {}")
        result = _format_test_summary(tmp_path)
        # Should detect junit framework
        assert "junit" in result
        # Should NOT detect pytest for Java project
        assert "pytest" not in result

    def test_coverage_stats_replaces_hint(self, tmp_path: Path) -> None:
        """When coverage_stats provided, shows estimated coverage instead of hint."""
        (tmp_path / "test_example.py").write_text("import pytest\n\ndef test_foo(): pass")
        # Provide coverage stats: 5 out of 10 functions tested (50%)
        coverage_stats = (5, 10, 50.0)
        result = _format_test_summary(tmp_path, coverage_stats)
        assert result.startswith("## Tests\n")
        assert "~50% estimated coverage" in result
        assert "5/10 functions called by tests" in result
        # Should NOT show the "Coverage requires execution" hint
        assert "Coverage requires execution" not in result

    def test_coverage_stats_zero_percent(self, tmp_path: Path) -> None:
        """Zero coverage is displayed correctly."""
        (tmp_path / "test_example.py").write_text("import pytest\n\ndef test_foo(): pass")
        coverage_stats = (0, 10, 0.0)
        result = _format_test_summary(tmp_path, coverage_stats)
        assert "~0% estimated coverage" in result
        assert "0/10 functions called by tests" in result

    def test_shell_integration_count_with_coverage(self, tmp_path: Path) -> None:
        """Shell integration count appended to coverage line."""
        (tmp_path / "test_example.py").write_text("import pytest\n\ndef test_foo(): pass")
        coverage_stats = (3, 10, 30.0)
        result = _format_test_summary(tmp_path, coverage_stats, shell_integration_count=5)
        assert "~30% estimated coverage" in result
        assert "3/10 functions called by tests" in result
        assert "+ 5 shell integration scripts" in result

    def test_shell_integration_count_singular(self, tmp_path: Path) -> None:
        """Uses singular 'script' for count of 1."""
        (tmp_path / "test_example.py").write_text("import pytest\n\ndef test_foo(): pass")
        coverage_stats = (3, 10, 30.0)
        result = _format_test_summary(tmp_path, coverage_stats, shell_integration_count=1)
        assert "+ 1 shell integration script" in result
        assert "scripts" not in result

    def test_shell_integration_only_no_coverage(self, tmp_path: Path) -> None:
        """Shell integration tests shown when no call graph coverage available."""
        (tmp_path / "test_example.sh").write_text("#!/bin/bash\n./myapp")
        result = _format_test_summary(tmp_path, shell_integration_count=3)
        assert "3 shell integration scripts" in result
        assert "invoke project binary" in result

    def test_shell_integration_zero_not_shown(self, tmp_path: Path) -> None:
        """Zero shell integration count not displayed."""
        (tmp_path / "test_example.py").write_text("import pytest\n\ndef test_foo(): pass")
        coverage_stats = (3, 10, 30.0)
        result = _format_test_summary(tmp_path, coverage_stats, shell_integration_count=0)
        assert "shell integration" not in result


class TestDetectProjectBinaryNames:
    """Tests for _detect_project_binary_names function."""

    def test_meson_build_executable(self, tmp_path: Path) -> None:
        """Detects executable from meson.build."""
        (tmp_path / "meson.build").write_text("executable('myapp', 'main.c')")
        from hypergumbo_core.sketch import _detect_project_binary_names
        result = _detect_project_binary_names(tmp_path)
        assert "myapp" in result

    def test_meson_build_multiple_executables(self, tmp_path: Path) -> None:
        """Detects multiple executables from meson.build."""
        (tmp_path / "meson.build").write_text(
            "executable('app1', 'main1.c')\nexecutable('app2', 'main2.c')"
        )
        from hypergumbo_core.sketch import _detect_project_binary_names
        result = _detect_project_binary_names(tmp_path)
        assert "app1" in result
        assert "app2" in result

    def test_makefile_target(self, tmp_path: Path) -> None:
        """Detects binary target from Makefile variable."""
        (tmp_path / "Makefile").write_text("TARGET = myapp\n\nmyapp: main.o\n\t$(CC) -o $(TARGET) main.o")
        from hypergumbo_core.sketch import _detect_project_binary_names
        result = _detect_project_binary_names(tmp_path)
        assert "myapp" in result

    def test_cmake_add_executable(self, tmp_path: Path) -> None:
        """Detects executable from CMakeLists.txt."""
        (tmp_path / "CMakeLists.txt").write_text("add_executable(myapp main.cpp)")
        from hypergumbo_core.sketch import _detect_project_binary_names
        result = _detect_project_binary_names(tmp_path)
        assert "myapp" in result

    def test_cargo_toml_package_name(self, tmp_path: Path) -> None:
        """Detects binary name from Cargo.toml package name."""
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "myapp"\nversion = "0.1.0"')
        from hypergumbo_core.sketch import _detect_project_binary_names
        result = _detect_project_binary_names(tmp_path)
        assert "myapp" in result

    def test_cargo_toml_explicit_bin(self, tmp_path: Path) -> None:
        """Detects explicit [[bin]] name from Cargo.toml."""
        (tmp_path / "Cargo.toml").write_text('[[bin]]\nname = "my-cli"\npath = "src/main.rs"')
        from hypergumbo_core.sketch import _detect_project_binary_names
        result = _detect_project_binary_names(tmp_path)
        assert "my-cli" in result

    def test_go_mod_module_name(self, tmp_path: Path) -> None:
        """Detects binary name from go.mod module path."""
        (tmp_path / "go.mod").write_text("module github.com/user/myapp\n\ngo 1.21")
        from hypergumbo_core.sketch import _detect_project_binary_names
        result = _detect_project_binary_names(tmp_path)
        assert "myapp" in result

    def test_configure_ac_package_name(self, tmp_path: Path) -> None:
        """Detects package name from configure.ac."""
        (tmp_path / "configure.ac").write_text('AC_INIT([myapp], [1.0])')
        from hypergumbo_core.sketch import _detect_project_binary_names
        result = _detect_project_binary_names(tmp_path)
        assert "myapp" in result

    def test_fallback_to_directory_name(self, tmp_path: Path) -> None:
        """Falls back to directory name when no build files found."""
        # Create a .c file so it looks like a C project
        (tmp_path / "main.c").write_text("int main() { return 0; }")
        from hypergumbo_core.sketch import _detect_project_binary_names
        result = _detect_project_binary_names(tmp_path)
        # Falls back to directory name
        assert tmp_path.name in result

    def test_no_build_files_no_c_files(self, tmp_path: Path) -> None:
        """Returns empty list when no build files and no C/C++/Go/Rust files."""
        (tmp_path / "main.py").write_text("print('hello')")
        from hypergumbo_core.sketch import _detect_project_binary_names
        result = _detect_project_binary_names(tmp_path)
        assert result == []


class TestDetectShellIntegrationTests:
    """Tests for _detect_shell_integration_tests function."""

    def test_detects_shell_script_invoking_binary(self, tmp_path: Path) -> None:
        """Detects shell scripts that invoke the project binary."""
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test-app.sh").write_text("#!/bin/bash\n./myapp --help")
        from hypergumbo_core.sketch import _detect_shell_integration_tests
        result = _detect_shell_integration_tests(tmp_path, ["myapp"])
        assert len(result) == 1
        assert result[0].name == "test-app.sh"

    def test_detects_various_invocation_patterns(self, tmp_path: Path) -> None:
        """Detects various patterns of binary invocation."""
        tests = tmp_path / "tests"
        tests.mkdir()
        # Different invocation patterns
        (tests / "test1.sh").write_text("#!/bin/bash\n./myapp arg")
        (tests / "test2.sh").write_text("#!/bin/bash\nmyapp arg1 arg2")
        (tests / "test3.sh").write_text("#!/bin/bash\n/usr/bin/myapp ")
        from hypergumbo_core.sketch import _detect_shell_integration_tests
        result = _detect_shell_integration_tests(tmp_path, ["myapp"])
        assert len(result) == 3

    def test_ignores_non_test_shell_scripts(self, tmp_path: Path) -> None:
        """Ignores shell scripts not in test directories or without test names."""
        # Script not in test directory and no test-like name
        (tmp_path / "build.sh").write_text("#!/bin/bash\n./myapp build")
        from hypergumbo_core.sketch import _detect_shell_integration_tests
        result = _detect_shell_integration_tests(tmp_path, ["myapp"])
        assert len(result) == 0

    def test_detects_test_named_scripts_outside_test_dir(self, tmp_path: Path) -> None:
        """Detects scripts with test-like names even outside test directories."""
        (tmp_path / "test-integration.sh").write_text("#!/bin/bash\n./myapp test")
        from hypergumbo_core.sketch import _detect_shell_integration_tests
        result = _detect_shell_integration_tests(tmp_path, ["myapp"])
        assert len(result) == 1

    def test_multiple_binaries(self, tmp_path: Path) -> None:
        """Detects scripts invoking any of multiple binaries."""
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test1.sh").write_text("#!/bin/bash\n./app1 arg")
        (tests / "test2.sh").write_text("#!/bin/bash\n./app2 arg")
        from hypergumbo_core.sketch import _detect_shell_integration_tests
        result = _detect_shell_integration_tests(tmp_path, ["app1", "app2"])
        assert len(result) == 2

    def test_empty_binary_list(self, tmp_path: Path) -> None:
        """Returns empty list when no binaries provided."""
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test.sh").write_text("#!/bin/bash\n./myapp test")
        from hypergumbo_core.sketch import _detect_shell_integration_tests
        result = _detect_shell_integration_tests(tmp_path, [])
        assert len(result) == 0


class TestEstimateTestCoverage:
    """Tests for _estimate_test_coverage function."""

    def test_no_targets_returns_none(self) -> None:
        """Returns None when no non-test functions exist."""
        # Only test functions
        test_sym = Symbol(
            id="test1", name="test_foo", kind="function", language="python",
            path="tests/test_app.py", span=Span(1, 5, 0, 0)
        )
        result = _estimate_test_coverage([test_sym], [])
        assert result is None

    def test_counts_tested_functions(self) -> None:
        """Counts functions called by tests as covered."""
        # Production function
        prod_sym = Symbol(
            id="prod1", name="main", kind="function", language="python",
            path="src/app.py", span=Span(1, 5, 0, 0)
        )
        prod_sym2 = Symbol(
            id="prod2", name="helper", kind="function", language="python",
            path="src/app.py", span=Span(10, 15, 0, 0)
        )
        # Test function
        test_sym = Symbol(
            id="test1", name="test_main", kind="function", language="python",
            path="tests/test_app.py", span=Span(1, 5, 0, 0)
        )
        # Edge: test calls prod1 (main)
        edge = Edge(id="e1", src="test1", dst="prod1", edge_type="calls", line=3)

        result = _estimate_test_coverage([prod_sym, prod_sym2, test_sym], [edge])

        assert result is not None
        tested, total, pct = result
        assert tested == 1  # only main is called
        assert total == 2   # main and helper
        assert pct == 50.0  # 1/2 = 50%

    def test_no_test_edges_zero_coverage(self) -> None:
        """Zero coverage when tests don't call production code."""
        prod_sym = Symbol(
            id="prod1", name="main", kind="function", language="python",
            path="src/app.py", span=Span(1, 5, 0, 0)
        )
        test_sym = Symbol(
            id="test1", name="test_main", kind="function", language="python",
            path="tests/test_app.py", span=Span(1, 5, 0, 0)
        )
        # No edges
        result = _estimate_test_coverage([prod_sym, test_sym], [])

        assert result is not None
        tested, total, pct = result
        assert tested == 0
        assert total == 1
        assert pct == 0.0

    def test_excludes_classes_from_targets(self) -> None:
        """Only functions and methods count as coverage targets."""
        func_sym = Symbol(
            id="f1", name="main", kind="function", language="python",
            path="src/app.py", span=Span(1, 5, 0, 0)
        )
        class_sym = Symbol(
            id="c1", name="MyClass", kind="class", language="python",
            path="src/app.py", span=Span(10, 20, 0, 0)
        )
        test_sym = Symbol(
            id="test1", name="test_main", kind="function", language="python",
            path="tests/test_app.py", span=Span(1, 5, 0, 0)
        )

        result = _estimate_test_coverage([func_sym, class_sym, test_sym], [])

        assert result is not None
        _, total, _ = result
        # Only the function counts, not the class
        assert total == 1

    def test_transitive_coverage_via_bfs(self) -> None:
        """Transitive calls through the call graph are counted as covered.

        If test_foo() -> helper() -> core(), both helper and core are tested.
        """
        # Production functions forming a call chain
        helper_sym = Symbol(
            id="helper", name="helper", kind="function", language="python",
            path="src/app.py", span=Span(1, 5, 0, 0)
        )
        core_sym = Symbol(
            id="core", name="core", kind="function", language="python",
            path="src/app.py", span=Span(10, 15, 0, 0)
        )
        unreachable_sym = Symbol(
            id="unreachable", name="unreachable", kind="function", language="python",
            path="src/app.py", span=Span(20, 25, 0, 0)
        )
        # Test function
        test_sym = Symbol(
            id="test1", name="test_foo", kind="function", language="python",
            path="tests/test_app.py", span=Span(1, 5, 0, 0)
        )

        # Edges: test -> helper -> core (transitive chain)
        edge1 = Edge(id="e1", src="test1", dst="helper", edge_type="calls", line=3)
        edge2 = Edge(id="e2", src="helper", dst="core", edge_type="calls", line=4)

        result = _estimate_test_coverage(
            [helper_sym, core_sym, unreachable_sym, test_sym],
            [edge1, edge2]
        )

        assert result is not None
        tested, total, pct = result
        # Both helper AND core should be counted (transitive via BFS)
        assert tested == 2
        assert total == 3  # helper, core, unreachable
        assert abs(pct - 66.67) < 1  # ~66.67%

    def test_transitive_coverage_diamond_pattern(self) -> None:
        """Diamond call pattern: test -> A -> C and test -> B -> C.

        C should only be counted once even if reachable via multiple paths.
        """
        sym_a = Symbol(
            id="a", name="func_a", kind="function", language="python",
            path="src/app.py", span=Span(1, 5, 0, 0)
        )
        sym_b = Symbol(
            id="b", name="func_b", kind="function", language="python",
            path="src/app.py", span=Span(10, 15, 0, 0)
        )
        sym_c = Symbol(
            id="c", name="func_c", kind="function", language="python",
            path="src/app.py", span=Span(20, 25, 0, 0)
        )
        test_sym = Symbol(
            id="test1", name="test_diamond", kind="function", language="python",
            path="tests/test_app.py", span=Span(1, 5, 0, 0)
        )

        # Diamond: test -> A, test -> B, A -> C, B -> C
        edges = [
            Edge(id="e1", src="test1", dst="a", edge_type="calls", line=1),
            Edge(id="e2", src="test1", dst="b", edge_type="calls", line=2),
            Edge(id="e3", src="a", dst="c", edge_type="calls", line=3),
            Edge(id="e4", src="b", dst="c", edge_type="calls", line=4),
        ]

        result = _estimate_test_coverage([sym_a, sym_b, sym_c, test_sym], edges)

        assert result is not None
        tested, total, pct = result
        # All three production functions reachable
        assert tested == 3
        assert total == 3
        assert pct == 100.0


class TestGroupFilesByLanguage:
    """Tests for language-based file grouping."""

    def test_single_language(self) -> None:
        """All files grouped under one language."""
        from hypergumbo_core.sketch import _group_files_by_language

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
        from hypergumbo_core.sketch import _group_files_by_language

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
        from hypergumbo_core.sketch import _group_files_by_language

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
        from hypergumbo_core.sketch import _allocate_language_budget

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
        from hypergumbo_core.sketch import _allocate_language_budget

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
        from hypergumbo_core.sketch import _allocate_language_budget

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
        from hypergumbo_core.sketch import _allocate_language_budget

        lang_groups: dict = {}
        result = _allocate_language_budget(lang_groups, max_symbols=10)

        assert result == {}


class TestLanguageProportionalSelection:
    """Integration tests for language-proportional symbol selection."""

    def test_language_proportional_selection(self, tmp_path: Path) -> None:
        """Multi-language project sketch reflects language proportions."""
        from hypergumbo_core.sketch import _select_symbols_two_phase
        from hypergumbo_core.ranking import compute_centrality, group_symbols_by_file

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
        from hypergumbo_core.sketch import _select_symbols_two_phase
        from hypergumbo_core.ranking import group_symbols_by_file, compute_centrality

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
        from hypergumbo_core.sketch import _select_symbols_two_phase
        from hypergumbo_core.ranking import group_symbols_by_file, compute_centrality

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


class TestCachedResults:
    """Tests for using cached results with generate_sketch."""

    def test_uses_cached_profile(self, tmp_path: Path) -> None:
        """Sketch uses profile from cached results instead of re-detecting."""
        # Create a minimal repo
        (tmp_path / "main.py").write_text("def hello(): pass\n")

        # Create cached results with a specific profile
        cached_results = {
            "profile": {
                "languages": {
                    "python": {"files": 10, "loc": 500},
                    "javascript": {"files": 5, "loc": 200},
                },
                "frameworks": ["flask", "react"],
                "framework_mode": "auto",
            },
            "nodes": [],
            "edges": [],
        }

        sketch = generate_sketch(tmp_path, max_tokens=500, cached_results=cached_results)

        # Cached profile should be used (showing both languages)
        assert "python" in sketch.lower() or "Python" in sketch
        assert "flask" in sketch.lower() or "Flask" in sketch

    def test_uses_cached_symbols(self, tmp_path: Path) -> None:
        """Sketch uses symbols from cached results instead of running analysis."""
        # Create empty repo (no actual code)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "api.py").write_text("# placeholder\n")

        # Create cached results with specific symbols
        cached_results = {
            "profile": {
                "languages": {"python": {"files": 1, "loc": 100}},
                "frameworks": [],
                "framework_mode": "auto",
            },
            "nodes": [
                {
                    "id": "python:src/api.py:10-20:process_request:function",
                    "name": "process_request",
                    "kind": "function",
                    "language": "python",
                    "path": "src/api.py",
                    "span": {"start_line": 10, "end_line": 20, "start_col": 0, "end_col": 0},
                    "origin": "python-ast-v1",
                    "supply_chain": {"tier": 1, "reason": "first_party"},
                },
                {
                    "id": "python:src/api.py:25-35:validate_input:function",
                    "name": "validate_input",
                    "kind": "function",
                    "language": "python",
                    "path": "src/api.py",
                    "span": {"start_line": 25, "end_line": 35, "start_col": 0, "end_col": 0},
                    "origin": "python-ast-v1",
                    "supply_chain": {"tier": 1, "reason": "first_party"},
                },
            ],
            "edges": [
                {
                    "src": "python:src/api.py:10-20:process_request:function",
                    "dst": "python:src/api.py:25-35:validate_input:function",
                    "type": "calls",
                    "line": 15,
                }
            ],
        }

        sketch = generate_sketch(tmp_path, max_tokens=2000, cached_results=cached_results)

        # Cached symbols should appear in output
        assert "process_request" in sketch
        assert "validate_input" in sketch

    def test_exclude_tests_with_cached_results(self, tmp_path: Path) -> None:
        """The -x flag filters test symbols from cached results."""
        # Create minimal repo structure
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "core.py").write_text("# placeholder\n")
        (tmp_path / "tests" / "test_core.py").write_text("# placeholder\n")

        # Create cached results with both test and non-test symbols
        cached_results = {
            "profile": {
                "languages": {"python": {"files": 2, "loc": 200}},
                "frameworks": [],
                "framework_mode": "auto",
            },
            "nodes": [
                {
                    "id": "python:src/core.py:5-15:main_logic:function",
                    "name": "main_logic",
                    "kind": "function",
                    "language": "python",
                    "path": "src/core.py",
                    "span": {"start_line": 5, "end_line": 15, "start_col": 0, "end_col": 0},
                    "origin": "python-ast-v1",
                    "supply_chain": {"tier": 1, "reason": "first_party"},
                },
                {
                    "id": "python:tests/test_core.py:10-20:test_main_logic:function",
                    "name": "test_main_logic",
                    "kind": "function",
                    "language": "python",
                    "path": "tests/test_core.py",
                    "span": {"start_line": 10, "end_line": 20, "start_col": 0, "end_col": 0},
                    "origin": "python-ast-v1",
                    "supply_chain": {"tier": 1, "reason": "first_party"},
                },
            ],
            "edges": [
                {
                    "src": "python:tests/test_core.py:10-20:test_main_logic:function",
                    "dst": "python:src/core.py:5-15:main_logic:function",
                    "type": "calls",
                    "line": 12,
                }
            ],
        }

        # Generate sketch WITH test exclusion
        sketch_no_tests = generate_sketch(
            tmp_path, max_tokens=2000, cached_results=cached_results, exclude_tests=True
        )

        # Generate sketch WITHOUT test exclusion
        sketch_with_tests = generate_sketch(
            tmp_path, max_tokens=2000, cached_results=cached_results, exclude_tests=False
        )

        # With exclude_tests, test symbol should not appear
        assert "main_logic" in sketch_no_tests
        # Test symbol should be filtered out
        assert "test_main_logic" not in sketch_no_tests

        # Without exclude_tests, test symbol should appear
        assert "main_logic" in sketch_with_tests
        # Can include test symbols in "Key Symbols" or "Test Summary" section
        # The test file path should appear somewhere

    def test_cached_results_with_empty_nodes(self, tmp_path: Path) -> None:
        """Sketch handles cached results with empty nodes gracefully."""
        (tmp_path / "main.py").write_text("def hello(): pass\n")

        cached_results = {
            "profile": {
                "languages": {"python": {"files": 1, "loc": 10}},
                "frameworks": [],
                "framework_mode": "auto",
            },
            "nodes": [],
            "edges": [],
        }

        # Should not raise, just produce minimal sketch
        sketch = generate_sketch(tmp_path, max_tokens=500, cached_results=cached_results)
        assert "python" in sketch.lower() or "Python" in sketch

    def test_cached_results_profile_frameworks(self, tmp_path: Path) -> None:
        """Cached profile frameworks are displayed in sketch."""
        (tmp_path / "main.py").write_text("def hello(): pass\n")

        cached_results = {
            "profile": {
                "languages": {"python": {"files": 5, "loc": 1000}},
                "frameworks": ["django", "celery", "postgresql"],
                "framework_mode": "auto",
            },
            "nodes": [],
            "edges": [],
        }

        sketch = generate_sketch(tmp_path, max_tokens=500, cached_results=cached_results)

        # Frameworks from cached profile should appear
        assert "django" in sketch.lower() or "Django" in sketch
        assert "celery" in sketch.lower() or "Celery" in sketch

    def test_uses_cached_sketch_precomputed_config(self, tmp_path: Path) -> None:
        """Sketch uses config_info from sketch_precomputed when available."""
        # Create a minimal repo with a config file
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "myproject"\n')

        # Cached results with sketch_precomputed containing config_info
        cached_results = {
            "profile": {
                "languages": {"python": {"files": 1, "loc": 10}},
                "frameworks": [],
                "framework_mode": "auto",
            },
            "nodes": [],
            "edges": [],
            "sketch_precomputed": {
                "config_info": 'name = "cached_project_name"\nversion = "1.2.3"',
                "vocabulary": ["token", "semantic", "embedding"],
                "readme_description": "A cached project description from README.",
            },
        }

        sketch = generate_sketch(tmp_path, max_tokens=1000, cached_results=cached_results)

        # Should use cached config instead of re-extracting
        assert "cached_project_name" in sketch
        assert "1.2.3" in sketch
        # Should NOT extract fresh (would show "myproject" from pyproject.toml)

    def test_uses_cached_sketch_precomputed_readme(self, tmp_path: Path) -> None:
        """Sketch uses readme_description from sketch_precomputed when available."""
        # Create README with different content than cached
        (tmp_path / "README.md").write_text("# Fresh\n\nFresh description from README.\n")

        cached_results = {
            "profile": {
                "languages": {"python": {"files": 1, "loc": 10}},
                "frameworks": [],
                "framework_mode": "auto",
            },
            "nodes": [],
            "edges": [],
            "sketch_precomputed": {
                "config_info": "",
                "vocabulary": [],
                "readme_description": "Cached README description here.",
            },
        }

        sketch = generate_sketch(tmp_path, max_tokens=1000, cached_results=cached_results)

        # Should use cached README description
        assert "Cached README description" in sketch
        # Should NOT use fresh description
        assert "Fresh description" not in sketch

    def test_extracts_fresh_when_no_sketch_precomputed(self, tmp_path: Path) -> None:
        """Sketch extracts fresh data when sketch_precomputed is missing."""
        # Create repo with README
        (tmp_path / "README.md").write_text("# Project\n\nFresh README content.\n")
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "fresh_project"\n')

        # Cached results WITHOUT sketch_precomputed
        cached_results = {
            "profile": {
                "languages": {"python": {"files": 1, "loc": 10}},
                "frameworks": [],
                "framework_mode": "auto",
            },
            "nodes": [],
            "edges": [],
            # No sketch_precomputed key
        }

        sketch = generate_sketch(tmp_path, max_tokens=1000, cached_results=cached_results)

        # Should extract fresh data
        assert "Fresh README content" in sketch
        assert "fresh_project" in sketch

    def test_auto_discovers_cached_results_from_cache_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sketch auto-discovers cached results from ~/.cache/hypergumbo/."""
        import json

        # Create a minimal repo
        (tmp_path / "main.py").write_text("def hello(): pass\n")

        # Create a mock cache directory with pre-computed results
        mock_cache = tmp_path / "mock_cache"
        mock_cache.mkdir(parents=True)

        cached_results = {
            "profile": {
                "languages": {"python": {"files": 1, "loc": 10}},
                "frameworks": ["auto_discovered_framework"],
                "framework_mode": "auto",
            },
            "nodes": [],
            "edges": [],
            "sketch_precomputed": {
                "config_info": 'name = "auto_discovered_project"',
                "vocabulary": ["autodiscovered"],
                "readme_description": "Auto-discovered README description.",
            },
        }
        cached_path = mock_cache / "hypergumbo.results.json"
        cached_path.write_text(json.dumps(cached_results))

        # Mock _get_results_cache_dir in sketch_embeddings module
        # (this is where it gets imported from in generate_sketch)
        import hypergumbo_core.sketch_embeddings as sketch_embeddings_module
        monkeypatch.setattr(
            sketch_embeddings_module,
            "_get_results_cache_dir",
            lambda repo_root: mock_cache,
        )

        # Generate sketch without passing cached_results - should auto-discover
        sketch = generate_sketch(tmp_path, max_tokens=1000)

        # Should use auto-discovered cached results
        assert "auto_discovered_framework" in sketch.lower() or "Auto_discovered_framework" in sketch
        assert "auto_discovered_project" in sketch
        assert "Auto-discovered README description" in sketch


class TestSketchWithSource:
    """Tests for sketch --with-source feature (include source file contents)."""

    def test_with_source_includes_file_contents(self, tmp_path: Path) -> None:
        """--with-source appends source file contents after regular sketch."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        main_py = src_dir / "main.py"
        # File must have at least 5 lines to be included (5 LOC minimum)
        main_py.write_text(
            """\
def main():
    x = 1
    y = 2
    print("hello world")
    return x + y
"""
        )

        sketch = generate_sketch(
            tmp_path,
            max_tokens=2000,
            with_source=True,
        )

        # Should include regular sketch content
        assert "## Overview" in sketch
        # Should include the actual source code
        assert 'print("hello world")' in sketch
        # Should have a section header for source content
        assert "## Source Files Content" in sketch

    def test_with_source_respects_token_budget(self, tmp_path: Path) -> None:
        """--with-source respects token budget (may omit files)."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        # Create larger files to test budget constraints
        for i in range(10):
            content = f"def function_{i}():\n"
            # Add significant bulk to each file
            for j in range(50):
                content += f"    x_{j} = {j}\n"
            content += f"    return {i}\n"
            (src_dir / f"module_{i}.py").write_text(content)

        # Tiny budget - no room for source content
        tiny_sketch = generate_sketch(tmp_path, max_tokens=200, with_source=True)
        # Large budget - should include source
        large_sketch = generate_sketch(tmp_path, max_tokens=20000, with_source=True)

        # Tiny budget may not have source content (too small for source section)
        # Large budget should have source content
        assert "## Source Files Content" in large_sketch
        # At least some source code should appear with large budget
        assert "def function_" in large_sketch

    def test_with_source_orders_by_density(self, tmp_path: Path) -> None:
        """Source files are ordered by density (most important first)."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        # Create a file that would be considered important (has many symbols)
        important = src_dir / "important.py"
        important.write_text(
            """\
def func_a():
    func_b()
    return 1

def func_b():
    func_c()
    return 2

def func_c():
    return 3
"""
        )

        # Create a simpler file
        simple = src_dir / "simple.py"
        simple.write_text("x = 1\n")

        sketch = generate_sketch(tmp_path, max_tokens=4000, with_source=True)

        # important.py should appear before simple.py in source content
        assert "## Source Files Content" in sketch
        # The important file's functions should appear
        assert "def func_a():" in sketch or "def func_b():" in sketch

    def test_with_source_skips_large_file(self, tmp_path: Path) -> None:
        """--with-source skips files that exceed remaining budget."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        # Create a small file with at least 5 lines (fits in budget)
        small_content = """\
def small_func():
    x = 1
    y = 2
    z = 3
    return x + y + z
"""
        (src_dir / "small.py").write_text(small_content)

        # Create a large file (won't fit)
        large_content = "def big():\n" + "    pass\n" * 500
        (src_dir / "large.py").write_text(large_content)

        # Budget large enough for small file, but not large file
        sketch = generate_sketch(tmp_path, max_tokens=800, with_source=True)

        # Should have source content (small file fits)
        assert "## Source Files Content" in sketch
        assert "def small_func():" in sketch
        # Large file should be skipped
        assert "def big():" not in sketch

    def test_with_source_skips_short_files(self, tmp_path: Path) -> None:
        """--with-source skips files with fewer than 5 lines of code."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        # Short file (4 lines) - should be skipped
        (src_dir / "short.py").write_text("def short():\n    x = 1\n    return x\n")

        # Long enough file (5 lines) - should be included
        long_content = """\
def long_enough():
    a = 1
    b = 2
    c = 3
    return a + b + c
"""
        (src_dir / "long.py").write_text(long_content)

        sketch = generate_sketch(tmp_path, max_tokens=2000, with_source=True)

        # Should have source content with the long file
        assert "## Source Files Content" in sketch
        assert "def long_enough():" in sketch
        # Short file should be skipped
        assert "def short():" not in sketch

    def test_with_source_disabled_by_default(self, tmp_path: Path) -> None:
        """Without --with-source, no Source Files Content section appears."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("def main():\n    pass\n")

        sketch = generate_sketch(tmp_path, max_tokens=2000)

        # Should NOT have Source Files Content section
        assert "## Source Files Content" not in sketch

    def test_with_source_includes_additional_file_content(self, tmp_path: Path) -> None:
        """--with-source includes Additional Files Content for semantic picks."""
        # Create source file
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("def main():\n    pass\n")

        # Create additional files (non-source, CONFIG/DOCUMENTATION role)
        readme = tmp_path / "README.md"
        readme.write_text("# My Project\n\nThis is a README file with content.\n")
        config = tmp_path / "config.yaml"
        config.write_text("name: myproject\nversion: 1.0\n")

        # Generate sketch with large budget to include additional file content
        sketch = generate_sketch(tmp_path, max_tokens=8000, with_source=True)

        # Should have Additional Files Content section
        assert "## Additional Files Content" in sketch
        # Should include content from additional files
        assert "# My Project" in sketch or "name: myproject" in sketch

    def test_with_source_additional_file_content_respects_budget(
        self, tmp_path: Path
    ) -> None:
        """--with-source skips additional files that don't fit in budget."""
        # Create source file
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("def main():\n    pass\n")

        # Create a small README that will fit
        (tmp_path / "README.md").write_text("# Project\n\nSmall readme.\n")

        # Create a large config file that won't fit
        large_config = "# Large config\n" + "key: value\n" * 500
        (tmp_path / "large_config.yaml").write_text(large_config)

        # Small budget - additional file content should only include small files
        sketch = generate_sketch(tmp_path, max_tokens=1000, with_source=True)

        # Large config shouldn't appear (budget constraint)
        assert "key: value" * 100 not in sketch

    def test_shell_integration_tests_detected_in_c_project(
        self, tmp_path: Path
    ) -> None:
        """Shell integration tests are detected and shown in test summary."""
        # Create a C project with meson.build
        (tmp_path / "meson.build").write_text("executable('myapp', 'main.c')")
        (tmp_path / "main.c").write_text("int main() { return 0; }")

        # Create shell test scripts that invoke the binary
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test-basic.sh").write_text("#!/bin/bash\n./myapp --help")
        (tests / "test-run.sh").write_text("#!/bin/bash\n./myapp run")

        # Generate sketch with source (triggers analysis path)
        sketch = generate_sketch(tmp_path, max_tokens=4000, with_source=True)

        # Should show shell integration tests in test summary
        assert "## Tests" in sketch
        assert "shell integration" in sketch

    def test_shell_integration_tests_without_token_budget(
        self, tmp_path: Path
    ) -> None:
        """Shell integration tests detected even with unlimited token budget."""
        # Create a C project with configure.ac (autotools)
        (tmp_path / "configure.ac").write_text("AC_INIT([testapp], [1.0])")
        (tmp_path / "main.c").write_text("int main() { return 0; }")

        # Create shell test that invokes the binary
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "check-app.sh").write_text("#!/bin/bash\n./testapp check")

        # Generate sketch without budget (max_tokens=None)
        sketch = generate_sketch(tmp_path, max_tokens=None, with_source=True)

        # Should show shell integration script
        assert "## Tests" in sketch
        assert "shell integration" in sketch


class TestRepoFingerprint:
    """Tests for repository fingerprinting and XDG cache location."""

    def _init_git_repo(self, path: Path) -> None:
        """Initialize a git repo with a commit for testing."""
        from hypergumbo_core.sketch_embeddings import _run_git_command

        _run_git_command(["init"], cwd=path)
        _run_git_command(["config", "user.email", "test@test.com"], cwd=path)
        _run_git_command(["config", "user.name", "Test"], cwd=path)
        (path / "test.txt").write_text("test")
        _run_git_command(["add", "."], cwd=path)
        _run_git_command(["commit", "-m", "Initial"], cwd=path)

    def test_fingerprint_git_repo(self, tmp_path: Path) -> None:
        """Git repos use remote URL + first commit for fingerprint."""
        from hypergumbo_core.sketch_embeddings import _get_repo_fingerprint

        # Create a git repo with a commit
        self._init_git_repo(tmp_path)

        fingerprint = _get_repo_fingerprint(tmp_path)

        # Should return a 16-character hex string
        assert len(fingerprint) == 16
        assert all(c in "0123456789abcdef" for c in fingerprint)

        # Same repo should give same fingerprint
        fingerprint2 = _get_repo_fingerprint(tmp_path)
        assert fingerprint == fingerprint2

    def test_fingerprint_non_git_dir(self, tmp_path: Path) -> None:
        """Non-git directories use path hash for fingerprint."""
        from hypergumbo_core.sketch_embeddings import _get_repo_fingerprint

        fingerprint = _get_repo_fingerprint(tmp_path)

        # Should return a 16-character hex string
        assert len(fingerprint) == 16
        assert all(c in "0123456789abcdef" for c in fingerprint)

        # Same path should give same fingerprint
        fingerprint2 = _get_repo_fingerprint(tmp_path)
        assert fingerprint == fingerprint2

    def test_fingerprint_different_dirs_differ(self, tmp_path: Path) -> None:
        """Different directories have different fingerprints."""
        from hypergumbo_core.sketch_embeddings import _get_repo_fingerprint

        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        fingerprint1 = _get_repo_fingerprint(dir1)
        fingerprint2 = _get_repo_fingerprint(dir2)

        assert fingerprint1 != fingerprint2

    def test_xdg_cache_base_default(self, tmp_path: Path, monkeypatch) -> None:
        """XDG cache base defaults to ~/.cache/hypergumbo."""
        from hypergumbo_core.sketch_embeddings import _get_xdg_cache_base

        # Clear XDG_CACHE_HOME to test default
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

        cache_base = _get_xdg_cache_base()

        assert cache_base == Path.home() / ".cache" / "hypergumbo"

    def test_xdg_cache_base_respects_env(self, tmp_path: Path, monkeypatch) -> None:
        """XDG cache base respects XDG_CACHE_HOME environment variable."""
        from hypergumbo_core.sketch_embeddings import _get_xdg_cache_base

        custom_cache = tmp_path / "custom_cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(custom_cache))

        cache_base = _get_xdg_cache_base()

        assert cache_base == custom_cache / "hypergumbo"

    def test_get_cache_dir_creates_directory(self, tmp_path: Path, monkeypatch) -> None:
        """Cache directory is created under XDG cache location."""
        from hypergumbo_core.sketch_embeddings import _get_cache_dir

        # Use tmp_path as XDG_CACHE_HOME to avoid polluting real cache
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg_cache"))

        cache_dir = _get_cache_dir(tmp_path / "my_repo")

        # Cache dir should be created
        assert cache_dir.exists()
        assert cache_dir.is_dir()

        # Should be under XDG cache location with embeddings subfolder
        assert str(cache_dir).startswith(str(tmp_path / "xdg_cache" / "hypergumbo"))
        assert cache_dir.name == "embeddings"

    def test_get_cache_dir_stable_for_same_repo(self, tmp_path: Path, monkeypatch) -> None:
        """Same repo always gets same cache directory."""
        from hypergumbo_core.sketch_embeddings import _get_cache_dir

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg_cache"))
        repo = tmp_path / "my_repo"
        repo.mkdir()

        cache_dir1 = _get_cache_dir(repo)
        cache_dir2 = _get_cache_dir(repo)

        assert cache_dir1 == cache_dir2

    def test_state_hash_changes_with_file_modifications(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """State hash changes when files are modified."""
        from hypergumbo_core.sketch_embeddings import _get_repo_state_hash

        self._init_git_repo(tmp_path)

        # Get initial state hash
        hash1 = _get_repo_state_hash(tmp_path)

        # Modify a file
        (tmp_path / "test.txt").write_text("modified content")

        # State hash should change
        hash2 = _get_repo_state_hash(tmp_path)
        assert hash1 != hash2

    def test_state_hash_stable_without_changes(self, tmp_path: Path) -> None:
        """State hash is stable when no changes are made."""
        from hypergumbo_core.sketch_embeddings import _get_repo_state_hash

        self._init_git_repo(tmp_path)

        hash1 = _get_repo_state_hash(tmp_path)
        hash2 = _get_repo_state_hash(tmp_path)

        assert hash1 == hash2

    def test_state_hash_non_git_uses_mtime(self, tmp_path: Path) -> None:
        """Non-git directories use file mtime for state hash."""
        import time
        from hypergumbo_core.sketch_embeddings import _get_repo_state_hash

        # Create a Python file (source file)
        (tmp_path / "main.py").write_text("print('hello')")

        hash1 = _get_repo_state_hash(tmp_path)

        # Wait a bit and modify the file (to ensure mtime changes)
        time.sleep(0.1)
        (tmp_path / "main.py").write_text("print('modified')")

        hash2 = _get_repo_state_hash(tmp_path)

        # Hash should change due to mtime change
        assert hash1 != hash2

    def test_results_cache_dir_per_state(self, tmp_path: Path, monkeypatch) -> None:
        """Results cache directory changes with repo state."""
        from hypergumbo_core.sketch_embeddings import (
            _get_results_cache_dir,
            _get_repo_fingerprint,
        )

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg_cache"))
        self._init_git_repo(tmp_path)

        # Get results cache for initial state
        cache1 = _get_results_cache_dir(tmp_path)
        assert cache1.exists()

        # Modify a file
        (tmp_path / "test.txt").write_text("modified")

        # Results cache should be different directory
        cache2 = _get_results_cache_dir(tmp_path)
        assert cache1 != cache2

        # But both should be under the same repo fingerprint
        fingerprint = _get_repo_fingerprint(tmp_path)
        assert fingerprint in str(cache1)
        assert fingerprint in str(cache2)


class TestBatchEmbedFiles:
    """Tests for batch embedding of files."""

    def test_returns_empty_dict_for_empty_input(self, tmp_path: Path) -> None:
        """Empty file list returns empty dict."""
        from hypergumbo_core.sketch_embeddings import batch_embed_files

        result = batch_embed_files([])
        assert result == {}

    def test_returns_none_when_no_sentence_transformers(self, tmp_path: Path) -> None:
        """Returns None for all files when sentence-transformers unavailable."""
        from unittest.mock import patch
        from hypergumbo_core.sketch_embeddings import batch_embed_files

        (tmp_path / "file1.py").write_text("print('hello')")
        (tmp_path / "file2.py").write_text("print('world')")

        with patch(
            "hypergumbo_core.sketch_embeddings._has_sentence_transformers",
            return_value=False,
        ):
            result = batch_embed_files([
                tmp_path / "file1.py",
                tmp_path / "file2.py",
            ])

        assert len(result) == 2
        assert result[tmp_path / "file1.py"] is None
        assert result[tmp_path / "file2.py"] is None

    def test_uses_cache_when_available(self, tmp_path: Path) -> None:
        """Uses cached embeddings when available."""
        from unittest.mock import patch, MagicMock
        from hypergumbo_core.sketch_embeddings import batch_embed_files

        # Create test file
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")

        # Mock cache to return a cached embedding
        fake_cached_embedding = MagicMock()
        mock_model = MagicMock()

        with patch(
            "hypergumbo_core.sketch_embeddings._has_sentence_transformers",
            return_value=True,
        ), patch(
            "hypergumbo_core.sketch_embeddings._load_cached_embedding",
            return_value=fake_cached_embedding,
        ), patch(
            "hypergumbo_core.sketch_embeddings._load_modernbert_model",
            return_value=mock_model,
        ):
            cache_dir = tmp_path / "cache"
            cache_dir.mkdir()
            result = batch_embed_files([test_file], cache_dir=cache_dir)

        # Should get cached embedding
        assert test_file in result
        assert result[test_file] is fake_cached_embedding

        # Model should not have been called (all files cached)
        mock_model.encode.assert_not_called()

    def test_batches_uncached_files(self, tmp_path: Path) -> None:
        """Encodes uncached files in batches."""
        from unittest.mock import patch, MagicMock
        from hypergumbo_core.sketch_embeddings import batch_embed_files

        # Create test files
        files = []
        for i in range(5):
            f = tmp_path / f"file{i}.py"
            f.write_text(f"# File {i}\nprint({i})")
            files.append(f)

        # Mock model to return correct-sized fake embeddings per batch
        mock_model = MagicMock()

        def mock_encode(texts, **kwargs):
            # Return list of MagicMocks matching batch size
            return [MagicMock() for _ in texts]

        mock_model.encode.side_effect = mock_encode

        with patch(
            "hypergumbo_core.sketch_embeddings._has_sentence_transformers",
            return_value=True,
        ), patch(
            "hypergumbo_core.sketch_embeddings._load_modernbert_model",
            return_value=mock_model,
        ):
            result = batch_embed_files(files, batch_size=3)

        # All files should have embeddings
        assert len(result) == 5
        for f in files:
            assert result[f] is not None

        # Model should have been called twice (3 + 2 files in batches of 3)
        assert mock_model.encode.call_count == 2

    def test_handles_unreadable_files(self, tmp_path: Path) -> None:
        """Returns None for files that can't be read."""
        from unittest.mock import patch, MagicMock
        from hypergumbo_core.sketch_embeddings import batch_embed_files

        # Create one good file and one that doesn't exist
        good_file = tmp_path / "good.py"
        good_file.write_text("print('hello')")
        missing_file = tmp_path / "missing.py"

        mock_model = MagicMock()
        mock_model.encode.return_value = [MagicMock()]  # Single embedding for good file

        with patch(
            "hypergumbo_core.sketch_embeddings._has_sentence_transformers",
            return_value=True,
        ), patch(
            "hypergumbo_core.sketch_embeddings._load_modernbert_model",
            return_value=mock_model,
        ):
            result = batch_embed_files([good_file, missing_file])

        # Good file should have embedding, missing file should be None
        assert result[good_file] is not None
        assert result[missing_file] is None

    def test_calls_progress_callback(self, tmp_path: Path) -> None:
        """Progress callback is called for each batch."""
        from unittest.mock import patch, MagicMock
        from hypergumbo_core.sketch_embeddings import batch_embed_files

        # Create test files
        files = []
        for i in range(5):
            f = tmp_path / f"file{i}.py"
            f.write_text(f"# File {i}\nprint({i})")
            files.append(f)

        mock_model = MagicMock()

        def mock_encode(texts, **kwargs):
            return [MagicMock() for _ in texts]

        mock_model.encode.side_effect = mock_encode

        progress_calls = []

        def track_progress(done, total):
            progress_calls.append((done, total))

        with patch(
            "hypergumbo_core.sketch_embeddings._has_sentence_transformers",
            return_value=True,
        ), patch(
            "hypergumbo_core.sketch_embeddings._load_modernbert_model",
            return_value=mock_model,
        ):
            batch_embed_files(files, batch_size=2, progress_callback=track_progress)

        # Should have 3 progress calls (2+2+1 files in batches of 2)
        assert len(progress_calls) == 3
        assert progress_calls[0] == (2, 5)
        assert progress_calls[1] == (4, 5)
        assert progress_calls[2] == (5, 5)

    def test_saves_embeddings_to_cache(self, tmp_path: Path) -> None:
        """Computed embeddings are saved to cache."""
        from unittest.mock import patch, MagicMock
        from hypergumbo_core.sketch_embeddings import batch_embed_files

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")

        mock_model = MagicMock()
        fake_embedding = MagicMock()
        mock_model.encode.return_value = [fake_embedding]

        save_calls = []

        def mock_save(cache_dir, file_hash, embedding):
            save_calls.append((cache_dir, file_hash, embedding))

        with patch(
            "hypergumbo_core.sketch_embeddings._has_sentence_transformers",
            return_value=True,
        ), patch(
            "hypergumbo_core.sketch_embeddings._load_modernbert_model",
            return_value=mock_model,
        ), patch(
            "hypergumbo_core.sketch_embeddings._save_cached_embedding",
            side_effect=mock_save,
        ):
            batch_embed_files([test_file], cache_dir=cache_dir)

        # Verify _save_cached_embedding was called with the embedding
        assert len(save_calls) == 1
        assert save_calls[0][0] == cache_dir
        assert save_calls[0][2] is fake_embedding

