"""Branch coverage tests for vue.py analyzer.

Tests specific branch paths in the Vue.js analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Component reference extraction
- Directive extraction
- Slot extraction
- Props extraction
- Style block extraction
- Import edge extraction
- File discovery patterns

Note: Methods and computed properties are NOT extracted by the Vue analyzer.
The JS/TS tree-sitter analyzer handles <script> sections with full precision.
"""
from pathlib import Path

from hypergumbo_lang_common.vue import (
    _make_symbol_id,
    analyze_vue,
    find_vue_files,
)


def make_vue_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Vue component file with given content."""
    (tmp_path / name).write_text(content)


class TestVueHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        from pathlib import Path
        symbol_id = _make_symbol_id(Path("src/App.vue"), "Button", "component_ref", 10)
        assert symbol_id == "vue:src/App.vue:component_ref:10:Button"


class TestComponentRefExtraction:
    """Branch coverage for component reference extraction."""

    def test_component_reference(self, tmp_path: Path) -> None:
        """Test component reference extraction."""
        make_vue_file(tmp_path, "App.vue", """
<template>
    <Button />
</template>

<script>
import Button from './Button.vue';
export default {
    components: { Button }
}
</script>
""")
        result = analyze_vue(tmp_path)
        assert not result.skipped

        refs = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(refs) >= 1
        assert any(r.name == "Button" for r in refs)

    def test_component_with_import_path(self, tmp_path: Path) -> None:
        """Test component has import path in metadata."""
        make_vue_file(tmp_path, "App.vue", """
<template>
    <Header />
</template>

<script>
import Header from './components/Header.vue';
export default {
    components: { Header }
}
</script>
""")
        result = analyze_vue(tmp_path)
        refs = [s for s in result.symbols if s.kind == "component_ref" and s.name == "Header"]
        assert len(refs) >= 1
        assert refs[0].meta is not None
        assert refs[0].meta.get("import_path") == "./components/Header.vue"

    def test_component_creates_import_edge(self, tmp_path: Path) -> None:
        """Test component reference creates import edge."""
        make_vue_file(tmp_path, "App.vue", """
<template>
    <Footer />
</template>

<script>
import Footer from './Footer.vue';
export default {
    components: { Footer }
}
</script>
""")
        result = analyze_vue(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(import_edges) >= 1

    def test_kebab_case_component(self, tmp_path: Path) -> None:
        """Test kebab-case component reference."""
        make_vue_file(tmp_path, "App.vue", """
<template>
    <my-custom-component />
</template>

<script>
import MyCustomComponent from './MyCustomComponent.vue';
export default {
    components: { MyCustomComponent }
}
</script>
""")
        result = analyze_vue(tmp_path)
        refs = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(refs) >= 1


class TestDirectiveExtraction:
    """Branch coverage for directive extraction."""

    def test_v_if_directive(self, tmp_path: Path) -> None:
        """Test v-if directive extraction."""
        make_vue_file(tmp_path, "App.vue", """
<template>
    <div v-if="isVisible">Hello</div>
</template>
""")
        result = analyze_vue(tmp_path)
        directives = [s for s in result.symbols if s.kind == "directive"]
        assert len(directives) >= 1
        assert any("v-if" in d.name for d in directives)

    def test_v_for_directive(self, tmp_path: Path) -> None:
        """Test v-for directive extraction."""
        make_vue_file(tmp_path, "App.vue", """
<template>
    <li v-for="item in items" :key="item.id">{{ item.name }}</li>
</template>
""")
        result = analyze_vue(tmp_path)
        directives = [s for s in result.symbols if s.kind == "directive"]
        assert any("v-for" in d.name for d in directives)

    def test_shorthand_event_directive(self, tmp_path: Path) -> None:
        """Test @click shorthand directive."""
        make_vue_file(tmp_path, "App.vue", """
<template>
    <button @click="handleClick">Click me</button>
</template>
""")
        result = analyze_vue(tmp_path)
        directives = [s for s in result.symbols if s.kind == "directive"]
        assert any("v-on:click" in d.name for d in directives)

    def test_shorthand_bind_directive(self, tmp_path: Path) -> None:
        """Test :prop shorthand directive."""
        make_vue_file(tmp_path, "App.vue", """
<template>
    <img :src="imageUrl" />
</template>
""")
        result = analyze_vue(tmp_path)
        directives = [s for s in result.symbols if s.kind == "directive"]
        assert any("v-bind:" in d.name for d in directives)


class TestSlotExtraction:
    """Branch coverage for slot extraction."""

    def test_default_slot(self, tmp_path: Path) -> None:
        """Test default slot extraction."""
        make_vue_file(tmp_path, "Card.vue", """
<template>
    <div class="card">
        <slot></slot>
    </div>
</template>
""")
        result = analyze_vue(tmp_path)
        slots = [s for s in result.symbols if s.kind == "slot"]
        assert len(slots) >= 1
        assert any(s.name == "default" for s in slots)

    def test_named_slot(self, tmp_path: Path) -> None:
        """Test named slot extraction."""
        make_vue_file(tmp_path, "Layout.vue", """
<template>
    <header>
        <slot name="header"></slot>
    </header>
    <main>
        <slot></slot>
    </main>
</template>
""")
        result = analyze_vue(tmp_path)
        slots = [s for s in result.symbols if s.kind == "slot"]
        assert len(slots) >= 2
        names = [s.name for s in slots]
        assert "header" in names
        assert "default" in names


class TestNoMethodComputedExtraction:
    """Verify Vue analyzer does NOT emit method/computed symbols.

    Methods and computed properties are handled by the JS/TS tree-sitter
    analyzer which processes .vue <script> sections with full precision.
    The Vue analyzer focuses on Vue-specific constructs only.
    """

    def test_no_methods_emitted(self, tmp_path: Path) -> None:
        """Vue analyzer does not emit method symbols from methods: { ... }."""
        make_vue_file(tmp_path, "App.vue", """
<template>
    <button @click="handleClick">Click</button>
</template>

<script>
export default {
    methods: {
        handleClick() {
            console.log('clicked');
        }
    }
}
</script>
""")
        result = analyze_vue(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        assert methods == []

    def test_no_computed_emitted(self, tmp_path: Path) -> None:
        """Vue analyzer does not emit computed symbols from computed: { ... }."""
        make_vue_file(tmp_path, "App.vue", """
<template>
    <span>{{ fullName }}</span>
</template>

<script>
export default {
    data() {
        return { firstName: 'John', lastName: 'Doe' }
    },
    computed: {
        fullName() {
            return this.firstName + ' ' + this.lastName;
        }
    }
}
</script>
""")
        result = analyze_vue(tmp_path)
        computed = [s for s in result.symbols if s.kind == "computed"]
        assert computed == []


class TestPropsExtraction:
    """Branch coverage for props extraction."""

    def test_props_array_syntax(self, tmp_path: Path) -> None:
        """Test props with array syntax."""
        make_vue_file(tmp_path, "Button.vue", """
<template>
    <button>{{ label }}</button>
</template>

<script>
export default {
    props: ['label', 'disabled']
}
</script>
""")
        result = analyze_vue(tmp_path)
        props = [s for s in result.symbols if s.kind == "prop"]
        assert len(props) >= 2
        names = [p.name for p in props]
        assert "label" in names
        assert "disabled" in names

    def test_props_object_syntax(self, tmp_path: Path) -> None:
        """Test props with object syntax."""
        make_vue_file(tmp_path, "Input.vue", """
<template>
    <input :value="value" />
</template>

<script>
export default {
    props: {
        value: String,
        placeholder: {
            type: String,
            default: ''
        }
    }
}
</script>
""")
        result = analyze_vue(tmp_path)
        props = [s for s in result.symbols if s.kind == "prop"]
        assert len(props) >= 2


class TestStyleExtraction:
    """Branch coverage for style block extraction."""

    def test_simple_style(self, tmp_path: Path) -> None:
        """Test style block extraction."""
        make_vue_file(tmp_path, "App.vue", """
<template>
    <div class="container"></div>
</template>

<style>
.container { padding: 10px; }
</style>
""")
        result = analyze_vue(tmp_path)
        styles = [s for s in result.symbols if s.kind == "style_block"]
        assert len(styles) >= 1

    def test_scoped_style(self, tmp_path: Path) -> None:
        """Test scoped style block."""
        make_vue_file(tmp_path, "App.vue", """
<template>
    <div class="box"></div>
</template>

<style scoped>
.box { border: 1px solid; }
</style>
""")
        result = analyze_vue(tmp_path)
        styles = [s for s in result.symbols if s.kind == "style_block"]
        assert len(styles) >= 1
        assert any(s.meta.get("is_scoped") for s in styles)

    def test_style_with_lang(self, tmp_path: Path) -> None:
        """Test style block with preprocessor."""
        make_vue_file(tmp_path, "App.vue", """
<template>
    <div></div>
</template>

<style lang="scss">
$color: red;
.box { color: $color; }
</style>
""")
        result = analyze_vue(tmp_path)
        styles = [s for s in result.symbols if s.kind == "style_block"]
        assert len(styles) >= 1
        assert any(s.meta.get("lang") == "scss" for s in styles)


class TestFindVueFiles:
    """Branch coverage for file discovery."""

    def test_finds_vue_files(self, tmp_path: Path) -> None:
        """Test .vue files are discovered."""
        (tmp_path / "App.vue").write_text("<template><div></div></template>")

        files = list(find_vue_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".vue"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        components = tmp_path / "src" / "components"
        components.mkdir(parents=True)
        (components / "Button.vue").write_text("<template><button></button></template>")

        files = list(find_vue_files(tmp_path))
        assert len(files) == 1


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_vue_files(self, tmp_path: Path) -> None:
        """Test directory with no Vue files."""
        result = analyze_vue(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_component(self, tmp_path: Path) -> None:
        """Test minimal Vue component."""
        make_vue_file(tmp_path, "Min.vue", """
<template>
    <h1>Hello</h1>
</template>
""")
        result = analyze_vue(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_vue_file(tmp_path, "App.vue", """
<template>
    <div>Hello</div>
</template>
""")
        result = analyze_vue(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1
