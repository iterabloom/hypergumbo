"""Branch coverage tests for html.py analyzer.

Tests specific branch paths in the HTML analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- File symbol extraction
- Script tag edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_mainstream.html import (
    _make_file_id,
    analyze_html,
    find_html_files,
)


def make_html_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an HTML file with given content."""
    (tmp_path / name).write_text(content)


class TestHtmlHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = _make_file_id("pages/index.html")
        assert file_id == "html:pages/index.html:1-1:file:file"


class TestFileSymbolExtraction:
    """Branch coverage for HTML file symbol extraction."""

    def test_creates_file_symbol(self, tmp_path: Path) -> None:
        """Test HTML file creates file symbol."""
        make_html_file(tmp_path, "index.html", """<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body><h1>Hello</h1></body>
</html>
""")
        result = analyze_html(tmp_path)
        file_symbols = [s for s in result.symbols if s.kind == "file"]
        assert len(file_symbols) >= 1

    def test_file_symbol_span(self, tmp_path: Path) -> None:
        """Test file symbol has correct span."""
        make_html_file(tmp_path, "index.html", """<!DOCTYPE html>
<html>
<body>
<h1>Hello</h1>
</body>
</html>
""")
        result = analyze_html(tmp_path)
        file_symbols = [s for s in result.symbols if s.kind == "file"]
        assert len(file_symbols) >= 1
        assert file_symbols[0].span.start_line == 1


class TestScriptTagEdgeExtraction:
    """Branch coverage for script tag edge extraction."""

    def test_script_src_double_quotes(self, tmp_path: Path) -> None:
        """Test script src with double quotes."""
        make_html_file(tmp_path, "index.html", """<!DOCTYPE html>
<html>
<head>
    <script src="js/main.js"></script>
</head>
<body></body>
</html>
""")
        result = analyze_html(tmp_path)
        script_edges = [e for e in result.edges if e.edge_type == "script_src"]
        assert len(script_edges) >= 1

    def test_script_src_single_quotes(self, tmp_path: Path) -> None:
        """Test script src with single quotes."""
        make_html_file(tmp_path, "index.html", """<!DOCTYPE html>
<html>
<head>
    <script src='js/app.js'></script>
</head>
<body></body>
</html>
""")
        result = analyze_html(tmp_path)
        script_edges = [e for e in result.edges if e.edge_type == "script_src"]
        assert len(script_edges) >= 1

    def test_multiple_scripts(self, tmp_path: Path) -> None:
        """Test multiple script tags."""
        make_html_file(tmp_path, "index.html", """<!DOCTYPE html>
<html>
<head>
    <script src="js/vendor.js"></script>
    <script src="js/app.js"></script>
</head>
<body>
    <script src="js/analytics.js"></script>
</body>
</html>
""")
        result = analyze_html(tmp_path)
        script_edges = [e for e in result.edges if e.edge_type == "script_src"]
        assert len(script_edges) >= 3

    def test_script_with_type(self, tmp_path: Path) -> None:
        """Test script tag with type attribute."""
        make_html_file(tmp_path, "index.html", """<!DOCTYPE html>
<html>
<head>
    <script type="module" src="js/main.mjs"></script>
</head>
<body></body>
</html>
""")
        result = analyze_html(tmp_path)
        script_edges = [e for e in result.edges if e.edge_type == "script_src"]
        assert len(script_edges) >= 1

    def test_script_line_number(self, tmp_path: Path) -> None:
        """Test script edge has correct line number."""
        make_html_file(tmp_path, "index.html", """<!DOCTYPE html>
<html>
<head>
    <title>Test</title>
</head>
<body>
    <script src="js/main.js"></script>
</body>
</html>
""")
        result = analyze_html(tmp_path)
        script_edges = [e for e in result.edges if e.edge_type == "script_src"]
        assert len(script_edges) >= 1
        # Script is on line 7 or 8 depending on newlines
        assert script_edges[0].line >= 6

    def test_external_script(self, tmp_path: Path) -> None:
        """Test external CDN script."""
        make_html_file(tmp_path, "index.html", """<!DOCTYPE html>
<html>
<head>
    <script src="https://cdn.example.com/lib.js"></script>
</head>
<body></body>
</html>
""")
        result = analyze_html(tmp_path)
        script_edges = [e for e in result.edges if e.edge_type == "script_src"]
        assert len(script_edges) >= 1


class TestFindHtmlFiles:
    """Branch coverage for file discovery."""

    def test_finds_html_files(self, tmp_path: Path) -> None:
        """Test .html files are discovered."""
        (tmp_path / "index.html").write_text("<html></html>")

        files = list(find_html_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".html" for f in files)

    def test_finds_htm_files(self, tmp_path: Path) -> None:
        """Test .htm files are discovered."""
        (tmp_path / "page.htm").write_text("<html></html>")

        files = list(find_html_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".htm" for f in files)

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_html_files(self, tmp_path: Path) -> None:
        """Test directory with no HTML files."""
        result = analyze_html(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_html(self, tmp_path: Path) -> None:
        """Test minimal HTML file."""
        make_html_file(tmp_path, "min.html", """<!DOCTYPE html>
<html>
<body>Hello</body>
</html>
""")
        result = analyze_html(tmp_path)
        assert len(result.symbols) >= 1

    def test_html_without_scripts(self, tmp_path: Path) -> None:
        """Test HTML file with no scripts."""
        make_html_file(tmp_path, "index.html", """<!DOCTYPE html>
<html>
<head><title>No scripts</title></head>
<body><p>No JavaScript here</p></body>
</html>
""")
        result = analyze_html(tmp_path)
        assert len(result.symbols) >= 1
        assert len(result.edges) == 0


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_html_file(tmp_path, "index.html", """<!DOCTYPE html>
<html><body>Test</body></html>
""")
        result = analyze_html(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1


class TestMaxFilesLimit:
    """Branch coverage for max_files parameter."""

    def test_max_files_limit(self, tmp_path: Path) -> None:
        """Test max_files limits file processing."""
        for i in range(5):
            make_html_file(tmp_path, f"page{i}.html", f"<html><body>Page {i}</body></html>")

        result = analyze_html(tmp_path, max_files=2)
        assert result.run.files_analyzed <= 2
