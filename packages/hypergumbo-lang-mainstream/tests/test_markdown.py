# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the markdown documentation analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_lang_mainstream import markdown as markdown_module
from hypergumbo_lang_mainstream.markdown import (
    _analyzer,
    analyze_markdown,
    find_markdown_files,
    is_markdown_tree_sitter_available,
)

def make_markdown_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a markdown file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path

class TestFindMarkdownFiles:
    """Tests for find_markdown_files function."""

    def test_finds_md_files(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "# Hello")
        make_markdown_file(tmp_path, "docs/guide.md", "# Guide")
        files = find_markdown_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"README.md", "guide.md"}

    def test_finds_markdown_extension(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.markdown", "# Hello")
        files = find_markdown_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "README.markdown"

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_markdown_files(tmp_path)
        assert files == []

class TestIsMarkdownTreeSitterAvailable:
    """Tests for is_markdown_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_markdown_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(markdown_module, "is_markdown_tree_sitter_available", return_value=False):
            assert markdown_module.is_markdown_tree_sitter_available() is False

class TestAnalyzeMarkdown:
    """Tests for analyze_markdown function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "# Hello")
        with patch.object(_analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="markdown analysis skipped"):
                result = markdown_module.analyze_markdown(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_h1_heading(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "# Project Title\n")
        result = analyze_markdown(tmp_path)
        assert not result.skipped
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.name == "Project Title"
        assert section.meta.get("level") == 1
        assert section.signature == "# Project Title"

    def test_extracts_h2_heading(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "## Installation\n")
        result = analyze_markdown(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.name == "Installation"
        assert section.meta.get("level") == 2
        assert section.meta.get("is_install") is True

    def test_extracts_h3_heading(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "### API Reference\n")
        result = analyze_markdown(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.name == "API Reference"
        assert section.meta.get("level") == 3
        assert section.meta.get("is_api") is True

    def test_extracts_multiple_headings(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", """# Title

## Overview

## Installation

## Usage
""")
        result = analyze_markdown(tmp_path)
        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) == 4
        names = [s.name for s in sections]
        assert "Title" in names
        assert "Overview" in names
        assert "Installation" in names
        assert "Usage" in names

    def test_extracts_code_block(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", """# Title

```javascript
const x = 1;
```
""")
        result = analyze_markdown(tmp_path)
        code = next((s for s in result.symbols if s.kind == "code_block"), None)
        assert code is not None
        assert code.name == "code:javascript"
        assert code.meta.get("code_language") == "javascript"
        assert code.meta.get("lines_of_code") >= 1
        assert code.signature == "```javascript"

    def test_extracts_code_block_without_language(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", """# Title

```
some code
```
""")
        result = analyze_markdown(tmp_path)
        code = next((s for s in result.symbols if s.kind == "code_block"), None)
        assert code is not None
        assert code.name == "code"
        assert code.meta.get("code_language") == ""
        assert code.signature == "```"

    def test_extracts_inline_link(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "See the [docs](./docs/guide.md) for more.\n")
        result = analyze_markdown(tmp_path)
        link = next((s for s in result.symbols if s.kind == "link"), None)
        assert link is not None
        assert link.name == "docs"
        assert link.meta.get("url") == "./docs/guide.md"
        assert link.meta.get("is_internal") is True

    def test_extracts_external_link(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "Visit [GitHub](https://github.com) today.\n")
        result = analyze_markdown(tmp_path)
        link = next((s for s in result.symbols if s.kind == "link"), None)
        assert link is not None
        assert link.name == "GitHub"
        assert link.meta.get("url") == "https://github.com"
        assert link.meta.get("is_external") is True

    def test_extracts_anchor_link(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "See [Installation](#installation) section.\n")
        result = analyze_markdown(tmp_path)
        link = next((s for s in result.symbols if s.kind == "link"), None)
        assert link is not None
        assert link.name == "Installation"
        assert link.meta.get("url") == "#installation"
        assert link.meta.get("is_anchor") is True

    def test_creates_edge_for_internal_link(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "See [guide](./docs/guide.md) for details.\n")
        result = analyze_markdown(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "links_to"), None)
        assert edge is not None
        # WI-diruj: dst is a properly-formed 5-part symbol id, NOT the raw URL.
        # Format: markdown:<path>:0-0:<basename>:doc_link
        assert edge.dst == "markdown:./docs/guide.md:0-0:guide.md:doc_link"

    def test_link_edge_dst_strips_anchor_fragment(self, tmp_path: Path) -> None:
        # WI-diruj: anchor fragments (#section) belong to the within-target
        # navigation, not the file identity — strip before building the dst id.
        make_markdown_file(
            tmp_path, "README.md", "See [section](../spec.md#anchor) here.\n",
        )
        result = analyze_markdown(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "links_to"), None)
        assert edge is not None
        assert edge.dst == "markdown:../spec.md:0-0:spec.md:doc_link"

    def test_link_edge_synthesizes_clean_external_node(self, tmp_path: Path) -> None:
        # WI-diruj: feed the markdown link edge through create_boundary_nodes
        # and verify the synthesized external_symbol carries language="markdown"
        # — NOT the raw URL string from the link target.
        from hypergumbo_core.ir import create_boundary_nodes

        make_markdown_file(
            tmp_path, "docs/adr/x.md", "See [agents](../../AGENTS.md) for rules.\n",
        )
        result = analyze_markdown(tmp_path)
        boundary_nodes = create_boundary_nodes(result.symbols, result.edges)
        link_externals = [
            n for n in boundary_nodes
            if (n.meta or {}).get("external_boundary") and n.language == "markdown"
        ]
        assert len(link_externals) == 1
        node = link_externals[0]
        assert node.language == "markdown"
        assert node.name == "AGENTS.md"
        # No node should carry a path-shaped string in its language field.
        assert all(
            ".md" not in (n.language or "") and "../" not in (n.language or "")
            for n in boundary_nodes
        )

    def test_no_edge_for_external_link(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "Visit [GitHub](https://github.com).\n")
        result = analyze_markdown(tmp_path)
        edges = [e for e in result.edges if e.edge_type == "links_to"]
        assert len(edges) == 0

    def test_pass_id(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "# Title\n")
        result = analyze_markdown(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.origin == "markdown-v1"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "# Title\n")
        result = analyze_markdown(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "markdown-v1"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_markdown(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        # Base class always creates a run, even with no files
        assert result.run is not None
        assert result.run.files_analyzed == 0

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "# Title\n")
        result = analyze_markdown(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.id == section.stable_id
        assert "markdown:" in section.id
        assert "README.md" in section.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "# Title\n")
        result = analyze_markdown(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.span is not None
        assert section.span.start_line >= 1

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "# Project\n")
        make_markdown_file(tmp_path, "CHANGELOG.md", "# Changelog\n")
        result = analyze_markdown(tmp_path)
        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) == 2
        names = {s.name for s in sections}
        assert names == {"Project", "Changelog"}

    def test_run_files_analyzed(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "a.md", "# A\n")
        make_markdown_file(tmp_path, "b.md", "# B\n")
        make_markdown_file(tmp_path, "c.md", "# C\n")
        result = analyze_markdown(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 3

    def test_usage_section_detected(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "## Usage Examples\n")
        result = analyze_markdown(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.meta.get("is_usage") is True

    def test_complete_readme(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", """# My Project

A cool project.

## Installation

```bash
npm install my-project
```

## Usage

```javascript
import { foo } from 'my-project';
foo();
```

## API Reference

### `foo()`

Does something.

## Links

- [Documentation](./docs/README.md)
- [GitHub](https://github.com/user/repo)
""")
        result = analyze_markdown(tmp_path)

        # Sections
        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) == 6
        section_names = {s.name for s in sections}
        assert "My Project" in section_names
        assert "Installation" in section_names
        assert "Usage" in section_names
        assert "API Reference" in section_names
        assert "Links" in section_names

        # Code blocks
        code_blocks = [s for s in result.symbols if s.kind == "code_block"]
        assert len(code_blocks) == 2
        languages = {c.meta.get("code_language") for c in code_blocks}
        assert "bash" in languages
        assert "javascript" in languages

        # Links
        links = [s for s in result.symbols if s.kind == "link"]
        assert len(links) == 2

        # Internal link edge — properly-formed dst id (WI-diruj)
        edges = [e for e in result.edges if e.edge_type == "links_to"]
        assert len(edges) == 1
        assert edges[0].dst == "markdown:./docs/README.md:0-0:README.md:doc_link"

    def test_link_truncates_long_url_in_signature(self, tmp_path: Path) -> None:
        long_url = "https://example.com/" + "x" * 50
        make_markdown_file(tmp_path, "README.md", f"See [link]({long_url}).\n")
        result = analyze_markdown(tmp_path)
        link = next((s for s in result.symbols if s.kind == "link"), None)
        assert link is not None
        assert len(link.signature) < len(long_url)
        assert "..." in link.signature

    def test_code_block_empty_content(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "```python\n```\n")
        result = analyze_markdown(tmp_path)
        code = next((s for s in result.symbols if s.kind == "code_block"), None)
        assert code is not None
        assert code.meta.get("lines_of_code") == 0
        assert code.meta.get("is_example") is False

    def test_multiple_links_in_paragraph(self, tmp_path: Path) -> None:
        """Test extracting multiple links from a single paragraph."""
        make_markdown_file(tmp_path, "README.md", "See [docs](./docs) and [api](./api.md) for more.\n")
        result = analyze_markdown(tmp_path)
        links = [s for s in result.symbols if s.kind == "link"]
        assert len(links) == 2
        names = {l.name for l in links}
        assert names == {"docs", "api"}

    def test_h6_heading(self, tmp_path: Path) -> None:
        make_markdown_file(tmp_path, "README.md", "###### Deep Section\n")
        result = analyze_markdown(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.meta.get("level") == 6

    def test_relative_link_without_prefix(self, tmp_path: Path) -> None:
        """Test link without ./ prefix but not http."""
        make_markdown_file(tmp_path, "README.md", "See [guide](docs/guide.md).\n")
        result = analyze_markdown(tmp_path)
        link = next((s for s in result.symbols if s.kind == "link"), None)
        assert link is not None
        assert link.meta.get("is_internal") is True
        assert link.meta.get("is_external") is False
