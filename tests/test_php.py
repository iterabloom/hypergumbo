"""Tests for PHP analyzer."""
import sys
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
        """PHP files that fail to read increment skipped counter."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "test.php"
        php_file.write_text("<?php function test() {} ?>")

        # Mock file read to fail with IOError
        original_read_bytes = Path.read_bytes

        def mock_read_bytes(self: Path) -> bytes:
            if self.name == "test.php":
                raise IOError("Mock read error")
            return original_read_bytes(self)

        with patch.object(Path, "read_bytes", mock_read_bytes):
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


class TestPhpMethodCalls:
    """Tests for PHP method call detection."""

    def test_this_method_call(self, tmp_path: Path) -> None:
        """Detects $this->method() calls within a class."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "MyClass.php"
        php_file.write_text("""<?php
class MyClass {
    public function helper() {
        return 42;
    }

    public function main() {
        return $this->helper();
    }
}
?>""")

        result = analyze_php(tmp_path)

        assert result.run is not None
        # Should have symbols for class and methods
        names = [s.name for s in result.symbols]
        assert "MyClass" in names
        assert "MyClass.helper" in names
        assert "MyClass.main" in names

        # Should have a call edge from main to helper
        assert len(result.edges) >= 1
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1
        # Verify evidence type
        assert any(e.evidence_type == "ast_method_this" for e in call_edges)

    def test_static_method_call(self, tmp_path: Path) -> None:
        """Detects ClassName::method() static calls."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "StaticClass.php"
        php_file.write_text("""<?php
class StaticClass {
    public static function helper() {
        return 42;
    }

    public static function main() {
        return StaticClass::helper();
    }
}
?>""")

        result = analyze_php(tmp_path)

        assert result.run is not None
        # Should have a call edge
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1
        assert any(e.evidence_type == "ast_static_call" for e in call_edges)

    def test_self_static_call(self, tmp_path: Path) -> None:
        """Detects self:: and static:: calls."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "SelfCall.php"
        php_file.write_text("""<?php
class SelfCall {
    public static function helper() {
        return 42;
    }

    public static function useSelf() {
        return self::helper();
    }

    public static function useStatic() {
        return static::helper();
    }
}
?>""")

        result = analyze_php(tmp_path)

        assert result.run is not None
        # Should have call edges for self:: and static::
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 2
        assert all(e.evidence_type == "ast_static_call" for e in call_edges)

    def test_object_instantiation(self, tmp_path: Path) -> None:
        """Detects new ClassName() instantiation."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "Factory.php"
        php_file.write_text("""<?php
class Product {
    public function __construct() {}
}

class Factory {
    public function create() {
        return new Product();
    }
}
?>""")

        result = analyze_php(tmp_path)

        assert result.run is not None
        # Should have instantiation edge
        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        assert len(inst_edges) >= 1
        assert inst_edges[0].evidence_type == "ast_new"

    def test_inferred_method_call(self, tmp_path: Path) -> None:
        """Detects $obj->method() with inferred type."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "Caller.php"
        php_file.write_text("""<?php
class Service {
    public function doWork() {
        return 42;
    }
}

class Caller {
    public function run($service) {
        return $service->doWork();
    }
}
?>""")

        result = analyze_php(tmp_path)

        assert result.run is not None
        # Should have inferred method call edge
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1
        # Inferred calls have lower confidence
        inferred = [e for e in call_edges if e.evidence_type == "ast_method_inferred"]
        assert len(inferred) >= 1
        assert inferred[0].confidence < 0.9  # Lower confidence for inferred


class TestPhpCrossFileResolution:
    """Tests for cross-file call resolution."""

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Resolves function calls across files."""
        from hypergumbo.analyze.php import analyze_php

        # Create two files
        (tmp_path / "helpers.php").write_text("""<?php
function helper() {
    return 42;
}
?>""")

        (tmp_path / "main.php").write_text("""<?php
function main() {
    return helper();
}
?>""")

        result = analyze_php(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 2

        # Should have symbols from both files
        names = [s.name for s in result.symbols]
        assert "helper" in names
        assert "main" in names

        # Should have cross-file call edge
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_cross_file_class_instantiation(self, tmp_path: Path) -> None:
        """Resolves class instantiation across files."""
        from hypergumbo.analyze.php import analyze_php

        (tmp_path / "Product.php").write_text("""<?php
class Product {
    public function getName() {
        return "Widget";
    }
}
?>""")

        (tmp_path / "Factory.php").write_text("""<?php
class Factory {
    public function create() {
        return new Product();
    }
}
?>""")

        result = analyze_php(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 2

        # Should have cross-file instantiation edge
        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        assert len(inst_edges) >= 1

    def test_cross_file_static_call(self, tmp_path: Path) -> None:
        """Resolves static method calls across files."""
        from hypergumbo.analyze.php import analyze_php

        (tmp_path / "Helper.php").write_text("""<?php
class Helper {
    public static function format($s) {
        return strtoupper($s);
    }
}
?>""")

        (tmp_path / "User.php").write_text("""<?php
class User {
    public function display() {
        return Helper::format("name");
    }
}
?>""")

        result = analyze_php(tmp_path)

        assert result.run is not None
        # Should have cross-file static call edge
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        static_calls = [e for e in call_edges if e.evidence_type == "ast_static_call"]
        assert len(static_calls) >= 1


class TestPhpEdgeExtraction:
    """Tests for edge extraction edge cases."""

    def test_no_edges_outside_function(self, tmp_path: Path) -> None:
        """Calls outside functions/methods don't create edges."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "top_level.php"
        php_file.write_text("""<?php
function helper() {
    return 42;
}

// Top-level call - no current function context
helper();
?>""")

        result = analyze_php(tmp_path)

        assert result.run is not None
        # The call at top level shouldn't create an edge
        # (no source function for the edge)
        assert len(result.edges) == 0

    def test_nested_class_method(self, tmp_path: Path) -> None:
        """Handles nested method calls."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "Nested.php"
        php_file.write_text("""<?php
class Outer {
    public function outer() {
        return $this->inner();
    }

    public function inner() {
        return 42;
    }
}
?>""")

        result = analyze_php(tmp_path)

        assert result.run is not None
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_method_without_class_context(self, tmp_path: Path) -> None:
        """Method outside class still extracts symbol."""
        from hypergumbo.analyze.php import _extract_symbols, _get_php_parser
        from hypergumbo.ir import AnalysisRun

        # This is an edge case - method_declaration outside class
        # Tree-sitter may parse it differently, but we handle it
        parser = _get_php_parser()
        assert parser is not None

        source = b"<?php function standalone() { return 1; } ?>"
        tree = parser.parse(source)
        run = AnalysisRun.create(pass_id="test", version="test")

        symbols = _extract_symbols(tree, source, tmp_path / "test.php", run)

        # Should extract as function
        assert len(symbols) >= 1
        assert symbols[0].kind == "function"

    def test_analyze_php_file_success(self, tmp_path: Path) -> None:
        """_analyze_php_file returns symbols and edges on success."""
        from hypergumbo.analyze.php import _analyze_php_file
        from hypergumbo.ir import AnalysisRun

        php_file = tmp_path / "test.php"
        php_file.write_text("""<?php
class MyClass {
    public function helper() {
        return 42;
    }

    public function main() {
        return $this->helper();
    }
}
?>""")

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_php_file(php_file, run)

        assert success is True
        assert len(symbols) >= 3  # class + 2 methods
        assert len(edges) >= 1  # at least one call edge

        # Verify method call edge is detected
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_analyze_php_parser_none_after_check(self, tmp_path: Path) -> None:
        """analyze_php handles case where parser is None after availability check."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "test.php"
        php_file.write_text("<?php function test() {} ?>")

        # Mock is_php_tree_sitter_available to return True
        # but _get_php_parser to return None
        with patch(
            "hypergumbo.analyze.php.is_php_tree_sitter_available",
            return_value=True,
        ), patch(
            "hypergumbo.analyze.php._get_php_parser",
            return_value=None,
        ):
            result = analyze_php(tmp_path)

        assert result.run is not None
        assert result.skipped is True
        assert "tree-sitter-php" in result.skip_reason


class TestLaravelRouteDetection:
    """Tests for Laravel route detection with Route::get, Route::post, etc."""

    def test_route_get(self, tmp_path: Path) -> None:
        """Detects Route::get() call."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "web.php"
        php_file.write_text("""<?php
use Illuminate\\Support\\Facades\\Route;

Route::get('/users', [UserController::class, 'index']);
?>""")

        result = analyze_php(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 1
        route = routes[0]

        assert route.meta is not None
        assert route.meta.get("route_path") == "/users"
        assert route.meta.get("http_method") == "GET"
        assert route.stable_id == "GET"

    def test_route_post(self, tmp_path: Path) -> None:
        """Detects Route::post() call."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "api.php"
        php_file.write_text("""<?php
Route::post('/users', [UserController::class, 'store']);
?>""")

        result = analyze_php(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 1
        route = routes[0]

        assert route.meta is not None
        assert route.meta.get("route_path") == "/users"
        assert route.meta.get("http_method") == "POST"
        assert route.stable_id == "POST"

    def test_all_http_methods(self, tmp_path: Path) -> None:
        """Detects all Laravel HTTP method routes."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "routes.php"
        php_file.write_text("""<?php
Route::get('/items', [ItemController::class, 'index']);
Route::post('/items', [ItemController::class, 'store']);
Route::put('/items/{id}', [ItemController::class, 'update']);
Route::delete('/items/{id}', [ItemController::class, 'destroy']);
Route::patch('/items/{id}', [ItemController::class, 'patch']);
?>""")

        result = analyze_php(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 5

        http_methods = {r.stable_id for r in routes}
        assert http_methods == {"GET", "POST", "PUT", "DELETE", "PATCH"}

    def test_route_with_closure(self, tmp_path: Path) -> None:
        """Detects Route with inline closure handler."""
        from hypergumbo.analyze.php import analyze_php

        php_file = tmp_path / "web.php"
        php_file.write_text("""<?php
Route::get('/hello', function () {
    return 'Hello World';
});
?>""")

        result = analyze_php(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 1
        assert routes[0].meta.get("route_path") == "/hello"


class TestPHPSignatureExtraction:
    """Tests for PHP function signature extraction."""

    def test_typed_method_with_return_type(self, tmp_path: Path) -> None:
        """Extracts signature from method with typed params and return type."""
        from hypergumbo.analyze.php import analyze_php

        (tmp_path / "Calculator.php").write_text("""<?php
class Calculator {
    public function add(int $x, int $y): int {
        return $x + $y;
    }
}
?>""")
        result = analyze_php(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "add" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(int $x, int $y): int"

    def test_void_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature from void method (omits void return type)."""
        from hypergumbo.analyze.php import analyze_php

        (tmp_path / "Logger.php").write_text("""<?php
class Logger {
    public function log(string $message): void {
        echo $message;
    }
}
?>""")
        result = analyze_php(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "log" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(string $message)"

    def test_no_type_hints_signature(self, tmp_path: Path) -> None:
        """Extracts signature from method without type hints."""
        from hypergumbo.analyze.php import analyze_php

        (tmp_path / "Utils.php").write_text("""<?php
class Utils {
    public function process($data) {
        return $data;
    }
}
?>""")
        result = analyze_php(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "process" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "($data)"


# ============================================================================
# Deprecation Warning Tests (ADR-0003 v1.0.x)
# ============================================================================


class TestLaravelRouteDetectionDeprecation:
    """Tests for deprecation warnings on analyzer-level Laravel route detection."""

    def test_laravel_route_emits_deprecation_warning(self, tmp_path: Path) -> None:
        """Laravel route detection emits deprecation warning."""
        import warnings
        from hypergumbo.analyze import php as php_module
        from hypergumbo.analyze.php import analyze_php

        # Reset the warning deduplication set
        php_module._deprecated_route_warnings_emitted.clear()

        routes_file = tmp_path / "web.php"
        routes_file.write_text("""<?php
Route::get('/users', [UserController::class, 'index']);
Route::post('/users', [UserController::class, 'store']);
?>""")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            analyze_php(tmp_path)

        # Should have at least one deprecation warning for Laravel
        deprecation_warnings = [
            warning
            for warning in w
            if issubclass(warning.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) >= 1
        warning_message = str(deprecation_warnings[0].message)
        assert "Laravel" in warning_message
        assert "deprecated" in warning_message.lower()

    def test_deprecation_warning_emitted_once_per_session(
        self, tmp_path: Path
    ) -> None:
        """Deprecation warning is emitted only once per session."""
        import warnings
        from hypergumbo.analyze import php as php_module
        from hypergumbo.analyze.php import analyze_php

        # Reset the warning deduplication set
        php_module._deprecated_route_warnings_emitted.clear()

        # Create multiple route files
        (tmp_path / "web.php").write_text("""<?php
Route::get('/users', 'UserController@index');
Route::post('/users', 'UserController@store');
?>""")
        (tmp_path / "api.php").write_text("""<?php
Route::get('/api/items', 'ItemController@index');
?>""")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            analyze_php(tmp_path)

        # Should have exactly one Laravel deprecation warning (deduplicated)
        laravel_warnings = [
            warning
            for warning in w
            if issubclass(warning.category, DeprecationWarning)
            and "Laravel" in str(warning.message)
        ]
        assert len(laravel_warnings) == 1

    def test_no_deprecation_warning_without_route_calls(
        self, tmp_path: Path
    ) -> None:
        """No deprecation warning for files without Laravel route calls."""
        import warnings
        from hypergumbo.analyze import php as php_module
        from hypergumbo.analyze.php import analyze_php

        # Reset the warning deduplication set
        php_module._deprecated_route_warnings_emitted.clear()

        php_file = tmp_path / "User.php"
        php_file.write_text("""<?php
class User {
    public function __construct($name) {
        $this->name = $name;
    }

    public function greet() {
        return "Hello, {$this->name}!";
    }
}
?>""")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            analyze_php(tmp_path)

        # Should have no deprecation warnings for route detection
        route_warnings = [
            warning
            for warning in w
            if issubclass(warning.category, DeprecationWarning)
            and "Laravel" in str(warning.message)
        ]
        assert len(route_warnings) == 0
