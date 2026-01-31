"""Tests for the Svelte component analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_common import svelte as svelte_module
from hypergumbo_lang_common.svelte import (
    SvelteAnalysisResult,
    analyze_svelte,
    find_svelte_files,
    is_svelte_tree_sitter_available,
)


def make_svelte_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Svelte file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindSvelteFiles:
    """Tests for find_svelte_files function."""

    def test_finds_svelte_files(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", "<h1>Hello</h1>")
        make_svelte_file(tmp_path, "src/Header.svelte", "<header>Header</header>")
        files = find_svelte_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"App.svelte", "Header.svelte"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_svelte_files(tmp_path)
        assert files == []


class TestIsSvelteTreeSitterAvailable:
    """Tests for is_svelte_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_svelte_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(svelte_module, "is_svelte_tree_sitter_available", return_value=False):
            assert svelte_module.is_svelte_tree_sitter_available() is False


class TestAnalyzeSvelte:
    """Tests for analyze_svelte function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", "<h1>Hello</h1>")
        with patch.object(svelte_module, "is_svelte_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Svelte analysis skipped"):
                result = svelte_module.analyze_svelte(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_component_ref(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", """<script>
  import Header from './Header.svelte';
</script>
<Header />
""")
        result = analyze_svelte(tmp_path)
        assert not result.skipped
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.name == "Header"
        assert comp.language == "svelte"
        assert comp.meta.get("import_path") == "./Header.svelte"

    def test_creates_imports_edge(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", """<script>
  import Button from './Button.svelte';
</script>
<Button />
""")
        result = analyze_svelte(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "imports_component"), None)
        assert edge is not None
        assert edge.dst == "./Button.svelte"
        assert edge.edge_type == "imports_component"

    def test_extracts_default_slot(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "Card.svelte", """<div class="card">
  <slot />
</div>
""")
        result = analyze_svelte(tmp_path)
        slot = next((s for s in result.symbols if s.kind == "slot"), None)
        assert slot is not None
        assert slot.name == "default"
        assert slot.meta.get("is_default") is True
        assert slot.signature == "<slot>"

    def test_extracts_named_slot(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "Layout.svelte", """<div>
  <slot name="header" />
  <slot />
  <slot name="footer" />
</div>
""")
        result = analyze_svelte(tmp_path)
        slots = [s for s in result.symbols if s.kind == "slot"]
        assert len(slots) == 3
        names = {s.name for s in slots}
        assert names == {"default", "header", "footer"}

        header_slot = next((s for s in slots if s.name == "header"), None)
        assert header_slot is not None
        assert header_slot.meta.get("is_default") is False
        assert 'name="header"' in header_slot.signature

    def test_extracts_event_handler(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "Button.svelte", """<button on:click={handleClick}>
  Click me
</button>
""")
        result = analyze_svelte(tmp_path)
        event = next((s for s in result.symbols if s.kind == "event"), None)
        assert event is not None
        assert event.name == "click"
        assert event.signature == "on:click"
        assert event.meta.get("element") == "button"

    def test_extracts_multiple_events(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "Input.svelte", """<input on:input={handleInput} on:focus={handleFocus} on:blur={handleBlur}>
""")
        result = analyze_svelte(tmp_path)
        events = [s for s in result.symbols if s.kind == "event"]
        assert len(events) == 3
        event_names = {e.name for e in events}
        assert event_names == {"input", "focus", "blur"}

    def test_extracts_if_block(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "Conditional.svelte", """{#if visible}
  <p>Visible</p>
{/if}
""")
        result = analyze_svelte(tmp_path)
        block = next((s for s in result.symbols if s.kind == "block"), None)
        assert block is not None
        assert block.name == "#if"
        assert block.meta.get("block_type") == "if"
        assert "visible" in block.meta.get("expression", "")

    def test_extracts_each_block(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "List.svelte", """{#each items as item}
  <li>{item}</li>
{/each}
""")
        result = analyze_svelte(tmp_path)
        block = next((s for s in result.symbols if s.kind == "block"), None)
        assert block is not None
        assert block.name == "#each"
        assert block.meta.get("block_type") == "each"
        assert "items" in block.meta.get("expression", "")

    def test_extracts_await_block(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "Async.svelte", """{#await promise}
  <p>Loading...</p>
{:then data}
  <p>{data}</p>
{/await}
""")
        result = analyze_svelte(tmp_path)
        block = next((s for s in result.symbols if s.kind == "block"), None)
        assert block is not None
        assert block.name == "#await"
        assert block.meta.get("block_type") == "await"

    def test_ignores_html_elements(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "Page.svelte", """<div>
  <h1>Title</h1>
  <p>Content</p>
  <span>Text</span>
</div>
""")
        result = analyze_svelte(tmp_path)
        comp_refs = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(comp_refs) == 0

    def test_ignores_svg_elements(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "Icon.svelte", """<svg width="100" height="100">
  <circle cx="50" cy="50" r="40" />
  <path d="M10 10" />
</svg>
""")
        result = analyze_svelte(tmp_path)
        comp_refs = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(comp_refs) == 0

    def test_pass_id(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", "<slot />")
        result = analyze_svelte(tmp_path)
        slot = next((s for s in result.symbols if s.kind == "slot"), None)
        assert slot is not None
        assert slot.origin == "svelte.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", "<h1>Hello</h1>")
        result = analyze_svelte(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "svelte.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_svelte(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", "<slot />")
        result = analyze_svelte(tmp_path)
        slot = next((s for s in result.symbols if s.kind == "slot"), None)
        assert slot is not None
        assert slot.id == slot.stable_id
        assert "svelte:" in slot.id
        assert "App.svelte" in slot.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", "<slot />")
        result = analyze_svelte(tmp_path)
        slot = next((s for s in result.symbols if s.kind == "slot"), None)
        assert slot is not None
        assert slot.span is not None
        assert slot.span.start_line >= 1

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", "<slot name=\"main\" />")
        make_svelte_file(tmp_path, "Header.svelte", "<slot name=\"title\" />")
        result = analyze_svelte(tmp_path)
        slots = [s for s in result.symbols if s.kind == "slot"]
        assert len(slots) == 2
        names = {s.name for s in slots}
        assert names == {"main", "title"}

    def test_run_files_analyzed(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "A.svelte", "<h1>A</h1>")
        make_svelte_file(tmp_path, "B.svelte", "<h1>B</h1>")
        make_svelte_file(tmp_path, "C.svelte", "<h1>C</h1>")
        result = analyze_svelte(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 3

    def test_component_ref_without_import(self, tmp_path: Path) -> None:
        """Test component reference without import (globally registered)."""
        make_svelte_file(tmp_path, "App.svelte", """<MyComponent />
""")
        result = analyze_svelte(tmp_path)
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.name == "MyComponent"
        assert comp.meta.get("import_path") == ""
        # No edge created for unimported component
        edges = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(edges) == 0

    def test_component_with_events_and_slot(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", """<script>
  import Card from './Card.svelte';
</script>
<Card on:click={handleClick} slot="content" />
""")
        result = analyze_svelte(tmp_path)
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.name == "Card"
        assert "click" in comp.meta.get("events", [])
        assert comp.meta.get("has_slot_attr") is True

    def test_complete_component(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", """<script>
  import Header from './Header.svelte';
  import Footer from './Footer.svelte';

  let count = 0;
  let items = [1, 2, 3];
</script>

<Header title="My App" />

<main>
  <slot />

  {#if count > 0}
    <p>Count: {count}</p>
  {/if}

  {#each items as item}
    <li on:click={handleClick}>{item}</li>
  {/each}
</main>

<Footer />
""")
        result = analyze_svelte(tmp_path)

        # Components
        comp_refs = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(comp_refs) == 2
        comp_names = {c.name for c in comp_refs}
        assert comp_names == {"Header", "Footer"}

        # Slot
        slots = [s for s in result.symbols if s.kind == "slot"]
        assert len(slots) == 1
        assert slots[0].name == "default"

        # Control blocks
        blocks = [s for s in result.symbols if s.kind == "block"]
        assert len(blocks) == 2
        block_types = {b.meta.get("block_type") for b in blocks}
        assert block_types == {"if", "each"}

        # Events
        events = [s for s in result.symbols if s.kind == "event"]
        assert len(events) == 1
        assert events[0].name == "click"

        # Import edges
        edges = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(edges) == 2

    def test_named_import_component(self, tmp_path: Path) -> None:
        """Test named import of components."""
        make_svelte_file(tmp_path, "App.svelte", """<script>
  import { Button, Card } from './components/index.svelte';
</script>
<Button />
<Card />
""")
        result = analyze_svelte(tmp_path)
        comp_refs = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(comp_refs) == 2
        # Named imports to .svelte file should track import path
        button = next((c for c in comp_refs if c.name == "Button"), None)
        assert button is not None
        assert button.meta.get("import_path") == "./components/index.svelte"

    def test_non_svelte_import(self, tmp_path: Path) -> None:
        """Test that non-.svelte imports don't create edges."""
        make_svelte_file(tmp_path, "App.svelte", """<script>
  import { writable } from 'svelte/store';
  import utils from './utils.js';
</script>
<h1>Hello</h1>
""")
        result = analyze_svelte(tmp_path)
        edges = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(edges) == 0

    def test_nested_control_blocks(self, tmp_path: Path) -> None:
        """Test nested control flow blocks."""
        make_svelte_file(tmp_path, "Nested.svelte", """{#if condition}
  {#each items as item}
    <p>{item}</p>
  {/each}
{/if}
""")
        result = analyze_svelte(tmp_path)
        blocks = [s for s in result.symbols if s.kind == "block"]
        # Should find both blocks
        assert len(blocks) == 2
        block_types = {b.meta.get("block_type") for b in blocks}
        assert block_types == {"if", "each"}

    def test_block_nested_elements_count(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "List.svelte", """{#each items as item}
  <li>{item.name}</li>
{/each}
""")
        result = analyze_svelte(tmp_path)
        block = next((s for s in result.symbols if s.kind == "block"), None)
        assert block is not None
        assert block.meta.get("nested_elements") >= 1

    def test_self_closing_component(self, tmp_path: Path) -> None:
        """Test self-closing component syntax."""
        make_svelte_file(tmp_path, "App.svelte", """<script>
  import Icon from './Icon.svelte';
</script>
<Icon name="check" />
""")
        result = analyze_svelte(tmp_path)
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.name == "Icon"
        assert comp.signature == "<Icon>"

    def test_element_with_multiple_events(self, tmp_path: Path) -> None:
        """Test element with multiple event handlers."""
        make_svelte_file(tmp_path, "Form.svelte", """<form on:submit={handleSubmit} on:reset={handleReset}>
  <input on:input={handleInput} on:change={handleChange} on:focus={handleFocus} />
</form>
""")
        result = analyze_svelte(tmp_path)
        events = [s for s in result.symbols if s.kind == "event"]
        # Should have events from both form and input
        assert len(events) == 5
        event_names = {e.name for e in events}
        assert "submit" in event_names
        assert "reset" in event_names
        assert "input" in event_names
        assert "change" in event_names
        assert "focus" in event_names
