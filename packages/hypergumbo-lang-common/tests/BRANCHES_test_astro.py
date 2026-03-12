# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for astro.py analyzer.

Tests specific branch paths in the Astro analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Component import extraction
- Frontmatter variable extraction
- Template component reference extraction
- Client directive detection
- Slot element extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.astro import (
    _make_symbol_id,
    analyze_astro,
    find_astro_files,
)


def make_astro_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an Astro file with given content."""
    (tmp_path / name).write_text(content)


class TestAstroHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self, tmp_path: Path) -> None:
        """Test symbol ID format."""
        path = tmp_path / "Component.astro"
        symbol_id = _make_symbol_id(path, "Button", "component_ref", 10)
        assert "astro:" in symbol_id
        assert "Button" in symbol_id
        assert "component_ref" in symbol_id


class TestFrontmatterImports:
    """Branch coverage for frontmatter import extraction."""

    def test_default_import(self, tmp_path: Path) -> None:
        """Test default import extraction from frontmatter."""
        make_astro_file(tmp_path, "Page.astro", """---
import Button from './Button.astro'
---
<Button />
""")
        result = analyze_astro(tmp_path)
        assert not result.skipped

        imports = [s for s in result.symbols if s.kind == "import"]
        assert len(imports) >= 1
        assert any(i.name == "Button" for i in imports)

    def test_multiple_imports(self, tmp_path: Path) -> None:
        """Test multiple imports in frontmatter."""
        make_astro_file(tmp_path, "Layout.astro", """---
import Header from './Header.astro'
import Footer from './Footer.astro'
---
<Header />
<Footer />
""")
        result = analyze_astro(tmp_path)
        imports = [s for s in result.symbols if s.kind == "import"]
        names = [i.name for i in imports]
        assert "Header" in names
        assert "Footer" in names


class TestFrontmatterVariables:
    """Branch coverage for frontmatter variable extraction."""

    def test_const_declaration(self, tmp_path: Path) -> None:
        """Test const variable extraction."""
        make_astro_file(tmp_path, "Page.astro", """---
const title = "My Page"
const count = 42
---
<h1>{title}</h1>
""")
        result = analyze_astro(tmp_path)
        variables = [s for s in result.symbols if s.kind == "variable"]
        names = [v.name for v in variables]
        assert "title" in names
        assert "count" in names

    def test_let_declaration(self, tmp_path: Path) -> None:
        """Test let variable extraction."""
        make_astro_file(tmp_path, "Counter.astro", """---
let counter = 0
---
<div>{counter}</div>
""")
        result = analyze_astro(tmp_path)
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert any(v.name == "counter" for v in variables)


class TestComponentReferences:
    """Branch coverage for template component reference extraction."""

    def test_component_ref_extraction(self, tmp_path: Path) -> None:
        """Test capitalized component reference extraction."""
        make_astro_file(tmp_path, "Page.astro", """---
import Card from './Card.astro'
---
<Card title="Hello" />
""")
        result = analyze_astro(tmp_path)
        refs = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(refs) >= 1
        assert any(r.name == "Card" for r in refs)

    def test_multiple_component_refs(self, tmp_path: Path) -> None:
        """Test multiple component references."""
        make_astro_file(tmp_path, "Page.astro", """---
import Header from './Header.astro'
import Nav from './Nav.astro'
import Footer from './Footer.astro'
---
<Header />
<Nav items={items} />
<Footer />
""")
        result = analyze_astro(tmp_path)
        refs = [s for s in result.symbols if s.kind == "component_ref"]
        names = [r.name for r in refs]
        assert "Header" in names
        assert "Nav" in names
        assert "Footer" in names


class TestClientDirectives:
    """Branch coverage for client directive detection."""

    def test_client_load_directive(self, tmp_path: Path) -> None:
        """Test client:load directive creates directive symbol."""
        make_astro_file(tmp_path, "Interactive.astro", """---
import Counter from './Counter.astro'
---
<Counter client:load />
""")
        result = analyze_astro(tmp_path)
        directives = [s for s in result.symbols if s.kind == "directive"]
        assert len(directives) >= 1
        assert any("client:load" in d.name for d in directives)

    def test_client_visible_directive(self, tmp_path: Path) -> None:
        """Test client:visible directive."""
        make_astro_file(tmp_path, "Lazy.astro", """---
import Chart from './Chart.astro'
---
<Chart client:visible />
""")
        result = analyze_astro(tmp_path)
        directives = [s for s in result.symbols if s.kind == "directive"]
        assert any("client:visible" in d.name for d in directives)


class TestSlotElements:
    """Branch coverage for slot element extraction."""

    def test_default_slot(self, tmp_path: Path) -> None:
        """Test default slot extraction."""
        make_astro_file(tmp_path, "Card.astro", """---
---
<div class="card">
    <slot />
</div>
""")
        result = analyze_astro(tmp_path)
        slots = [s for s in result.symbols if s.kind == "slot"]
        assert len(slots) >= 1
        assert any(s.name == "default" for s in slots)

    def test_named_slot(self, tmp_path: Path) -> None:
        """Test named slot extraction."""
        make_astro_file(tmp_path, "Layout.astro", """---
---
<header><slot name="header" /></header>
<main><slot /></main>
<footer><slot name="footer" /></footer>
""")
        result = analyze_astro(tmp_path)
        slots = [s for s in result.symbols if s.kind == "slot"]
        names = [s.name for s in slots]
        assert "header" in names
        assert "footer" in names


class TestImportEdges:
    """Branch coverage for import edge creation."""

    def test_import_edge_created(self, tmp_path: Path) -> None:
        """Test import edge is created for component references."""
        make_astro_file(tmp_path, "Page.astro", """---
import Widget from './Widget.astro'
---
<Widget />
""")
        result = analyze_astro(tmp_path)
        edges = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(edges) >= 1


class TestFindAstroFiles:
    """Branch coverage for file discovery."""

    def test_finds_astro_files(self, tmp_path: Path) -> None:
        """Test .astro files are discovered."""
        (tmp_path / "Component.astro").write_text("---\n---\n<div />")

        files = find_astro_files(tmp_path)
        assert len(files) == 1
        assert files[0].suffix == ".astro"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        components = tmp_path / "src" / "components"
        components.mkdir(parents=True)
        (components / "Button.astro").write_text("---\n---\n<button />")

        files = find_astro_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "Button.astro"


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_empty_frontmatter(self, tmp_path: Path) -> None:
        """Test file with empty frontmatter."""
        make_astro_file(tmp_path, "Simple.astro", """---
---
<div>Hello</div>
""")
        result = analyze_astro(tmp_path)
        assert not result.skipped

    def test_no_astro_files(self, tmp_path: Path) -> None:
        """Test directory with no Astro files."""
        result = analyze_astro(tmp_path)
        assert not result.skipped
        assert len(result.symbols) == 0


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_astro_file(tmp_path, "Page.astro", """---
const x = 1
---
<div />
""")
        result = analyze_astro(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1
