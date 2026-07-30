# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Svelte component analyzer."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_lang_common import svelte as svelte_module
from hypergumbo_lang_common.svelte import analyze_svelte, find_svelte_files, is_svelte_tree_sitter_available

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
        with patch.object(svelte_module._analyzer, "_check_grammar_available", return_value=False):
            assert svelte_module.is_svelte_tree_sitter_available() is False

class TestAnalyzeSvelte:
    """Tests for analyze_svelte function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", "<h1>Hello</h1>")
        with patch.object(svelte_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="svelte analysis skipped"):
                result = svelte_module.analyze_svelte(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_component_ref(self, tmp_path: Path) -> None:
        # Cluster F per audit-findings 0011: component_ref Symbol dropped;
        # imports Edge with meta['component_name'] carries the relationship.
        make_svelte_file(tmp_path, "App.svelte", """<script>
  import Header from './Header.svelte';
</script>
<Header />
""")
        result = analyze_svelte(tmp_path)
        assert not result.skipped
        edge = next(
            (e for e in result.edges if e.edge_type == "imports"
             and (e.meta or {}).get("component_name") == "Header"),
            None,
        )
        assert edge is not None
        assert edge.meta.get("import_path") == "./Header.svelte"

    def test_creates_imports_edge(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", """<script>
  import Button from './Button.svelte';
</script>
<Button />
""")
        result = analyze_svelte(tmp_path)
        edge = next(
            (e for e in result.edges if e.edge_type == "imports"
             and (e.meta or {}).get("component_name") == "Button"),
            None,
        )
        assert edge is not None
        assert edge.dst == "./Button.svelte"
        # Cluster F per audit-findings 0011: edge src is now the file id.
        assert edge.src == "svelte:App.svelte:1-1:file:file"

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
        # Cluster F per audit-findings 0011: lowercase HTML elements never
        # produce imports Edges (component refs require capitalized tags).
        edges = [e for e in result.edges if e.edge_type == "imports"]
        assert edges == []

    def test_ignores_svg_elements(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "Icon.svelte", """<svg width="100" height="100">
  <circle cx="50" cy="50" r="40" />
  <path d="M10 10" />
</svg>
""")
        result = analyze_svelte(tmp_path)
        edges = [e for e in result.edges if e.edge_type == "imports"]
        assert edges == []

    def test_pass_id(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", "<slot />")
        result = analyze_svelte(tmp_path)
        slot = next((s for s in result.symbols if s.kind == "slot"), None)
        assert slot is not None
        assert slot.origin == ["svelte"]

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_svelte_file(tmp_path, "App.svelte", "<h1>Hello</h1>")
        result = analyze_svelte(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "svelte"
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
        # INV-dulah: node.id and stable_id are minted together by
        # make_doc_symbol_ids; node.id is the canonical ADR-0036
        # "{lang}:{path}:{start}-{end}:{name}:{kind}" (was the doc-family
        # kind-third/name-last order, which put the kind word in the span slot).
        # Parsed RIGHT-anchored, the way the canonical parser does
        # (span, name, kind = parts[-3:]), so a colon in the path cannot shift it.
        _head, _span, _name, _kind = slot.id.rsplit(":", 3)
        assert _head == "svelte:App.svelte", slot.id
        assert re.match(r"^\d+-\d+$", _span), slot.id
        assert _kind == slot.kind, slot.id
        assert re.match(r"^sha256:[0-9a-f]{16}$", slot.stable_id)

    def test_all_symbols_have_canonical_stable_id(self, tmp_path: Path) -> None:
        """Every emitted Symbol carries a canonical sha256 stable_id (WI-rijup)."""
        # Reuse the complete-component fixture (test_complete_component) which
        # yields slot + block + event symbols (>= 1).
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
        assert len(result.symbols) >= 1
        canonical = re.compile(r"^sha256:[0-9a-f]{16}$")
        for symbol in result.symbols:
            assert canonical.match(symbol.stable_id), (
                f"{symbol.kind} {symbol.name} has non-canonical stable_id: {symbol.stable_id}"
            )

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
        # Cluster F per audit-findings 0011: unresolved component refs now
        # produce an imports Edge with a 5-part dangling component dst, so
        # the relationship keeps representation in the graph.
        make_svelte_file(tmp_path, "App.svelte", """<MyComponent />
""")
        result = analyze_svelte(tmp_path)
        edge = next(
            (e for e in result.edges if e.edge_type == "imports"
             and (e.meta or {}).get("component_name") == "MyComponent"),
            None,
        )
        assert edge is not None
        assert edge.meta.get("import_path") == ""
        assert edge.dst == "svelte:component:MyComponent:0-0:MyComponent:component"

    def test_component_with_events_and_slot(self, tmp_path: Path) -> None:
        # Cluster F per audit-findings 0011: events / has_slot_attr meta
        # moved from the dropped component_ref Symbol to the imports Edge.
        make_svelte_file(tmp_path, "App.svelte", """<script>
  import Card from './Card.svelte';
</script>
<Card on:click={handleClick} slot="content" />
""")
        result = analyze_svelte(tmp_path)
        edge = next(
            (e for e in result.edges if e.edge_type == "imports"
             and (e.meta or {}).get("component_name") == "Card"),
            None,
        )
        assert edge is not None
        assert "click" in edge.meta.get("events", [])
        assert edge.meta.get("has_slot_attr") is True

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

        # Cluster F per audit-findings 0011: component refs ride imports
        # Edge meta['component_name'] (Symbol dropped).
        comp_edges = [
            e for e in result.edges if e.edge_type == "imports"
            and (e.meta or {}).get("component_name") is not None
        ]
        assert len(comp_edges) == 2
        comp_names = {(e.meta or {}).get("component_name") for e in comp_edges}
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
        edges = [e for e in result.edges if e.edge_type == "imports"]
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
        # Cluster F per audit-findings 0011: import_path lives on Edge meta.
        comp_edges = [
            e for e in result.edges if e.edge_type == "imports"
            and (e.meta or {}).get("component_name") is not None
        ]
        assert len(comp_edges) == 2
        button_edge = next(
            (e for e in comp_edges if (e.meta or {}).get("component_name") == "Button"),
            None,
        )
        assert button_edge is not None
        assert button_edge.meta.get("import_path") == "./components/index.svelte"

    def test_non_svelte_import(self, tmp_path: Path) -> None:
        """Test that non-.svelte imports don't create edges."""
        make_svelte_file(tmp_path, "App.svelte", """<script>
  import { writable } from 'svelte/store';
  import utils from './utils.js';
</script>
<h1>Hello</h1>
""")
        result = analyze_svelte(tmp_path)
        edges = [e for e in result.edges if e.edge_type == "imports"]
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
        # Cluster F per audit-findings 0011: assert via imports Edge meta.
        make_svelte_file(tmp_path, "App.svelte", """<script>
  import Icon from './Icon.svelte';
</script>
<Icon name="check" />
""")
        result = analyze_svelte(tmp_path)
        edge = next(
            (e for e in result.edges if e.edge_type == "imports"
             and (e.meta or {}).get("component_name") == "Icon"),
            None,
        )
        assert edge is not None

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
