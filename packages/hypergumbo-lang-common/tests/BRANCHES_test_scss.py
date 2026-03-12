# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for scss.py analyzer.

Tests specific branch paths in the SCSS analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Variable extraction and categorization
- Mixin extraction
- Function extraction
- Rule set extraction
- Include edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.scss import (
    _make_symbol_id,
    analyze_scss,
    find_scss_files,
)


def make_scss_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an SCSS file with given content."""
    (tmp_path / name).write_text(content)


class TestScssHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        from pathlib import Path
        symbol_id = _make_symbol_id(Path("styles/main.scss"), "$primary-color", "variable", 1)
        assert symbol_id == "scss:styles/main.scss:variable:1:$primary-color"


class TestVariableExtraction:
    """Branch coverage for variable extraction."""

    def test_simple_variable(self, tmp_path: Path) -> None:
        """Test simple variable extraction."""
        make_scss_file(tmp_path, "vars.scss", """
$primary-color: #3498db;
$secondary-color: #2ecc71;
""")
        result = analyze_scss(tmp_path)
        assert not result.skipped

        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) >= 2
        names = [v.name for v in variables]
        assert "$primary-color" in names
        assert "$secondary-color" in names

    def test_variable_categorization_color(self, tmp_path: Path) -> None:
        """Test variable categorization for color variables."""
        make_scss_file(tmp_path, "vars.scss", """
$background-color: #ffffff;
$text-color: #333333;
""")
        result = analyze_scss(tmp_path)
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) >= 2
        # Check that color variables are categorized
        for var in variables:
            assert var.meta is not None
            if "color" in var.name.lower():
                assert var.meta.get("category") == "color"

    def test_variable_categorization_typography(self, tmp_path: Path) -> None:
        """Test variable categorization for typography variables."""
        make_scss_file(tmp_path, "vars.scss", """
$font-family: 'Arial', sans-serif;
$text-size: 16px;
""")
        result = analyze_scss(tmp_path)
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) >= 1

    def test_variable_categorization_spacing(self, tmp_path: Path) -> None:
        """Test variable categorization for spacing variables."""
        make_scss_file(tmp_path, "vars.scss", """
$spacing-sm: 8px;
$margin-base: 16px;
""")
        result = analyze_scss(tmp_path)
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) >= 2


class TestMixinExtraction:
    """Branch coverage for mixin extraction."""

    def test_simple_mixin(self, tmp_path: Path) -> None:
        """Test simple mixin extraction."""
        make_scss_file(tmp_path, "mixins.scss", """
@mixin center {
    display: flex;
    justify-content: center;
    align-items: center;
}
""")
        result = analyze_scss(tmp_path)
        mixins = [s for s in result.symbols if s.kind == "mixin"]
        assert len(mixins) >= 1
        assert any(m.name == "center" for m in mixins)

    def test_mixin_with_params(self, tmp_path: Path) -> None:
        """Test mixin with parameters."""
        make_scss_file(tmp_path, "mixins.scss", """
@mixin button($bg-color, $text-color) {
    background: $bg-color;
    color: $text-color;
}
""")
        result = analyze_scss(tmp_path)
        mixins = [s for s in result.symbols if s.kind == "mixin" and s.name == "button"]
        assert len(mixins) >= 1
        assert mixins[0].meta is not None
        assert mixins[0].meta.get("param_count") >= 2


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_simple_function(self, tmp_path: Path) -> None:
        """Test simple SCSS function extraction."""
        make_scss_file(tmp_path, "functions.scss", """
@function double($n) {
    @return $n * 2;
}
""")
        result = analyze_scss(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]
        assert len(functions) >= 1
        assert any(f.name == "double" for f in functions)

    def test_function_with_params(self, tmp_path: Path) -> None:
        """Test function with multiple parameters."""
        make_scss_file(tmp_path, "functions.scss", """
@function clamp-value($min, $value, $max) {
    @return min(max($value, $min), $max);
}
""")
        result = analyze_scss(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]
        assert len(functions) >= 1


class TestRuleSetExtraction:
    """Branch coverage for rule set extraction."""

    def test_class_selector(self, tmp_path: Path) -> None:
        """Test class selector extraction."""
        make_scss_file(tmp_path, "styles.scss", """
.button {
    padding: 10px;
}
""")
        result = analyze_scss(tmp_path)
        rules = [s for s in result.symbols if s.kind == "rule_set"]
        assert len(rules) >= 1
        assert any(r.meta.get("selector_type") == "class" for r in rules)

    def test_id_selector(self, tmp_path: Path) -> None:
        """Test ID selector extraction."""
        make_scss_file(tmp_path, "styles.scss", """
#header {
    background: #333;
}
""")
        result = analyze_scss(tmp_path)
        rules = [s for s in result.symbols if s.kind == "rule_set"]
        assert len(rules) >= 1
        assert any(r.meta.get("selector_type") == "id" for r in rules)

    def test_nesting_selector(self, tmp_path: Path) -> None:
        """Test nesting selector extraction."""
        make_scss_file(tmp_path, "styles.scss", """
.button {
    &:hover {
        opacity: 0.8;
    }
}
""")
        result = analyze_scss(tmp_path)
        rules = [s for s in result.symbols if s.kind == "rule_set"]
        nesting_rules = [r for r in rules if r.meta.get("selector_type") == "nesting"]
        assert len(nesting_rules) >= 1

    def test_pseudo_selector(self, tmp_path: Path) -> None:
        """Test pseudo selector extraction."""
        make_scss_file(tmp_path, "styles.scss", """
:root {
    --color-primary: blue;
}
""")
        result = analyze_scss(tmp_path)
        rules = [s for s in result.symbols if s.kind == "rule_set"]
        assert len(rules) >= 1

    def test_element_selector(self, tmp_path: Path) -> None:
        """Test element selector extraction."""
        make_scss_file(tmp_path, "styles.scss", """
body {
    margin: 0;
}
""")
        result = analyze_scss(tmp_path)
        rules = [s for s in result.symbols if s.kind == "rule_set"]
        assert len(rules) >= 1
        assert any(r.meta.get("selector_type") == "element" for r in rules)


class TestIncludeExtraction:
    """Branch coverage for include extraction."""

    def test_include_mixin(self, tmp_path: Path) -> None:
        """Test mixin include creates edge."""
        make_scss_file(tmp_path, "styles.scss", """
@mixin center {
    display: flex;
    justify-content: center;
}

.container {
    @include center;
}
""")
        result = analyze_scss(tmp_path)
        includes = [s for s in result.symbols if s.kind == "include"]
        assert len(includes) >= 1

        # Check for edge
        uses_edges = [e for e in result.edges if e.edge_type == "uses_mixin"]
        assert len(uses_edges) >= 1


class TestFindScssFiles:
    """Branch coverage for file discovery."""

    def test_finds_scss_files(self, tmp_path: Path) -> None:
        """Test .scss files are discovered."""
        (tmp_path / "styles.scss").write_text("body { margin: 0; }")

        files = list(find_scss_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".scss"

    def test_finds_sass_files(self, tmp_path: Path) -> None:
        """Test .sass files are discovered."""
        (tmp_path / "styles.sass").write_text("body\n  margin: 0")

        files = list(find_scss_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".sass"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        styles = tmp_path / "src" / "styles"
        styles.mkdir(parents=True)
        (styles / "main.scss").write_text("body { margin: 0; }")

        files = list(find_scss_files(tmp_path))
        assert len(files) == 1


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_scss_files(self, tmp_path: Path) -> None:
        """Test directory with no SCSS files."""
        result = analyze_scss(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_scss(self, tmp_path: Path) -> None:
        """Test minimal SCSS file."""
        make_scss_file(tmp_path, "min.scss", """
body {
    margin: 0;
}
""")
        result = analyze_scss(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_scss_file(tmp_path, "styles.scss", """
$color: red;
.box { color: $color; }
""")
        result = analyze_scss(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1
