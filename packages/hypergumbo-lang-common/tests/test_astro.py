"""Tests for the Astro component analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_common import astro as astro_module
from hypergumbo_lang_common.astro import (
    AstroAnalysisResult,
    analyze_astro,
    find_astro_files,
    is_astro_tree_sitter_available,
)


def make_astro_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create an Astro file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindAstroFiles:
    """Tests for find_astro_files function."""

    def test_finds_astro_files(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", "---\n---\n<h1>Hello</h1>")
        make_astro_file(tmp_path, "components/Header.astro", "---\n---\n<header/>")
        files = find_astro_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"index.astro", "Header.astro"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_astro_files(tmp_path)
        assert files == []


class TestIsAstroTreeSitterAvailable:
    """Tests for is_astro_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_astro_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(astro_module, "is_astro_tree_sitter_available", return_value=False):
            assert astro_module.is_astro_tree_sitter_available() is False


class TestAnalyzeAstro:
    """Tests for analyze_astro function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", "---\n---\n<h1>Hello</h1>")
        with patch.object(astro_module, "is_astro_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Astro analysis skipped"):
                result = astro_module.analyze_astro(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_astro(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_extracts_component_ref(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
import Header from './Header.astro';
---

<Header/>
""")
        result = analyze_astro(tmp_path)
        assert not result.skipped
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.name == "Header"
        assert comp.signature == "<Header>"

    def test_extracts_multiple_component_refs(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
import Header from './Header.astro';
import Footer from './Footer.astro';
---

<html>
  <Header/>
  <main/>
  <Footer/>
</html>
""")
        result = analyze_astro(tmp_path)
        comps = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(comps) == 2
        names = {c.name for c in comps}
        assert names == {"Header", "Footer"}

    def test_ignores_html_elements(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
---

<div>
  <span>Hello</span>
  <button>Click</button>
</div>
""")
        result = analyze_astro(tmp_path)
        comps = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(comps) == 0

    def test_extracts_import_symbol(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
import Header from './Header.astro';
---

<Header/>
""")
        result = analyze_astro(tmp_path)
        imp = next((s for s in result.symbols if s.kind == "import"), None)
        assert imp is not None
        assert imp.name == "Header"
        assert imp.meta.get("import_path") == "./Header.astro"

    def test_extracts_variable(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
const title = 'Hello';
let count = 0;
var legacy = true;
---

<h1>{title}</h1>
""")
        result = analyze_astro(tmp_path)
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) == 3
        var_names = {v.name for v in variables}
        assert var_names == {"title", "count", "legacy"}

    def test_extracts_slot_default(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "Card.astro", """---
---

<div class="card">
  <slot/>
</div>
""")
        result = analyze_astro(tmp_path)
        slot = next((s for s in result.symbols if s.kind == "slot"), None)
        assert slot is not None
        assert slot.name == "default"
        assert slot.meta.get("is_default") is True
        assert slot.signature == "<slot>"

    def test_extracts_slot_named(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "Card.astro", """---
---

<div class="card">
  <slot name="header"/>
  <slot/>
  <slot name="footer"/>
</div>
""")
        result = analyze_astro(tmp_path)
        slots = [s for s in result.symbols if s.kind == "slot"]
        assert len(slots) == 3
        slot_names = {s.name for s in slots}
        assert slot_names == {"default", "header", "footer"}

    def test_extracts_client_directive_load(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
import Counter from './Counter.astro';
---

<Counter client:load/>
""")
        result = analyze_astro(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        assert directive.name == "client:load"
        assert directive.meta.get("element") == "Counter"
        assert directive.meta.get("directive_type") == "client"

    def test_extracts_client_directive_idle(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
import Heavy from './Heavy.astro';
---

<Heavy client:idle/>
""")
        result = analyze_astro(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        assert directive.name == "client:idle"

    def test_extracts_client_directive_visible(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
import Image from './Image.astro';
---

<Image client:visible/>
""")
        result = analyze_astro(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        assert directive.name == "client:visible"

    def test_creates_import_edge(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
import Header from './Header.astro';
---

<Header/>
""")
        result = analyze_astro(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "imports_component"), None)
        assert edge is not None
        assert edge.dst == "./Header.astro"

    def test_component_with_import_path(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
import Button from '../components/Button.astro';
---

<Button/>
""")
        result = analyze_astro(tmp_path)
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.meta.get("import_path") == "../components/Button.astro"

    def test_component_with_client_directive_in_meta(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
import Counter from './Counter.astro';
---

<Counter client:load/>
""")
        result = analyze_astro(tmp_path)
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.meta.get("client_directive") == "client:load"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", "---\n---\n<h1>Hello</h1>")
        result = analyze_astro(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "astro.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0
        assert result.run.files_analyzed == 1

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", "---\n---\n<h1>Hello</h1>")
        make_astro_file(tmp_path, "about.astro", "---\n---\n<h1>About</h1>")
        result = analyze_astro(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 2

    def test_pass_id(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
import Header from './Header.astro';
---

<Header/>
""")
        result = analyze_astro(tmp_path)
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.origin == "astro.tree_sitter"

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
import Header from './Header.astro';
---

<Header/>
""")
        result = analyze_astro(tmp_path)
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.id == comp.stable_id
        assert "astro:" in comp.id
        assert "index.astro" in comp.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
import Header from './Header.astro';
---

<Header/>
""")
        result = analyze_astro(tmp_path)
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.span is not None
        assert comp.span.start_line >= 1

    def test_component_attributes_in_meta(self, tmp_path: Path) -> None:
        make_astro_file(tmp_path, "index.astro", """---
import Card from './Card.astro';
---

<Card title="Hello" size="large"/>
""")
        result = analyze_astro(tmp_path)
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert "title" in comp.meta.get("attributes", [])
        assert "size" in comp.meta.get("attributes", [])

    def test_named_import_component(self, tmp_path: Path) -> None:
        """Test named imports of components."""
        make_astro_file(tmp_path, "index.astro", """---
import { Header, Footer } from './components.astro';
---

<Header/>
<Footer/>
""")
        result = analyze_astro(tmp_path)
        comps = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(comps) == 2
        # Named imports should populate _current_imports
        # Header and Footer should have the import path in meta
        header = next((c for c in comps if c.name == "Header"), None)
        footer = next((c for c in comps if c.name == "Footer"), None)
        assert header is not None
        assert footer is not None
        assert header.meta.get("import_path") == "./components.astro"
        assert footer.meta.get("import_path") == "./components.astro"

    def test_complete_component(self, tmp_path: Path) -> None:
        """Test a complete Astro component with all features."""
        make_astro_file(tmp_path, "Page.astro", """---
import Header from '../components/Header.astro';
import Footer from '../components/Footer.astro';
import Counter from '../components/Counter.astro';

const title = 'My Page';
const description = 'A test page';
---

<html>
  <head>
    <title>{title}</title>
  </head>
  <body>
    <Header title={title}/>
    <main>
      <slot name="content"/>
      <Counter client:load/>
      <slot/>
    </main>
    <Footer/>
  </body>
</html>
""")
        result = analyze_astro(tmp_path)

        # Check imports
        imports = [s for s in result.symbols if s.kind == "import"]
        assert len(imports) == 3
        import_names = {i.name for i in imports}
        assert import_names == {"Header", "Footer", "Counter"}

        # Check variables
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) == 2
        var_names = {v.name for v in variables}
        assert var_names == {"title", "description"}

        # Check component refs
        comps = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(comps) == 3
        comp_names = {c.name for c in comps}
        assert comp_names == {"Header", "Footer", "Counter"}

        # Check slots
        slots = [s for s in result.symbols if s.kind == "slot"]
        assert len(slots) == 2
        slot_names = {s.name for s in slots}
        assert slot_names == {"default", "content"}

        # Check directives
        directives = [s for s in result.symbols if s.kind == "directive"]
        assert len(directives) == 1
        assert directives[0].name == "client:load"

        # Check edges
        edges = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(edges) == 3
