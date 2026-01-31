"""Tests for the Vue.js component analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_common import vue as vue_module
from hypergumbo_lang_common.vue import (
    VueAnalysisResult,
    analyze_vue,
    find_vue_files,
    is_vue_tree_sitter_available,
)


def make_vue_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Vue file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindVueFiles:
    """Tests for find_vue_files function."""

    def test_finds_vue_files(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", "<template></template>")
        make_vue_file(tmp_path, "components/Header.vue", "<template></template>")
        files = find_vue_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"App.vue", "Header.vue"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_vue_files(tmp_path)
        assert files == []


class TestIsVueTreeSitterAvailable:
    """Tests for is_vue_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_vue_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(vue_module, "is_vue_tree_sitter_available", return_value=False):
            assert vue_module.is_vue_tree_sitter_available() is False


class TestAnalyzeVue:
    """Tests for analyze_vue function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", "<template></template>")
        with patch.object(vue_module, "is_vue_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Vue analysis skipped"):
                result = vue_module.analyze_vue(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_vue(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_extracts_component_ref(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <Header title="Hello"/>
</template>
""")
        result = analyze_vue(tmp_path)
        assert not result.skipped
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.name == "Header"
        assert comp.signature == "<Header>"

    def test_extracts_multiple_component_refs(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <div>
    <Header/>
    <Sidebar/>
    <Footer/>
  </div>
</template>
""")
        result = analyze_vue(tmp_path)
        comps = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(comps) == 3
        names = {c.name for c in comps}
        assert names == {"Header", "Sidebar", "Footer"}

    def test_ignores_html_elements(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <div>
    <span>Hello</span>
    <button>Click</button>
  </div>
</template>
""")
        result = analyze_vue(tmp_path)
        comps = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(comps) == 0

    def test_extracts_directive_v_if(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <div v-if="show">Visible</div>
</template>
""")
        result = analyze_vue(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        assert directive.name == "v-if"
        assert directive.meta.get("element") == "div"

    def test_extracts_directive_v_for(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <li v-for="item in items" :key="item.id">{{ item.name }}</li>
</template>
""")
        result = analyze_vue(tmp_path)
        directives = [s for s in result.symbols if s.kind == "directive"]
        directive_names = {d.name for d in directives}
        assert "v-for" in directive_names
        assert "v-bind:key" in directive_names

    def test_extracts_directive_at_click(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <button @click="handleClick">Click</button>
</template>
""")
        result = analyze_vue(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        assert directive.name == "v-on:click"
        assert directive.meta.get("directive_type") == "v-on"

    def test_extracts_directive_colon_bind(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <img :src="imageUrl"/>
</template>
""")
        result = analyze_vue(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        assert directive.name == "v-bind:src"
        assert directive.meta.get("directive_type") == "v-bind"

    def test_extracts_slot_default(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "Card.vue", """<template>
  <div class="card">
    <slot></slot>
  </div>
</template>
""")
        result = analyze_vue(tmp_path)
        slot = next((s for s in result.symbols if s.kind == "slot"), None)
        assert slot is not None
        assert slot.name == "default"
        assert slot.meta.get("is_default") is True
        assert slot.signature == "<slot>"

    def test_extracts_slot_named(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "Card.vue", """<template>
  <div class="card">
    <slot name="header"></slot>
    <slot></slot>
    <slot name="footer"></slot>
  </div>
</template>
""")
        result = analyze_vue(tmp_path)
        slots = [s for s in result.symbols if s.kind == "slot"]
        assert len(slots) == 3
        slot_names = {s.name for s in slots}
        assert slot_names == {"default", "header", "footer"}

    def test_extracts_methods(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <div></div>
</template>

<script>
export default {
  methods: {
    handleClick() {
      console.log('clicked');
    },
    fetchData() {
      return fetch('/api/data');
    }
  }
}
</script>
""")
        result = analyze_vue(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 2
        method_names = {m.name for m in methods}
        assert method_names == {"handleClick", "fetchData"}

    def test_extracts_computed(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <div></div>
</template>

<script>
export default {
  computed: {
    fullName() {
      return this.firstName + ' ' + this.lastName;
    },
    isValid() {
      return this.value > 0;
    }
  }
}
</script>
""")
        result = analyze_vue(tmp_path)
        computed = [s for s in result.symbols if s.kind == "computed"]
        assert len(computed) == 2
        computed_names = {c.name for c in computed}
        assert computed_names == {"fullName", "isValid"}

    def test_extracts_props_array_syntax(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "Button.vue", """<template>
  <button></button>
</template>

<script>
export default {
  props: ['label', 'disabled', 'variant']
}
</script>
""")
        result = analyze_vue(tmp_path)
        props = [s for s in result.symbols if s.kind == "prop"]
        assert len(props) == 3
        prop_names = {p.name for p in props}
        assert prop_names == {"label", "disabled", "variant"}

    def test_extracts_props_object_syntax(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "Button.vue", """<template>
  <button></button>
</template>

<script>
export default {
  props: {
    label: String,
    disabled: Boolean,
    count: {
      type: Number,
      default: 0
    }
  }
}
</script>
""")
        result = analyze_vue(tmp_path)
        props = [s for s in result.symbols if s.kind == "prop"]
        prop_names = {p.name for p in props}
        # Should include label, disabled, count but NOT type, default
        assert "label" in prop_names
        assert "disabled" in prop_names
        assert "count" in prop_names
        assert "type" not in prop_names
        assert "default" not in prop_names

    def test_extracts_style_block(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <div></div>
</template>

<style>
.container { padding: 20px; }
</style>
""")
        result = analyze_vue(tmp_path)
        style = next((s for s in result.symbols if s.kind == "style_block"), None)
        assert style is not None
        assert style.name == "style"
        assert style.meta.get("is_scoped") is False
        assert style.meta.get("lang") == "css"

    def test_extracts_style_scoped(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <div></div>
</template>

<style scoped>
.container { padding: 20px; }
</style>
""")
        result = analyze_vue(tmp_path)
        style = next((s for s in result.symbols if s.kind == "style_block"), None)
        assert style is not None
        assert style.meta.get("is_scoped") is True
        assert "scoped" in style.signature

    def test_extracts_style_scss(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <div></div>
</template>

<style lang="scss" scoped>
$color: blue;
.container { color: $color; }
</style>
""")
        result = analyze_vue(tmp_path)
        style = next((s for s in result.symbols if s.kind == "style_block"), None)
        assert style is not None
        assert style.meta.get("lang") == "scss"
        assert style.meta.get("is_scoped") is True
        assert 'lang="scss"' in style.signature

    def test_extracts_style_module(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <div></div>
</template>

<style module>
.container { padding: 20px; }
</style>
""")
        result = analyze_vue(tmp_path)
        style = next((s for s in result.symbols if s.kind == "style_block"), None)
        assert style is not None
        assert style.meta.get("is_module") is True
        assert "module" in style.signature

    def test_creates_import_edge(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <Header/>
</template>

<script>
import Header from './Header.vue';

export default {
  components: { Header }
}
</script>
""")
        result = analyze_vue(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "imports_component"), None)
        assert edge is not None
        assert edge.dst == "./Header.vue"

    def test_component_with_import_path(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", """<template>
  <MyButton/>
</template>

<script>
import MyButton from '@/components/Button.vue';

export default {
  components: { MyButton }
}
</script>
""")
        result = analyze_vue(tmp_path)
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.meta.get("import_path") == "@/components/Button.vue"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", "<template></template>")
        result = analyze_vue(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "vue.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0
        assert result.run.files_analyzed == 1

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", "<template><Header/></template>")
        make_vue_file(tmp_path, "Header.vue", "<template><slot></slot></template>")
        result = analyze_vue(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 2

    def test_pass_id(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", "<template><Header/></template>")
        result = analyze_vue(tmp_path)
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.origin == "vue.tree_sitter"

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", "<template><Header/></template>")
        result = analyze_vue(tmp_path)
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.id == comp.stable_id
        assert "vue:" in comp.id
        assert "App.vue" in comp.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_vue_file(tmp_path, "App.vue", "<template><Header/></template>")
        result = analyze_vue(tmp_path)
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.span is not None
        assert comp.span.start_line >= 1

    def test_kebab_case_component(self, tmp_path: Path) -> None:
        """Test kebab-case component names are recognized."""
        make_vue_file(tmp_path, "App.vue", """<template>
  <my-component/>
  <custom-button/>
</template>

<script>
import MyComponent from './MyComponent.vue';
import CustomButton from './CustomButton.vue';

export default {
  components: { MyComponent, CustomButton }
}
</script>
""")
        result = analyze_vue(tmp_path)
        comps = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(comps) == 2
        names = {c.name for c in comps}
        assert names == {"my-component", "custom-button"}

    def test_complete_component(self, tmp_path: Path) -> None:
        """Test a complete Vue component with all features."""
        make_vue_file(tmp_path, "UserCard.vue", """<template>
  <div class="user-card">
    <Avatar :src="user.avatar" @error="handleError"/>
    <div v-if="showDetails">
      <h2>{{ fullName }}</h2>
      <p v-for="detail in details" :key="detail.id">{{ detail.value }}</p>
    </div>
    <slot name="actions"></slot>
    <slot></slot>
  </div>
</template>

<script>
import Avatar from './Avatar.vue';

export default {
  name: 'UserCard',
  components: { Avatar },
  props: {
    user: {
      type: Object,
      required: true
    },
    showDetails: {
      type: Boolean,
      default: false
    }
  },
  computed: {
    fullName() {
      return this.user.firstName + ' ' + this.user.lastName;
    },
    details() {
      return [{ id: 1, value: this.user.email }];
    }
  },
  methods: {
    handleError() {
      console.error('Avatar failed to load');
    }
  }
}
</script>

<style scoped lang="scss">
.user-card {
  padding: 20px;
  border: 1px solid #ccc;
}
</style>
""")
        result = analyze_vue(tmp_path)

        # Check component refs
        comps = [s for s in result.symbols if s.kind == "component_ref"]
        assert len(comps) == 1
        assert comps[0].name == "Avatar"

        # Check directives
        directives = [s for s in result.symbols if s.kind == "directive"]
        directive_names = {d.name for d in directives}
        assert "v-if" in directive_names
        assert "v-for" in directive_names
        assert "v-bind:key" in directive_names
        assert "v-bind:src" in directive_names
        assert "v-on:error" in directive_names

        # Check slots
        slots = [s for s in result.symbols if s.kind == "slot"]
        assert len(slots) == 2
        slot_names = {s.name for s in slots}
        assert slot_names == {"default", "actions"}

        # Check methods
        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        assert methods[0].name == "handleError"

        # Check computed
        computed = [s for s in result.symbols if s.kind == "computed"]
        assert len(computed) == 2
        computed_names = {c.name for c in computed}
        assert computed_names == {"fullName", "details"}

        # Check props
        props = [s for s in result.symbols if s.kind == "prop"]
        prop_names = {p.name for p in props}
        assert "user" in prop_names
        assert "showDetails" in prop_names

        # Check style
        style = next((s for s in result.symbols if s.kind == "style_block"), None)
        assert style is not None
        assert style.meta.get("is_scoped") is True
        assert style.meta.get("lang") == "scss"

        # Check edges
        edges = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(edges) == 1
        assert edges[0].dst == "./Avatar.vue"

    def test_named_import_component(self, tmp_path: Path) -> None:
        """Test named imports of components."""
        make_vue_file(tmp_path, "App.vue", """<template>
  <Button/>
</template>

<script>
import { Button } from './components/index.vue';

export default {
  components: { Button }
}
</script>
""")
        result = analyze_vue(tmp_path)
        comp = next((s for s in result.symbols if s.kind == "component_ref"), None)
        assert comp is not None
        assert comp.name == "Button"
        # Named import should also track the path
        assert comp.meta.get("import_path") == "./components/index.vue"

    def test_component_with_slot_attr(self, tmp_path: Path) -> None:
        """Test component with slot attribute."""
        make_vue_file(tmp_path, "App.vue", """<template>
  <Card>
    <Button slot="actions"/>
  </Card>
</template>
""")
        result = analyze_vue(tmp_path)
        button = next((s for s in result.symbols if s.name == "Button"), None)
        assert button is not None
        assert button.meta.get("has_slot_attr") is True
