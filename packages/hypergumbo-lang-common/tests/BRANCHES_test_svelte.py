# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for svelte.py analyzer.

Tests specific branch paths in the Svelte analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Component reference extraction
- Slot extraction
- Control block extraction
- Event handler extraction
- Component import edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.svelte import (
    analyze_svelte,
    find_svelte_files,
)


def make_svelte_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Svelte component file with given content."""
    (tmp_path / name).write_text(content)


# The per-analyzer _make_symbol_id builder was folded into the shared
# make_doc_symbol_ids helper (tested in hypergumbo-core test_base.py), so the
# former TestSvelteHelperFunctions::test_make_symbol_id_format unit test was
# retired here.


class TestComponentRefExtraction:
    """Branch coverage for component reference extraction."""

    def test_component_reference(self, tmp_path: Path) -> None:
        """Test component reference extraction."""
        # Cluster F per audit-findings 0011: imports Edge with
        # meta['component_name'] replaces the dropped component_ref Symbol.
        make_svelte_file(tmp_path, "App.svelte", """
<script>
    import Button from './Button.svelte';
</script>

<Button />
""")
        result = analyze_svelte(tmp_path)
        assert not result.skipped

        edges = [e for e in result.edges if e.edge_type == "imports"]
        assert any((e.meta or {}).get("component_name") == "Button" for e in edges)

    def test_component_with_import_path(self, tmp_path: Path) -> None:
        """Test component import path is on the Edge meta."""
        # Cluster F per audit-findings 0011.
        make_svelte_file(tmp_path, "App.svelte", """
<script>
    import Header from './Header.svelte';
</script>

<Header />
""")
        result = analyze_svelte(tmp_path)
        edge = next(
            (e for e in result.edges if e.edge_type == "imports"
             and (e.meta or {}).get("component_name") == "Header"),
            None,
        )
        assert edge is not None
        assert edge.meta.get("import_path") == "./Header.svelte"

    def test_component_creates_import_edge(self, tmp_path: Path) -> None:
        """Test component reference creates import edge."""
        make_svelte_file(tmp_path, "App.svelte", """
<script>
    import Footer from './Footer.svelte';
</script>

<Footer />
""")
        result = analyze_svelte(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1


class TestSlotExtraction:
    """Branch coverage for slot extraction."""

    def test_default_slot(self, tmp_path: Path) -> None:
        """Test default slot extraction."""
        make_svelte_file(tmp_path, "Card.svelte", """
<div class="card">
    <slot></slot>
</div>
""")
        result = analyze_svelte(tmp_path)
        slots = [s for s in result.symbols if s.kind == "slot"]
        assert len(slots) >= 1
        assert any(s.name == "default" for s in slots)

    def test_named_slot(self, tmp_path: Path) -> None:
        """Test named slot extraction."""
        make_svelte_file(tmp_path, "Layout.svelte", """
<header>
    <slot name="header"></slot>
</header>
<main>
    <slot></slot>
</main>
""")
        result = analyze_svelte(tmp_path)
        slots = [s for s in result.symbols if s.kind == "slot"]
        assert len(slots) >= 2
        names = [s.name for s in slots]
        assert "header" in names
        assert "default" in names


class TestControlBlockExtraction:
    """Branch coverage for control block extraction."""

    def test_if_block(self, tmp_path: Path) -> None:
        """Test if block extraction."""
        make_svelte_file(tmp_path, "App.svelte", """
{#if condition}
    <p>Visible</p>
{/if}
""")
        result = analyze_svelte(tmp_path)
        blocks = [s for s in result.symbols if s.kind == "block"]
        assert len(blocks) >= 1
        assert any(b.meta.get("block_type") == "if" for b in blocks)

    def test_each_block(self, tmp_path: Path) -> None:
        """Test each block extraction."""
        make_svelte_file(tmp_path, "App.svelte", """
{#each items as item}
    <li>{item}</li>
{/each}
""")
        result = analyze_svelte(tmp_path)
        blocks = [s for s in result.symbols if s.kind == "block"]
        assert len(blocks) >= 1
        assert any(b.meta.get("block_type") == "each" for b in blocks)

    def test_await_block(self, tmp_path: Path) -> None:
        """Test await block extraction."""
        make_svelte_file(tmp_path, "App.svelte", """
{#await promise}
    <p>Loading...</p>
{:then data}
    <p>{data}</p>
{/await}
""")
        result = analyze_svelte(tmp_path)
        blocks = [s for s in result.symbols if s.kind == "block"]
        assert len(blocks) >= 1
        assert any(b.meta.get("block_type") == "await" for b in blocks)


class TestEventExtraction:
    """Branch coverage for event handler extraction."""

    def test_click_event(self, tmp_path: Path) -> None:
        """Test click event extraction."""
        make_svelte_file(tmp_path, "App.svelte", """
<button on:click={handleClick}>Click me</button>
""")
        result = analyze_svelte(tmp_path)
        events = [s for s in result.symbols if s.kind == "event"]
        assert len(events) >= 1
        assert any(e.name == "click" for e in events)

    def test_multiple_events(self, tmp_path: Path) -> None:
        """Test multiple events on same element."""
        make_svelte_file(tmp_path, "App.svelte", """
<input on:input={handleInput} on:focus={handleFocus} on:blur={handleBlur} />
""")
        result = analyze_svelte(tmp_path)
        events = [s for s in result.symbols if s.kind == "event"]
        names = [e.name for e in events]
        assert "input" in names
        assert "focus" in names
        assert "blur" in names


class TestFindSvelteFiles:
    """Branch coverage for file discovery."""

    def test_finds_svelte_files(self, tmp_path: Path) -> None:
        """Test .svelte files are discovered."""
        (tmp_path / "App.svelte").write_text("<h1>Hello</h1>")

        files = list(find_svelte_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".svelte"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        src = tmp_path / "src" / "components"
        src.mkdir(parents=True)
        (src / "Button.svelte").write_text("<button><slot /></button>")

        files = list(find_svelte_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "Button.svelte"


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_svelte_files(self, tmp_path: Path) -> None:
        """Test directory with no Svelte files."""
        result = analyze_svelte(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_component(self, tmp_path: Path) -> None:
        """Test minimal Svelte component."""
        make_svelte_file(tmp_path, "Min.svelte", """
<h1>Hello</h1>
""")
        result = analyze_svelte(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_svelte_file(tmp_path, "App.svelte", """
<script>
    let name = 'World';
</script>

<h1>Hello {name}!</h1>
""")
        result = analyze_svelte(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1


class TestComponentMetadata:
    """Branch coverage for component metadata extraction."""

    def test_component_with_events(self, tmp_path: Path) -> None:
        """Test component reference with event handlers."""
        # Cluster F per audit-findings 0011: events meta moved from
        # the dropped component_ref Symbol to the imports Edge.
        make_svelte_file(tmp_path, "App.svelte", """
<script>
    import Button from './Button.svelte';
</script>

<Button on:click={handleClick} />
""")
        result = analyze_svelte(tmp_path)
        edge = next(
            (e for e in result.edges if e.edge_type == "imports"
             and (e.meta or {}).get("component_name") == "Button"),
            None,
        )
        assert edge is not None
        assert "events" in edge.meta
