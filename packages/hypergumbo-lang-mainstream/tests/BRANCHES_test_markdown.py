"""Branch coverage tests for markdown.py analyzer.

Tests specific branch paths in the Markdown analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Section/heading extraction
- Code block extraction
- Link extraction
- Link edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_mainstream.markdown import (
    _make_symbol_id,
    analyze_markdown,
    find_markdown_files,
)


def make_md_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Markdown file with given content."""
    (tmp_path / name).write_text(content)


class TestMarkdownHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        from pathlib import Path
        symbol_id = _make_symbol_id(Path("docs/readme.md"), "Introduction", "section", 1)
        assert symbol_id == "markdown:docs/readme.md:section:1:Introduction"

    def test_make_symbol_id_truncates_long_names(self) -> None:
        """Test symbol ID truncates long names."""
        from pathlib import Path
        long_name = "A" * 50
        symbol_id = _make_symbol_id(Path("docs/readme.md"), long_name, "section", 1)
        assert len(symbol_id.split(":")[-1]) == 30


class TestSectionExtraction:
    """Branch coverage for section/heading extraction."""

    def test_h1_heading(self, tmp_path: Path) -> None:
        """Test H1 heading extraction."""
        make_md_file(tmp_path, "README.md", """# Project Name

This is the project description.
""")
        result = analyze_markdown(tmp_path)
        assert not result.skipped

        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) >= 1
        assert any(s.name == "Project Name" for s in sections)

    def test_multiple_heading_levels(self, tmp_path: Path) -> None:
        """Test multiple heading levels."""
        make_md_file(tmp_path, "README.md", """# Title

## Overview

### Details

#### Subdetails
""")
        result = analyze_markdown(tmp_path)
        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) >= 4

    def test_heading_metadata(self, tmp_path: Path) -> None:
        """Test heading metadata extraction."""
        make_md_file(tmp_path, "README.md", """# API Reference

## Installation Guide

## Usage Examples
""")
        result = analyze_markdown(tmp_path)
        sections = [s for s in result.symbols if s.kind == "section"]

        api_sections = [s for s in sections if "API" in s.name]
        assert len(api_sections) >= 1
        assert api_sections[0].meta is not None
        assert api_sections[0].meta.get("is_api") is True


class TestCodeBlockExtraction:
    """Branch coverage for code block extraction."""

    def test_fenced_code_block(self, tmp_path: Path) -> None:
        """Test fenced code block extraction."""
        make_md_file(tmp_path, "README.md", """# Example

```python
def hello():
    print("Hello, World!")
```
""")
        result = analyze_markdown(tmp_path)
        code_blocks = [s for s in result.symbols if s.kind == "code_block"]
        assert len(code_blocks) >= 1

    def test_code_block_language(self, tmp_path: Path) -> None:
        """Test code block language detection."""
        make_md_file(tmp_path, "README.md", """# Examples

```javascript
console.log("Hello");
```

```python
print("Hello")
```
""")
        result = analyze_markdown(tmp_path)
        code_blocks = [s for s in result.symbols if s.kind == "code_block"]
        assert len(code_blocks) >= 2

        languages = [b.meta.get("code_language") for b in code_blocks]
        assert "javascript" in languages
        assert "python" in languages

    def test_code_block_no_language(self, tmp_path: Path) -> None:
        """Test code block without language."""
        make_md_file(tmp_path, "README.md", """# Example

```
some code
```
""")
        result = analyze_markdown(tmp_path)
        code_blocks = [s for s in result.symbols if s.kind == "code_block"]
        assert len(code_blocks) >= 1


class TestLinkExtraction:
    """Branch coverage for link extraction."""

    def test_internal_link(self, tmp_path: Path) -> None:
        """Test internal link extraction."""
        make_md_file(tmp_path, "README.md", """# Docs

See [Getting Started](./docs/getting-started.md) for more info.
""")
        result = analyze_markdown(tmp_path)
        links = [s for s in result.symbols if s.kind == "link"]
        assert len(links) >= 1
        assert any(l.meta.get("is_internal") for l in links)

    def test_external_link(self, tmp_path: Path) -> None:
        """Test external link extraction."""
        make_md_file(tmp_path, "README.md", """# Links

Visit [our website](https://example.com) for more info.
""")
        result = analyze_markdown(tmp_path)
        links = [s for s in result.symbols if s.kind == "link"]
        assert len(links) >= 1
        assert any(l.meta.get("is_external") for l in links)

    def test_anchor_link(self, tmp_path: Path) -> None:
        """Test anchor link extraction."""
        make_md_file(tmp_path, "README.md", """# Table of Contents

- [Introduction](#introduction)
- [Installation](#installation)

## Introduction

Content here.

## Installation

More content.
""")
        result = analyze_markdown(tmp_path)
        links = [s for s in result.symbols if s.kind == "link"]
        anchor_links = [l for l in links if l.meta.get("is_anchor")]
        assert len(anchor_links) >= 2

    def test_link_creates_edge(self, tmp_path: Path) -> None:
        """Test internal link creates edge."""
        make_md_file(tmp_path, "README.md", """# Links

See [API docs](./api.md) for details.
""")
        result = analyze_markdown(tmp_path)
        link_edges = [e for e in result.edges if e.edge_type == "links_to"]
        assert len(link_edges) >= 1


class TestFindMarkdownFiles:
    """Branch coverage for file discovery."""

    def test_finds_md_files(self, tmp_path: Path) -> None:
        """Test .md files are discovered."""
        (tmp_path / "README.md").write_text("# Test")

        files = find_markdown_files(tmp_path)
        assert len(files) >= 1
        assert any(f.suffix == ".md" for f in files)

    def test_finds_markdown_files(self, tmp_path: Path) -> None:
        """Test .markdown files are discovered."""
        (tmp_path / "doc.markdown").write_text("# Test")

        files = find_markdown_files(tmp_path)
        assert len(files) >= 1
        assert any(f.suffix == ".markdown" for f in files)

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "guide.md").write_text("# Guide")

        files = find_markdown_files(tmp_path)
        assert len(files) >= 1


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_markdown_files(self, tmp_path: Path) -> None:
        """Test directory with no Markdown files."""
        result = analyze_markdown(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_markdown(self, tmp_path: Path) -> None:
        """Test minimal Markdown file."""
        make_md_file(tmp_path, "README.md", """# Hello
""")
        result = analyze_markdown(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_md_file(tmp_path, "README.md", """# Project

Description here.
""")
        result = analyze_markdown(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1


class TestComplexMarkdown:
    """Branch coverage for complex Markdown files."""

    def test_full_readme(self, tmp_path: Path) -> None:
        """Test complete README with all features."""
        make_md_file(tmp_path, "README.md", """# Project Name

A brief description of the project.

## Installation

```bash
pip install myproject
```

## Usage

```python
import myproject

myproject.run()
```

## API Reference

See [API docs](./docs/api.md) for details.

## Links

- [Documentation](https://docs.example.com)
- [Source Code](https://github.com/example/repo)

## License

MIT
""")
        result = analyze_markdown(tmp_path)

        # Should have sections
        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) >= 5

        # Should have code blocks
        code_blocks = [s for s in result.symbols if s.kind == "code_block"]
        assert len(code_blocks) >= 2

        # Should have links
        links = [s for s in result.symbols if s.kind == "link"]
        assert len(links) >= 3
