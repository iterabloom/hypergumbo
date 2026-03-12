# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for css.py analyzer.

Tests specific branch paths in the CSS analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Import extraction
- CSS variable extraction
- Keyframes extraction
- Media query extraction
- Font-face extraction
- Class selector extraction
- ID selector extraction
- Import edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_mainstream.css import (
    _make_symbol_id,
    _make_edge_id,
    analyze_css_files,
    find_css_files,
)


def make_css_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a CSS file with given content."""
    (tmp_path / name).write_text(content)


class TestCSSHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("styles/main.css", 10, "--primary-color", "variable")
        assert symbol_id.startswith("css:sha256:")

    def test_make_edge_id_format(self) -> None:
        """Test edge ID is deterministic."""
        edge_id1 = _make_edge_id("src1", "dst1", "imports")
        edge_id2 = _make_edge_id("src1", "dst1", "imports")
        assert edge_id1 == edge_id2
        assert edge_id1.startswith("edge:sha256:")


class TestImportExtraction:
    """Branch coverage for @import extraction."""

    def test_import_string(self, tmp_path: Path) -> None:
        """Test @import with string path."""
        make_css_file(tmp_path, "styles.css", """
@import "reset.css";
@import 'variables.css';

body { margin: 0; }
""")
        result = analyze_css_files(tmp_path)
        assert not result.skipped

        imports = [s for s in result.symbols if s.kind == "import"]
        assert len(imports) >= 2

    def test_import_url(self, tmp_path: Path) -> None:
        """Test @import with url()."""
        make_css_file(tmp_path, "styles.css", """
@import url("https://fonts.googleapis.com/css?family=Roboto");

body { font-family: 'Roboto', sans-serif; }
""")
        result = analyze_css_files(tmp_path)
        imports = [s for s in result.symbols if s.kind == "import"]
        assert len(imports) >= 1

    def test_import_creates_edge(self, tmp_path: Path) -> None:
        """Test import creates imports edge."""
        make_css_file(tmp_path, "styles.css", """
@import "variables.css";

:root { --color: red; }
""")
        result = analyze_css_files(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1


class TestVariableExtraction:
    """Branch coverage for CSS variable extraction."""

    def test_simple_variable(self, tmp_path: Path) -> None:
        """Test CSS custom property extraction."""
        make_css_file(tmp_path, "styles.css", """
:root {
    --primary-color: #3498db;
    --secondary-color: #2ecc71;
}
""")
        result = analyze_css_files(tmp_path)
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) >= 2
        names = [v.name for v in variables]
        assert "--primary-color" in names
        assert "--secondary-color" in names

    def test_variable_with_calc(self, tmp_path: Path) -> None:
        """Test variable with complex value."""
        make_css_file(tmp_path, "styles.css", """
:root {
    --spacing-unit: 8px;
    --spacing-lg: calc(var(--spacing-unit) * 2);
}
""")
        result = analyze_css_files(tmp_path)
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) >= 2


class TestKeyframesExtraction:
    """Branch coverage for @keyframes extraction."""

    def test_simple_keyframes(self, tmp_path: Path) -> None:
        """Test @keyframes animation extraction."""
        make_css_file(tmp_path, "styles.css", """
@keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
}
""")
        result = analyze_css_files(tmp_path)
        keyframes = [s for s in result.symbols if s.kind == "keyframes"]
        assert len(keyframes) >= 1
        assert any(k.name == "fadeIn" for k in keyframes)

    def test_multiple_keyframes(self, tmp_path: Path) -> None:
        """Test multiple @keyframes animations."""
        make_css_file(tmp_path, "styles.css", """
@keyframes slideIn {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(0); }
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}
""")
        result = analyze_css_files(tmp_path)
        keyframes = [s for s in result.symbols if s.kind == "keyframes"]
        assert len(keyframes) >= 2


class TestMediaQueryExtraction:
    """Branch coverage for @media query extraction."""

    def test_simple_media_query(self, tmp_path: Path) -> None:
        """Test @media query extraction."""
        make_css_file(tmp_path, "styles.css", """
@media (min-width: 768px) {
    .container { max-width: 720px; }
}
""")
        result = analyze_css_files(tmp_path)
        media = [s for s in result.symbols if s.kind == "media"]
        assert len(media) >= 1

    def test_multiple_media_queries(self, tmp_path: Path) -> None:
        """Test multiple @media queries."""
        make_css_file(tmp_path, "styles.css", """
@media (max-width: 576px) {
    .container { width: 100%; }
}

@media (min-width: 992px) {
    .container { max-width: 960px; }
}
""")
        result = analyze_css_files(tmp_path)
        media = [s for s in result.symbols if s.kind == "media"]
        assert len(media) >= 2


class TestFontFaceExtraction:
    """Branch coverage for @font-face extraction."""

    def test_font_face(self, tmp_path: Path) -> None:
        """Test @font-face declaration extraction."""
        make_css_file(tmp_path, "styles.css", """
@font-face {
    font-family: 'CustomFont';
    src: url('fonts/custom.woff2') format('woff2');
}
""")
        result = analyze_css_files(tmp_path)
        fonts = [s for s in result.symbols if s.kind == "font_face"]
        assert len(fonts) >= 1


class TestSelectorExtraction:
    """Branch coverage for selector extraction."""

    def test_class_selector(self, tmp_path: Path) -> None:
        """Test class selector extraction."""
        make_css_file(tmp_path, "styles.css", """
.button {
    padding: 10px 20px;
}

.card {
    border-radius: 8px;
}
""")
        result = analyze_css_files(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class_selector"]
        assert len(classes) >= 2

    def test_id_selector(self, tmp_path: Path) -> None:
        """Test ID selector extraction."""
        make_css_file(tmp_path, "styles.css", """
#header {
    background: #333;
}

#footer {
    background: #222;
}
""")
        result = analyze_css_files(tmp_path)
        ids = [s for s in result.symbols if s.kind == "id_selector"]
        assert len(ids) >= 2


class TestFindCSSFiles:
    """Branch coverage for file discovery."""

    def test_finds_css_files(self, tmp_path: Path) -> None:
        """Test .css files are discovered."""
        (tmp_path / "styles.css").write_text("body { margin: 0; }")

        files = list(find_css_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".css" for f in files)

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        styles = tmp_path / "src" / "styles"
        styles.mkdir(parents=True)
        (styles / "main.css").write_text("body { margin: 0; }")

        files = list(find_css_files(tmp_path))
        assert len(files) >= 1


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_css_files(self, tmp_path: Path) -> None:
        """Test directory with no CSS files."""
        result = analyze_css_files(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_css(self, tmp_path: Path) -> None:
        """Test minimal CSS file."""
        make_css_file(tmp_path, "min.css", """
body {
    margin: 0;
}
""")
        result = analyze_css_files(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_css_file(tmp_path, "styles.css", """
:root {
    --color: red;
}
""")
        result = analyze_css_files(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1
