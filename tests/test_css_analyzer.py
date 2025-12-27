"""Tests for CSS analyzer using tree-sitter-css.

Tests verify that the analyzer correctly extracts:
- @import statements
- CSS variables (custom properties)
- @keyframes animations
- @media queries
- @font-face declarations
"""

from hypergumbo.analyze.css import (
    PASS_ID,
    PASS_VERSION,
    CSSAnalysisResult,
    analyze_css_files,
    find_css_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "css-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_import(tmp_path):
    """Test detection of @import statements."""
    css_file = tmp_path / "styles.css"
    css_file.write_text("""
@import "base.css";
@import url("theme.css");
""")
    result = analyze_css_files(tmp_path)

    assert not result.skipped
    imports = [s for s in result.symbols if s.kind == "import"]
    assert len(imports) >= 2
    names = [i.name for i in imports]
    assert "base.css" in names
    assert "theme.css" in names


def test_analyze_import_edges(tmp_path):
    """Test that import edges are created."""
    css_file = tmp_path / "main.css"
    css_file.write_text('@import "variables.css";')

    result = analyze_css_files(tmp_path)

    assert len(result.edges) >= 1
    import_edges = [e for e in result.edges if e.edge_type == "imports"]
    assert len(import_edges) >= 1
    assert import_edges[0].dst == "variables.css"


def test_analyze_css_variable(tmp_path):
    """Test detection of CSS custom properties."""
    css_file = tmp_path / "variables.css"
    css_file.write_text("""
:root {
    --primary-color: #3498db;
    --font-size-base: 16px;
    --spacing-unit: 8px;
}
""")
    result = analyze_css_files(tmp_path)

    variables = [s for s in result.symbols if s.kind == "variable"]
    assert len(variables) >= 3
    names = [v.name for v in variables]
    assert "--primary-color" in names
    assert "--font-size-base" in names
    assert "--spacing-unit" in names


def test_analyze_keyframes(tmp_path):
    """Test detection of @keyframes animations."""
    css_file = tmp_path / "animations.css"
    css_file.write_text("""
@keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
}

@keyframes slideUp {
    0% { transform: translateY(100%); }
    100% { transform: translateY(0); }
}
""")
    result = analyze_css_files(tmp_path)

    keyframes = [s for s in result.symbols if s.kind == "keyframes"]
    assert len(keyframes) >= 2
    names = [k.name for k in keyframes]
    assert "fadeIn" in names
    assert "slideUp" in names


def test_analyze_media_query(tmp_path):
    """Test detection of @media queries."""
    css_file = tmp_path / "responsive.css"
    css_file.write_text("""
@media (max-width: 768px) {
    body { font-size: 14px; }
}

@media screen and (min-width: 1024px) {
    .container { width: 960px; }
}
""")
    result = analyze_css_files(tmp_path)

    media = [s for s in result.symbols if s.kind == "media"]
    assert len(media) >= 2


def test_analyze_font_face(tmp_path):
    """Test detection of @font-face declarations."""
    css_file = tmp_path / "fonts.css"
    css_file.write_text("""
@font-face {
    font-family: "CustomFont";
    src: url("custom-font.woff2") format("woff2");
}
""")
    result = analyze_css_files(tmp_path)

    fonts = [s for s in result.symbols if s.kind == "font_face"]
    assert len(fonts) >= 1
    assert fonts[0].name == "CustomFont"


def test_find_css_files(tmp_path):
    """Test that CSS files are discovered correctly."""
    (tmp_path / "styles.css").write_text("body {}")
    (tmp_path / "theme.css").write_text(".btn {}")
    (tmp_path / "not_css.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.css").write_text("h1 {}")

    files = list(find_css_files(tmp_path))
    assert len(files) >= 3


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no CSS files."""
    result = analyze_css_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    css_file = tmp_path / "test.css"
    css_file.write_text("body {}")

    result = analyze_css_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    css_file = tmp_path / "broken.css"
    css_file.write_text("{ invalid { css {{{")

    # Should not raise an exception
    result = analyze_css_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, CSSAnalysisResult)


def test_span_information(tmp_path):
    """Test that span information is correct."""
    css_file = tmp_path / "span.css"
    css_file.write_text("""@keyframes test {
    from { opacity: 0; }
}
""")
    result = analyze_css_files(tmp_path)

    keyframes = [s for s in result.symbols if s.kind == "keyframes"]
    assert len(keyframes) >= 1

    # Check span
    assert keyframes[0].span.start_line >= 1
    assert keyframes[0].span.end_line >= keyframes[0].span.start_line


def test_tree_sitter_not_available():
    """Test graceful degradation when tree-sitter is not available."""
    from hypergumbo.analyze.css import is_css_tree_sitter_available

    # The function should return a boolean
    result = is_css_tree_sitter_available()
    assert isinstance(result, bool)


def test_nested_media_variables(tmp_path):
    """Test that variables inside @media are detected."""
    css_file = tmp_path / "responsive-vars.css"
    css_file.write_text("""
@media (prefers-color-scheme: dark) {
    :root {
        --bg-color: #1a1a1a;
    }
}
""")
    result = analyze_css_files(tmp_path)

    variables = [s for s in result.symbols if s.kind == "variable"]
    # Should find --bg-color inside the media query
    assert len(variables) >= 1
    assert any(v.name == "--bg-color" for v in variables)


def test_analyze_class_selector(tmp_path):
    """Test detection of class selectors (.class)."""
    css_file = tmp_path / "classes.css"
    css_file.write_text("""
.button {
    padding: 10px;
}

.nav-item {
    display: inline-block;
}

.card.featured {
    border: 2px solid gold;
}
""")
    result = analyze_css_files(tmp_path)

    classes = [s for s in result.symbols if s.kind == "class_selector"]
    # Note: .card.featured may be 1 or 2 symbols depending on tree-sitter-css version
    assert len(classes) >= 3
    names = [c.name for c in classes]
    assert ".button" in names
    assert ".nav-item" in names
    # Combined selectors like .card.featured may appear as single unit or split
    assert any(".card" in n for n in names)


def test_analyze_id_selector(tmp_path):
    """Test detection of ID selectors (#id)."""
    css_file = tmp_path / "ids.css"
    css_file.write_text("""
#header {
    position: fixed;
}

#main-content {
    margin: 0 auto;
}

#footer {
    background: #333;
}
""")
    result = analyze_css_files(tmp_path)

    ids = [s for s in result.symbols if s.kind == "id_selector"]
    assert len(ids) >= 3
    names = [i.name for i in ids]
    assert "#header" in names
    assert "#main-content" in names
    assert "#footer" in names


def test_analyze_mixed_selectors(tmp_path):
    """Test detection of mixed class and ID selectors."""
    css_file = tmp_path / "mixed.css"
    css_file.write_text("""
#app .container {
    max-width: 1200px;
}

.sidebar #search-box {
    width: 100%;
}
""")
    result = analyze_css_files(tmp_path)

    classes = [s for s in result.symbols if s.kind == "class_selector"]
    ids = [s for s in result.symbols if s.kind == "id_selector"]

    assert len(classes) >= 2
    assert len(ids) >= 2
    assert any(c.name == ".container" for c in classes)
    assert any(c.name == ".sidebar" for c in classes)
    assert any(i.name == "#app" for i in ids)
    assert any(i.name == "#search-box" for i in ids)
