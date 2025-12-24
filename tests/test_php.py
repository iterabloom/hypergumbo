"""Tests for PHP analyzer."""
import sys
import pytest
from pathlib import Path
from unittest.mock import patch


class TestFindPhpFiles:
    """Tests for PHP file discovery."""

    def test_finds_php_files(self, tmp_path: Path) -> None:
        """Finds .php files."""
        from hypergumbo.analyze.php import find_php_files

        (tmp_path / "index.php").write_text("<?php echo 'hello'; ?>")
        (tmp_path / "other.txt").write_text("not php")

        files = list(find_php_files(tmp_path))

        assert len(files) == 1
        assert files[0].suffix == ".php"

    def test_excludes_vendor(self, tmp_path: Path) -> None:
        """Excludes vendor directory."""
        from hypergumbo.analyze.php import find_php_files

        (tmp_path / "app.php").write_text("<?php class App {} ?>")
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "pkg.php").write_text("<?php class Vendor {} ?>")

        files = list(find_php_files(tmp_path))

        assert len(files) == 1
        assert files[0].name == "app.php"


class TestPhpTreeSitterAvailability:
    """Tests for tree-sitter-php availability checking."""

    def test_is_php_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-php is available."""
        from hypergumbo.analyze.php import is_php_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()  # Non-None = available
            assert is_php_tree_sitter_available() is True

    def test_is_php_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.php import is_php_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_php_tree_sitter_available() is False

    def test_is_php_tree_sitter_available_no_php_grammar(self) -> None:
        """Returns False when tree-sitter-php is not available."""
        from hypergumbo.analyze.php import is_php_tree_sitter_available

        def mock_find_spec(name: str):
            if name == "tree_sitter":
                return object()  # tree_sitter is available
            return None  # tree_sitter_php is not

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_php_tree_sitter_available() is False


class TestAnalyzePhpFallback:
    """Tests for fallback behavior when tree-sitter-php unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-php unavailable."""
        from hypergumbo.analyze.php import analyze_php

        (tmp_path / "test.php").write_text("<?php function foo() {} ?>")

        with patch("hypergumbo.analyze.php.is_php_tree_sitter_available", return_value=False):
            result = analyze_php(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-php" in result.skip_reason


class TestPhpFunctionExtraction:
    """Tests for extracting PHP functions."""

    def test_extracts_function(self, tmp_path: Path) -> None:
        """Extracts PHP function declarations."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "functions.php"
        php_file.write_text("""<?php
function hello($name) {
    return "Hello, " . $name;
}

function goodbye() {
    echo "Goodbye!";
}
?>""")

        result = analyze_php(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 1
        names = [s.name for s in result.symbols]
        assert "hello" in names
        assert "goodbye" in names

    def test_extracts_class(self, tmp_path: Path) -> None:
        """Extracts PHP class declarations."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "MyClass.php"
        php_file.write_text("""<?php
class MyClass {
    public function myMethod() {
        return 42;
    }
}
?>""")

        result = analyze_php(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "MyClass" in names
        # Method should be MyClass.myMethod
        assert any("myMethod" in name for name in names)

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Handles PHP file with no functions/classes."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "empty.php"
        php_file.write_text("<?php echo 'Hello'; ?>")

        result = analyze_php(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 1
        # No symbols extracted, but no error
        assert result.skipped is False


class TestPhpMixedContent:
    """Tests for PHP files with mixed HTML/PHP content."""

    def test_handles_html_with_php(self, tmp_path: Path) -> None:
        """Handles files with mixed HTML and PHP."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "template.php"
        php_file.write_text("""<!DOCTYPE html>
<html>
<head>
    <title><?php echo $title; ?></title>
</head>
<body>
<?php
function renderContent($data) {
    foreach ($data as $item) {
        echo "<p>" . $item . "</p>";
    }
}
renderContent($items);
?>
</body>
</html>""")

        result = analyze_php(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "renderContent" in names


class TestPhpAnalysisRun:
    """Tests for PHP analysis run tracking."""

    def test_tracks_files_analyzed(self, tmp_path: Path) -> None:
        """Tracks number of files analyzed."""
        from hypergumbo.analyze.php import analyze_php

        (tmp_path / "a.php").write_text("<?php function a() {} ?>")
        (tmp_path / "b.php").write_text("<?php function b() {} ?>")
        (tmp_path / "c.php").write_text("<?php function c() {} ?>")

        result = analyze_php(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 3
        assert result.run.pass_id == "php-v1"

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Handles repo with no PHP files."""
        from hypergumbo.analyze.php import analyze_php

        (tmp_path / "app.js").write_text("const x = 1;")

        result = analyze_php(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 0
        assert len(result.symbols) == 0


class TestPhpEdgeCases:
    """Tests for PHP edge cases and error handling."""

    def test_find_name_in_children_no_name(self) -> None:
        """Returns None when node has no 'name' child."""
        from hypergumbo.analyze.php import _find_name_in_children
        from unittest.mock import MagicMock

        # Create mock node with no "name" child
        mock_child = MagicMock()
        mock_child.type = "identifier"

        mock_node = MagicMock()
        mock_node.children = [mock_child]

        result = _find_name_in_children(mock_node, b"source")
        assert result is None

    def test_get_php_parser_import_error(self) -> None:
        """Returns None when tree-sitter-php is not available."""
        from hypergumbo.analyze.php import _get_php_parser

        # Mark tree-sitter modules as unavailable in sys.modules
        with patch.dict(sys.modules, {
            "tree_sitter": None,
            "tree_sitter_php": None,
        }):
            result = _get_php_parser()
            assert result is None

    def test_analyze_php_file_parser_unavailable(self, tmp_path: Path) -> None:
        """Returns failure when parser is unavailable."""
        from hypergumbo.analyze.php import _analyze_php_file
        from hypergumbo.ir import AnalysisRun

        php_file = tmp_path / "test.php"
        php_file.write_text("<?php function test() {} ?>")

        run = AnalysisRun.create(pass_id="test", version="test")

        with patch("hypergumbo.analyze.php._get_php_parser", return_value=None):
            symbols, edges, success = _analyze_php_file(php_file, run)

        assert success is False
        assert len(symbols) == 0

    def test_analyze_php_file_read_error(self, tmp_path: Path) -> None:
        """Returns failure when file cannot be read."""
        from hypergumbo.analyze.php import _analyze_php_file
        from hypergumbo.ir import AnalysisRun

        php_file = tmp_path / "missing.php"
        # Don't create the file

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_php_file(php_file, run)

        assert success is False
        assert len(symbols) == 0

    def test_php_file_skipped_increments_counter(self, tmp_path: Path) -> None:
        """PHP files that fail to analyze increment skipped counter."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "test.php"
        php_file.write_text("<?php function test() {} ?>")

        # Mock file analysis to fail
        def mock_analyze_php_file(file_path, run):
            return [], [], False

        with patch(
            "hypergumbo.analyze.php._analyze_php_file",
            side_effect=mock_analyze_php_file,
        ):
            result = analyze_php(tmp_path)

        assert result.run is not None
        assert result.run.files_skipped == 1

    def test_extracts_call_edges(self, tmp_path: Path) -> None:
        """Extracts call edges between PHP functions."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "functions.php"
        php_file.write_text("""<?php
function helper() {
    return 42;
}

function main() {
    $x = helper();
    return $x;
}
?>""")

        result = analyze_php(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "helper" in names
        assert "main" in names

        # Should have a call edge from main to helper
        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.edge_type == "calls"
