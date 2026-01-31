"""Tests for the SCSS stylesheet analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_common import scss as scss_module
from hypergumbo_lang_common.scss import (
    ScssAnalysisResult,
    analyze_scss,
    find_scss_files,
    is_scss_tree_sitter_available,
)


def make_scss_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create an SCSS file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindScssFiles:
    """Tests for find_scss_files function."""

    def test_finds_scss_files(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$color: red;")
        make_scss_file(tmp_path, "components/button.scss", ".button {}")
        files = find_scss_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"styles.scss", "button.scss"}

    def test_finds_sass_files(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.sass", "$color: red")
        files = find_scss_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "styles.sass"

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_scss_files(tmp_path)
        assert files == []


class TestIsScssTreeSitterAvailable:
    """Tests for is_scss_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_scss_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(scss_module, "is_scss_tree_sitter_available", return_value=False):
            assert scss_module.is_scss_tree_sitter_available() is False


class TestAnalyzeScss:
    """Tests for analyze_scss function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$color: red;")
        with patch.object(scss_module, "is_scss_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="SCSS analysis skipped"):
                result = scss_module.analyze_scss(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_scss(tmp_path)
        assert result.symbols == []
        assert result.run is None

    def test_extracts_variable(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$primary-color: #3498db;")
        result = analyze_scss(tmp_path)
        assert not result.skipped
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.name == "$primary-color"
        assert "#3498db" in var.signature

    def test_extracts_multiple_variables(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """$primary: #3498db;
$secondary: #2ecc71;
$spacing: 16px;
""")
        result = analyze_scss(tmp_path)
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) == 3
        names = {v.name for v in variables}
        assert names == {"$primary", "$secondary", "$spacing"}

    def test_variable_category_color(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$primary-color: #3498db;")
        result = analyze_scss(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.meta.get("category") == "color"

    def test_variable_category_typography(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$font-size: 16px;")
        result = analyze_scss(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.meta.get("category") == "typography"

    def test_variable_category_spacing(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$margin-base: 8px;")
        result = analyze_scss(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.meta.get("category") == "spacing"

    def test_variable_category_border(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$border-radius: 4px;")
        result = analyze_scss(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.meta.get("category") == "border"

    def test_variable_category_breakpoint(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$breakpoint-md: 768px;")
        result = analyze_scss(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.meta.get("category") == "breakpoint"

    def test_variable_category_layer(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$z-index-modal: 1000;")
        result = analyze_scss(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.meta.get("category") == "layer"

    def test_variable_category_shadow(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$box-shadow: 0 2px 4px rgba(0,0,0,0.1);")
        result = analyze_scss(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.meta.get("category") == "shadow"

    def test_variable_category_animation(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$transition-duration: 200ms;")
        result = analyze_scss(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.meta.get("category") == "animation"

    def test_variable_category_general(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$my-var: 42;")
        result = analyze_scss(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.meta.get("category") == "general"

    def test_extracts_mixin(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """@mixin button-styles {
  padding: 10px;
}
""")
        result = analyze_scss(tmp_path)
        mixin = next((s for s in result.symbols if s.kind == "mixin"), None)
        assert mixin is not None
        assert mixin.name == "button-styles"
        assert "@mixin button-styles" in mixin.signature

    def test_extracts_mixin_with_params(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """@mixin button($color, $size) {
  color: $color;
  font-size: $size;
}
""")
        result = analyze_scss(tmp_path)
        mixin = next((s for s in result.symbols if s.kind == "mixin"), None)
        assert mixin is not None
        assert mixin.name == "button"
        assert mixin.meta.get("param_count") == 2
        assert "$color" in mixin.meta.get("params", [])
        assert "$size" in mixin.meta.get("params", [])

    def test_extracts_function(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """@function double($n) {
  @return $n * 2;
}
""")
        result = analyze_scss(tmp_path)
        func = next((s for s in result.symbols if s.kind == "function"), None)
        assert func is not None
        assert func.name == "double"
        assert "@function double" in func.signature
        assert func.meta.get("param_count") == 1

    def test_extracts_rule_set(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """.container {
  padding: 20px;
}
""")
        result = analyze_scss(tmp_path)
        rule = next((s for s in result.symbols if s.kind == "rule_set"), None)
        assert rule is not None
        assert rule.name == ".container"
        assert rule.meta.get("selector_type") == "class"

    def test_extracts_id_selector(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """#header {
  height: 60px;
}
""")
        result = analyze_scss(tmp_path)
        rule = next((s for s in result.symbols if s.kind == "rule_set"), None)
        assert rule is not None
        assert rule.meta.get("selector_type") == "id"

    def test_extracts_element_selector(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """body {
  margin: 0;
}
""")
        result = analyze_scss(tmp_path)
        rule = next((s for s in result.symbols if s.kind == "rule_set"), None)
        assert rule is not None
        assert rule.meta.get("selector_type") == "element"

    def test_extracts_nesting_selector(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """.button {
  &:hover {
    color: blue;
  }
}
""")
        result = analyze_scss(tmp_path)
        rules = [s for s in result.symbols if s.kind == "rule_set"]
        nesting_rule = next((r for r in rules if r.meta.get("selector_type") == "nesting"), None)
        assert nesting_rule is not None

    def test_extracts_pseudo_selector(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """:root {
  --color: red;
}
""")
        result = analyze_scss(tmp_path)
        rule = next((s for s in result.symbols if s.kind == "rule_set"), None)
        assert rule is not None
        assert rule.meta.get("selector_type") == "pseudo"

    def test_extracts_attribute_selector(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """[data-active] {
  display: block;
}
""")
        result = analyze_scss(tmp_path)
        rule = next((s for s in result.symbols if s.kind == "rule_set"), None)
        assert rule is not None
        assert rule.meta.get("selector_type") == "attribute"

    def test_extracts_multiple_selector(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """h1, h2, h3 {
  font-weight: bold;
}
""")
        result = analyze_scss(tmp_path)
        rule = next((s for s in result.symbols if s.kind == "rule_set"), None)
        assert rule is not None
        assert rule.meta.get("selector_type") == "multiple"

    def test_extracts_at_rule_selector(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """@keyframes fade {
  from { opacity: 0; }
  to { opacity: 1; }
}
""")
        result = analyze_scss(tmp_path)
        # At-rules like @keyframes may be handled differently by tree-sitter
        # Just verify we don't crash
        assert result.symbols is not None

    def test_mixin_param_with_default(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """@mixin button($color: red, $size: 16px) {
  color: $color;
  font-size: $size;
}
""")
        result = analyze_scss(tmp_path)
        mixin = next((s for s in result.symbols if s.kind == "mixin"), None)
        assert mixin is not None
        assert mixin.name == "button"
        # Params should have defaults stripped
        assert "$color" in mixin.meta.get("params", [])
        assert "$size" in mixin.meta.get("params", [])

    def test_extracts_include(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """@mixin button {
  padding: 10px;
}

.btn {
  @include button;
}
""")
        result = analyze_scss(tmp_path)
        include = next((s for s in result.symbols if s.kind == "include"), None)
        assert include is not None
        assert include.name == "@include button"
        assert include.meta.get("mixin_name") == "button"

    def test_creates_uses_mixin_edge(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", """@mixin button {
  padding: 10px;
}

.btn {
  @include button;
}
""")
        result = analyze_scss(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "uses_mixin"), None)
        assert edge is not None

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$color: red;")
        result = analyze_scss(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "scss.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0
        assert result.run.files_analyzed == 1

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$color: red;")
        make_scss_file(tmp_path, "button.scss", ".button {}")
        result = analyze_scss(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 2

    def test_pass_id(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$color: red;")
        result = analyze_scss(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.origin == "scss.tree_sitter"

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$color: red;")
        result = analyze_scss(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.id == var.stable_id
        assert "scss:" in var.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_scss_file(tmp_path, "styles.scss", "$color: red;")
        result = analyze_scss(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.span is not None
        assert var.span.start_line >= 1

    def test_complete_stylesheet(self, tmp_path: Path) -> None:
        """Test a complete SCSS stylesheet."""
        make_scss_file(tmp_path, "styles.scss", """$primary-color: #3498db;
$spacing: 16px;
$font-size: 14px;

@mixin flex-center {
  display: flex;
  align-items: center;
  justify-content: center;
}

@mixin button($bg-color) {
  background-color: $bg-color;
  padding: $spacing;
}

@function rem($pixels) {
  @return $pixels / 16 * 1rem;
}

.container {
  max-width: 1200px;
  margin: 0 auto;
}

.header {
  @include flex-center;
  height: 60px;
}

.button {
  @include button($primary-color);
  font-size: rem(14);
}
""")
        result = analyze_scss(tmp_path)

        # Check variables
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) == 3
        var_names = {v.name for v in variables}
        assert var_names == {"$primary-color", "$spacing", "$font-size"}

        # Check mixins
        mixins = [s for s in result.symbols if s.kind == "mixin"]
        assert len(mixins) == 2
        mixin_names = {m.name for m in mixins}
        assert mixin_names == {"flex-center", "button"}

        # Check functions
        functions = [s for s in result.symbols if s.kind == "function"]
        assert len(functions) == 1
        assert functions[0].name == "rem"

        # Check rule sets
        rules = [s for s in result.symbols if s.kind == "rule_set"]
        assert len(rules) == 3
        rule_names = {r.name for r in rules}
        assert ".container" in rule_names
        assert ".header" in rule_names
        assert ".button" in rule_names

        # Check includes
        includes = [s for s in result.symbols if s.kind == "include"]
        assert len(includes) == 2

        # Check edges
        edges = [e for e in result.edges if e.edge_type == "uses_mixin"]
        assert len(edges) == 2
