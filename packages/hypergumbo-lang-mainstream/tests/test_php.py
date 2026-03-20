# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for PHP analyzer."""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch



class TestFindPhpFiles:
    """Tests for PHP file discovery."""

    def test_finds_php_files(self, tmp_path: Path) -> None:
        """Finds .php files."""
        from hypergumbo_lang_mainstream.php import find_php_files

        (tmp_path / "index.php").write_text("<?php echo 'hello'; ?>")
        (tmp_path / "other.txt").write_text("not php")

        files = list(find_php_files(tmp_path))

        assert len(files) == 1
        assert files[0].suffix == ".php"

    def test_excludes_vendor(self, tmp_path: Path) -> None:
        """Excludes vendor directory."""
        from hypergumbo_lang_mainstream.php import find_php_files

        (tmp_path / "app.php").write_text("<?php class App {} ?>")
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "pkg.php").write_text("<?php class Vendor {} ?>")

        files = list(find_php_files(tmp_path))

        assert len(files) == 1
        assert files[0].name == "app.php"


class TestPhpTreeSitterAvailability:
    """Tests for tree-sitter-php availability checking."""

    def test_is_php_tree_sitter_available(self) -> None:
        """Availability check returns a boolean."""
        from hypergumbo_lang_mainstream.php import is_php_tree_sitter_available

        result = is_php_tree_sitter_available()
        assert isinstance(result, bool)


class TestAnalyzePhpFallback:
    """Tests for fallback behavior when tree-sitter-php unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-php unavailable."""
        from hypergumbo_lang_mainstream import php as php_module

        (tmp_path / "test.php").write_text("<?php function foo() {} ?>")

        with patch.object(php_module._analyzer, "_check_grammar_available", return_value=False):
            result = php_module.analyze_php(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-php" in result.skip_reason


class TestPhpFunctionExtraction:
    """Tests for extracting PHP functions."""

    def test_extracts_function(self, tmp_path: Path) -> None:
        """Extracts PHP function declarations."""
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import analyze_php

        (tmp_path / "a.php").write_text("<?php function a() {} ?>")
        (tmp_path / "b.php").write_text("<?php function b() {} ?>")
        (tmp_path / "c.php").write_text("<?php function c() {} ?>")

        result = analyze_php(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 3
        assert result.run.pass_id == "php-v1"

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Handles repo with no PHP files."""
        from hypergumbo_lang_mainstream.php import analyze_php

        (tmp_path / "app.js").write_text("const x = 1;")

        result = analyze_php(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 0
        assert len(result.symbols) == 0


class TestPhpEdgeCases:
    """Tests for PHP edge cases and error handling."""

    def test_find_name_in_children_no_name(self) -> None:
        """Returns None when node has no 'name' child."""
        from hypergumbo_lang_mainstream.php import _find_name_in_children
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
        from hypergumbo_lang_mainstream.php import _get_php_parser

        # Mark tree-sitter modules as unavailable in sys.modules
        with patch.dict(sys.modules, {
            "tree_sitter": None,
            "tree_sitter_php": None,
        }):
            result = _get_php_parser()
            assert result is None

    def test_analyze_php_file_parser_unavailable(self, tmp_path: Path) -> None:
        """Returns failure when parser is unavailable."""
        from hypergumbo_lang_mainstream.php import _analyze_php_file
        from hypergumbo_core.ir import AnalysisRun

        php_file = tmp_path / "test.php"
        php_file.write_text("<?php function test() {} ?>")

        run = AnalysisRun.create(pass_id="test", version="test")

        with patch("hypergumbo_lang_mainstream.php._get_php_parser", return_value=None):
            symbols, edges, success = _analyze_php_file(php_file, run)

        assert success is False
        assert len(symbols) == 0

    def test_analyze_php_file_read_error(self, tmp_path: Path) -> None:
        """Returns failure when file cannot be read."""
        from hypergumbo_lang_mainstream.php import _analyze_php_file
        from hypergumbo_core.ir import AnalysisRun

        php_file = tmp_path / "missing.php"
        # Don't create the file

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_php_file(php_file, run)

        assert success is False
        assert len(symbols) == 0

    def test_php_file_skipped_increments_counter(self, tmp_path: Path) -> None:
        """PHP files that fail to read increment skipped counter."""
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import analyze_php

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


class TestPhpInferredMethodNoFanout:
    """Bare name fallback should emit at most one edge, not N per candidate."""

    def test_inferred_method_no_fanout(self, tmp_path: Path) -> None:
        """Multiple classes with same method name: emit at most 1 inferred edge.

        When multiple classes define the same method name and a call site can't
        be resolved via type info, the fallback should emit a single best-guess
        edge rather than fanning out to all candidates.
        """
        from hypergumbo_lang_mainstream.php import analyze_php

        (tmp_path / "app.php").write_text("""<?php
class CatsService {
    public function create($dto) {
        return $dto;
    }
}

class UsersService {
    public function create($dto) {
        return $dto;
    }
}

class OrdersService {
    public function create($dto) {
        return $dto;
    }
}

class Handler {
    public function run($unknown) {
        return $unknown->create(["name" => "test"]);
    }
}
?>""")

        result = analyze_php(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        inferred = [e for e in call_edges if e.evidence_type == "ast_method_inferred"]
        # Should emit at most 1 edge, NOT 3 (one per candidate)
        assert len(inferred) <= 1
        if inferred:
            assert "create" in inferred[0].dst


class TestPhpCrossFileResolution:
    """Tests for cross-file call resolution."""

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Resolves function calls across files."""
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import analyze_php

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


class TestUseAliasExtraction:
    """Tests for use statement alias extraction for disambiguation."""

    def test_extracts_simple_use(self, tmp_path: Path) -> None:
        """Extracts simple use statements using last component."""
        from hypergumbo_lang_mainstream.php import (
            _extract_use_aliases,
            is_php_tree_sitter_available,
            _get_php_parser,
        )

        if not is_php_tree_sitter_available():
            pytest.skip("tree-sitter-php not available")

        parser = _get_php_parser()
        if parser is None:
            pytest.skip("tree-sitter-php parser not available")

        php_file = tmp_path / "main.php"
        php_file.write_text(r"""<?php
namespace App;

use App\Services\UserService;
use App\Models\User;

class Main {
    public function run() {
        $svc = new UserService();
    }
}
?>""")

        source = php_file.read_bytes()
        tree = parser.parse(source)

        aliases = _extract_use_aliases(tree, source)

        # Last component of namespace path should be the short name
        assert "UserService" in aliases
        assert aliases["UserService"] == r"App\Services\UserService"
        assert "User" in aliases
        assert aliases["User"] == r"App\Models\User"

    def test_extracts_aliased_use(self, tmp_path: Path) -> None:
        """Extracts use statements with 'as' alias."""
        from hypergumbo_lang_mainstream.php import (
            _extract_use_aliases,
            is_php_tree_sitter_available,
            _get_php_parser,
        )

        if not is_php_tree_sitter_available():
            pytest.skip("tree-sitter-php not available")

        parser = _get_php_parser()
        if parser is None:
            pytest.skip("tree-sitter-php parser not available")

        php_file = tmp_path / "main.php"
        php_file.write_text(r"""<?php
namespace App;

use App\Services\UserService as Svc;

class Main {
    public function run() {
        $svc = new Svc();
    }
}
?>""")

        source = php_file.read_bytes()
        tree = parser.parse(source)

        aliases = _extract_use_aliases(tree, source)

        # Custom alias should be used
        assert "Svc" in aliases
        assert aliases["Svc"] == r"App\Services\UserService"


class TestPhpEdgeExtraction:
    """Tests for edge extraction edge cases."""

    def test_no_edges_outside_function(self, tmp_path: Path) -> None:
        """Calls outside functions/methods don't create edges."""
        from hypergumbo_lang_mainstream.php import analyze_php

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
        # Top-level call is now attributed to <module:filename> symbol
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        assert "<module:" in call_edges[0].src

    def test_nested_class_method(self, tmp_path: Path) -> None:
        """Handles nested method calls."""
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import _extract_symbols, _get_php_parser
        from hypergumbo_core.ir import AnalysisRun

        # This is an edge case - method_declaration outside class
        # Tree-sitter may parse it differently, but we handle it
        parser = _get_php_parser()
        assert parser is not None

        source = b"<?php function standalone() { return 1; } ?>"
        tree = parser.parse(source)
        run = AnalysisRun.create(pass_id="test", version="test")

        symbols = _extract_symbols(tree, source, tmp_path / "test.php", run)

        # Should extract function (module symbol also present)
        func_symbols = [s for s in symbols if s.kind == "function"]
        assert len(func_symbols) >= 1

    def test_analyze_php_file_success(self, tmp_path: Path) -> None:
        """_analyze_php_file returns symbols and edges on success."""
        from hypergumbo_lang_mainstream.php import _analyze_php_file
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream import php as php_module

        php_file = tmp_path / "test.php"
        php_file.write_text("<?php function test() {} ?>")

        # Mock _check_grammar_available to return True
        # but _get_php_parser to return None
        with patch.object(
            php_module._analyzer, "_check_grammar_available",
            return_value=True,
        ), patch(
            "hypergumbo_lang_mainstream.php._get_php_parser",
            return_value=None,
        ):
            result = php_module.analyze_php(tmp_path)

        assert result.run is not None
        assert result.skipped is True
        assert "tree-sitter-php" in result.skip_reason


class TestPHPSignatureExtraction:
    """Tests for PHP function signature extraction."""

    def test_typed_method_with_return_type(self, tmp_path: Path) -> None:
        """Extracts signature from method with typed params and return type."""
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import analyze_php

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
        from hypergumbo_lang_mainstream.php import analyze_php

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
# UsageContext Extraction Tests (ADR-0003 v1.1.x - YAML Pattern Support)
# ============================================================================


class TestLaravelUsageContextExtraction:
    """Tests for extracting Laravel route UsageContext records."""

    def test_extracts_get_route_usage_context(self, tmp_path: Path) -> None:
        """Extracts UsageContext for Route::get() calls."""
        from hypergumbo_lang_mainstream.php import analyze_php

        routes_file = tmp_path / "web.php"
        routes_file.write_text("""<?php
Route::get('/users', [UserController::class, 'index']);
?>""")

        result = analyze_php(tmp_path)

        # Should have UsageContext records
        assert len(result.usage_contexts) >= 1

        # Find the GET route context
        get_ctx = next(
            (c for c in result.usage_contexts if c.context_name == "get"), None
        )
        assert get_ctx is not None
        assert get_ctx.kind == "call"
        assert get_ctx.position == "args[0]"
        assert get_ctx.metadata is not None
        assert get_ctx.metadata.get("route_path") == "/users"
        assert get_ctx.metadata.get("http_method") == "GET"

    def test_extracts_post_route_usage_context(self, tmp_path: Path) -> None:
        """Extracts UsageContext for Route::post() calls."""
        from hypergumbo_lang_mainstream.php import analyze_php

        routes_file = tmp_path / "web.php"
        routes_file.write_text("""<?php
Route::post('/users', [UserController::class, 'store']);
?>""")

        result = analyze_php(tmp_path)

        post_ctx = next(
            (c for c in result.usage_contexts if c.context_name == "post"), None
        )
        assert post_ctx is not None
        assert post_ctx.metadata.get("http_method") == "POST"

    def test_extracts_resource_route_usage_context(self, tmp_path: Path) -> None:
        """Extracts UsageContext for Route::resource() calls."""
        from hypergumbo_lang_mainstream.php import analyze_php

        routes_file = tmp_path / "web.php"
        routes_file.write_text("""<?php
Route::resource('photos', PhotoController::class);
?>""")

        result = analyze_php(tmp_path)

        resource_ctx = next(
            (c for c in result.usage_contexts if c.context_name == "resource"), None
        )
        assert resource_ctx is not None
        assert resource_ctx.metadata.get("http_method") == "RESOURCE"
        # Path is normalized with leading /
        assert resource_ctx.metadata.get("route_path") == "/photos"

    def test_extracts_apiresource_route_usage_context(self, tmp_path: Path) -> None:
        """Extracts UsageContext for Route::apiResource() calls."""
        from hypergumbo_lang_mainstream.php import analyze_php

        routes_file = tmp_path / "web.php"
        routes_file.write_text("""<?php
Route::apiResource('posts', PostController::class);
?>""")

        result = analyze_php(tmp_path)

        api_ctx = next(
            (c for c in result.usage_contexts if c.context_name == "apiresource"), None
        )
        assert api_ctx is not None
        assert api_ctx.metadata.get("http_method") == "RESOURCE"

    def test_extracts_any_route_usage_context(self, tmp_path: Path) -> None:
        """Extracts UsageContext for Route::any() calls."""
        from hypergumbo_lang_mainstream.php import analyze_php

        routes_file = tmp_path / "web.php"
        routes_file.write_text("""<?php
Route::any('/catchall', [CatchAllController::class, 'handle']);
?>""")

        result = analyze_php(tmp_path)

        any_ctx = next(
            (c for c in result.usage_contexts if c.context_name == "any"), None
        )
        assert any_ctx is not None
        assert any_ctx.metadata.get("http_method") == "ANY"

    def test_match_route_with_array_first_arg_skipped(self, tmp_path: Path) -> None:
        """Route::match() with array first arg is skipped (path not extractable)."""
        from hypergumbo_lang_mainstream.php import analyze_php

        routes_file = tmp_path / "web.php"
        routes_file.write_text("""<?php
Route::match(['get', 'post'], '/form', [FormController::class, 'handle']);
?>""")

        result = analyze_php(tmp_path)

        # Route::match first arg is an array of methods, not a path
        # The current implementation expects first string arg to be path
        # so this gets skipped (no UsageContext created)
        match_ctx = next(
            (c for c in result.usage_contexts if c.context_name == "match"), None
        )
        # Currently skipped because first arg isn't a string
        assert match_ctx is None

    def test_skips_non_route_scoped_calls(self, tmp_path: Path) -> None:
        """Doesn't extract UsageContext for non-Route scoped calls."""
        from hypergumbo_lang_mainstream.php import analyze_php

        php_file = tmp_path / "app.php"
        php_file.write_text("""<?php
DB::get('/not-a-route', function() {});
OtherClass::post('/also-not-route');
?>""")

        result = analyze_php(tmp_path)

        # Should have no usage contexts (these aren't Route:: calls)
        assert len(result.usage_contexts) == 0

    def test_handles_missing_route_path(self, tmp_path: Path) -> None:
        """Handles Route calls without string path argument."""
        from hypergumbo_lang_mainstream.php import analyze_php

        php_file = tmp_path / "web.php"
        php_file.write_text("""<?php
// Route with variable path (no string literal)
Route::get($dynamicPath, function() {});
?>""")

        result = analyze_php(tmp_path)

        # Should NOT create UsageContext without a route path
        assert len(result.usage_contexts) == 0


class TestLaravelControllerExtraction:
    """Tests for extracting controller info from Laravel routes."""

    def test_extracts_array_style_controller(self, tmp_path: Path) -> None:
        """Extracts controller from [Controller::class, 'action'] syntax."""
        from hypergumbo_lang_mainstream.php import analyze_php

        routes_file = tmp_path / "web.php"
        routes_file.write_text("""<?php
Route::get('/users', [UserController::class, 'index']);
?>""")

        result = analyze_php(tmp_path)

        get_ctx = next(
            (c for c in result.usage_contexts if c.context_name == "get"), None
        )
        assert get_ctx is not None
        assert get_ctx.metadata.get("controller_action") == "UserController@index"

    def test_extracts_string_style_controller(self, tmp_path: Path) -> None:
        """Extracts controller from 'Controller@action' string syntax."""
        from hypergumbo_lang_mainstream.php import analyze_php

        routes_file = tmp_path / "web.php"
        routes_file.write_text("""<?php
Route::post('/login', 'AuthController@login');
?>""")

        result = analyze_php(tmp_path)

        post_ctx = next(
            (c for c in result.usage_contexts if c.context_name == "post"), None
        )
        assert post_ctx is not None
        assert post_ctx.metadata.get("controller_action") == "AuthController@login"


class TestLaravelRouteSymbols:
    """Tests for Laravel route Symbol extraction (enables route-handler linking)."""

    def test_route_symbols_created_for_http_methods(self, tmp_path: Path) -> None:
        """Laravel HTTP routes create Symbol objects with kind='route'."""
        from hypergumbo_lang_mainstream.php import analyze_php

        routes_file = tmp_path / "web.php"
        routes_file.write_text("""<?php
Route::get('/users', [UserController::class, 'index']);
Route::post('/login', 'AuthController@login');
?>""")

        result = analyze_php(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 2

        get_route = next((s for s in route_symbols if "GET" in s.name), None)
        assert get_route is not None
        assert get_route.name == "GET /users"
        assert get_route.meta["http_method"] == "GET"
        assert get_route.meta["route_path"] == "/users"
        assert get_route.meta["controller_action"] == "UserController@index"
        assert get_route.language == "php"

        post_route = next((s for s in route_symbols if "POST" in s.name), None)
        assert post_route is not None
        assert post_route.meta["controller_action"] == "AuthController@login"

    def test_route_symbols_for_resource_macro(self, tmp_path: Path) -> None:
        """Laravel resource routes create expanded RESTful route symbols."""
        from hypergumbo_lang_mainstream.php import analyze_php

        routes_file = tmp_path / "web.php"
        routes_file.write_text("""<?php
Route::resource('photos', PhotoController::class);
?>""")

        result = analyze_php(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        # Laravel resource creates 7 RESTful routes
        assert len(route_symbols) == 7

        routes_by_action = {s.meta["controller_action"]: s for s in route_symbols}

        # Collection routes
        assert "PhotoController@index" in routes_by_action
        assert routes_by_action["PhotoController@index"].meta["http_method"] == "GET"

        assert "PhotoController@create" in routes_by_action
        assert "PhotoController@store" in routes_by_action
        assert "PhotoController@show" in routes_by_action
        assert "PhotoController@edit" in routes_by_action
        assert "PhotoController@update" in routes_by_action
        assert "PhotoController@destroy" in routes_by_action


class TestPhpInheritanceEdges:
    """Tests for PHP base_classes metadata extraction.

    The inheritance linker creates edges from base_classes metadata.
    These tests verify that the PHP analyzer extracts base_classes correctly.
    """

    def test_class_extends_class_has_base_classes(self, tmp_path: Path) -> None:
        """Class extending another class has base_classes metadata."""
        from hypergumbo_lang_mainstream.php import analyze_php

        (tmp_path / "Models.php").write_text("""<?php
class BaseModel {
    public function save() {}
}

class User extends BaseModel {
    public function greet() {}
}
?>""")
        result = analyze_php(tmp_path)

        user_class = next(
            (s for s in result.symbols if s.name == "User" and s.kind == "class"),
            None,
        )
        assert user_class is not None
        assert user_class.meta is not None
        assert "base_classes" in user_class.meta
        assert "BaseModel" in user_class.meta["base_classes"]

    def test_class_implements_interface_has_base_classes(self, tmp_path: Path) -> None:
        """Class implementing interface has base_classes metadata."""
        from hypergumbo_lang_mainstream.php import analyze_php

        (tmp_path / "Models.php").write_text("""<?php
interface Serializable {
    public function serialize();
}

class User implements Serializable {
    public function serialize() { return ""; }
}
?>""")
        result = analyze_php(tmp_path)

        user_class = next(
            (s for s in result.symbols if s.name == "User" and s.kind == "class"),
            None,
        )
        assert user_class is not None
        assert user_class.meta is not None
        assert "base_classes" in user_class.meta
        assert "Serializable" in user_class.meta["base_classes"]

    def test_class_extends_and_implements_has_both(self, tmp_path: Path) -> None:
        """Class extending and implementing has both in base_classes."""
        from hypergumbo_lang_mainstream.php import analyze_php

        (tmp_path / "Models.php").write_text("""<?php
class BaseModel {}
interface Serializable {}
interface Comparable {}

class User extends BaseModel implements Serializable, Comparable {
    public function save() {}
}
?>""")
        result = analyze_php(tmp_path)

        user_class = next(
            (s for s in result.symbols if s.name == "User" and s.kind == "class"),
            None,
        )
        assert user_class is not None
        assert user_class.meta is not None
        assert "base_classes" in user_class.meta
        assert "BaseModel" in user_class.meta["base_classes"]
        assert "Serializable" in user_class.meta["base_classes"]
        assert "Comparable" in user_class.meta["base_classes"]

    def test_class_without_extends_has_no_base_classes(self, tmp_path: Path) -> None:
        """Class without extends/implements has no base_classes metadata."""
        from hypergumbo_lang_mainstream.php import analyze_php

        (tmp_path / "Simple.php").write_text("""<?php
class SimpleClass {
    public function method() {}
}
?>""")
        result = analyze_php(tmp_path)

        simple_class = next(
            (s for s in result.symbols if s.name == "SimpleClass" and s.kind == "class"),
            None,
        )
        assert simple_class is not None
        # No meta or no base_classes is fine
        if simple_class.meta:
            assert simple_class.meta.get("base_classes", []) == []

    def test_qualified_names_in_base_classes(self, tmp_path: Path) -> None:
        """Extracts fully qualified namespace names in extends/implements."""
        from hypergumbo_lang_mainstream.php import analyze_php

        (tmp_path / "Controller.php").write_text(r"""<?php
namespace App\Controllers;

class UserController extends \Illuminate\Routing\Controller implements \App\Contracts\UserInterface {
    public function index() {}
}
?>""")
        result = analyze_php(tmp_path)

        controller = next(
            (s for s in result.symbols if s.name == "UserController" and s.kind == "class"),
            None,
        )
        assert controller is not None
        assert controller.meta is not None
        assert "base_classes" in controller.meta
        # Should have both the qualified base class and interface
        assert r"\Illuminate\Routing\Controller" in controller.meta["base_classes"]
        assert r"\App\Contracts\UserInterface" in controller.meta["base_classes"]

    def test_linker_creates_extends_edge(self, tmp_path: Path) -> None:
        """Inheritance linker creates extends edge from base_classes."""
        from hypergumbo_lang_mainstream.php import analyze_php
        from hypergumbo_core.linkers.inheritance import link_inheritance
        from hypergumbo_core.linkers.registry import LinkerContext

        (tmp_path / "Models.php").write_text("""<?php
class BaseModel {
    public function save() {}
}

class User extends BaseModel {
    public function greet() {}
}
?>""")
        result = analyze_php(tmp_path)

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=result.symbols,
            edges=result.edges,
        )
        linker_result = link_inheritance(ctx)

        # Should create an extends edge
        extends_edges = [e for e in linker_result.edges if e.edge_type == "extends"]
        assert len(extends_edges) == 1
        assert "User" in extends_edges[0].src
        assert "BaseModel" in extends_edges[0].dst


class TestPhpAmbiguousMethodGuard:
    """Tests for AMB-METHOD invariant in PHP.

    When a method name has 3+ definitions across different classes and
    the receiver type cannot be inferred, the analyzer must NOT produce
    a resolved call edge (which would be a false positive).

    Invariant: Method calls with 3+ ambiguous receiver types must not
    produce resolved call edges.
    """

    def test_ambiguous_method_three_plus_classes_no_resolved_edge(
        self, tmp_path: Path,
    ) -> None:
        """$obj->close() with 3 classes defining close() → no resolved edge.

        When Server, Client, and Worker all define close(), and $obj's type
        cannot be inferred, the call should NOT produce a resolved edge
        pointing to any specific class's close() method.
        """
        from hypergumbo_lang_mainstream.php import analyze_php

        php_file = tmp_path / "multi.php"
        php_file.write_text("""<?php
class Server {
    public function close() { return true; }
}

class Client {
    public function close() { return true; }
}

class Worker {
    public function close() { return true; }
}

function cleanup($obj) {
    $obj->close();
}
?>""")

        result = analyze_php(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        cleanup_calls = [e for e in call_edges if "cleanup" in e.src]

        # Should NOT have a resolved edge to any specific class's close()
        for edge in cleanup_calls:
            if "close" in edge.dst.lower():
                # Accept either bare "close" or unresolved external edge
                assert edge.dst == "close" or edge.evidence_type == "unresolved_external_call", (
                    "Ambiguous method call should not resolve to a specific class, "
                    f"got {edge.dst}"
                )
                # If an edge exists, it should NOT be high confidence
                assert edge.confidence <= 0.50, (
                    f"Ambiguous method with 3+ candidates should have low confidence, "
                    f"got {edge.confidence}"
                )

    def test_two_classes_same_method_still_resolves(
        self, tmp_path: Path,
    ) -> None:
        """$obj->run() with 2 classes → still resolves (below threshold)."""
        from hypergumbo_lang_mainstream.php import analyze_php

        php_file = tmp_path / "two.php"
        php_file.write_text("""<?php
class Server {
    public function run() { return true; }
}

class Client {
    public function run() { return true; }
}

function execute($obj) {
    $obj->run();
}
?>""")

        result = analyze_php(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        execute_calls = [e for e in call_edges if "execute" in e.src]

        # 2 candidates is below the threshold — should still resolve
        run_calls = [e for e in execute_calls if "run" in e.dst.lower()]
        assert len(run_calls) >= 1, (
            "2 candidates should still resolve"
        )


class TestPHPVisibilityModifiers:
    """Tests for PHP visibility modifier extraction."""

    def test_method_visibility(self, tmp_path: Path) -> None:
        """Methods with visibility modifiers get them extracted."""
        from hypergumbo_lang_mainstream.php import analyze_php

        (tmp_path / "Vis.php").write_text("""<?php
class Vis {
    public function pubMethod() {}
    private function privMethod() {}
    protected function protMethod() {}
    public static function staticPub() {}
}
?>""")
        result = analyze_php(tmp_path)

        pub = next(s for s in result.symbols if s.name == "Vis.pubMethod")
        assert "public" in pub.modifiers

        priv = next(s for s in result.symbols if s.name == "Vis.privMethod")
        assert "private" in priv.modifiers

        prot = next(s for s in result.symbols if s.name == "Vis.protMethod")
        assert "protected" in prot.modifiers

        static_pub = next(s for s in result.symbols if s.name == "Vis.staticPub")
        assert "public" in static_pub.modifiers
        assert "static" in static_pub.modifiers

    def test_abstract_final_readonly_class(self, tmp_path: Path) -> None:
        """Abstract, final, readonly class modifiers are extracted."""
        from hypergumbo_lang_mainstream.php import analyze_php

        (tmp_path / "Mods.php").write_text("""<?php
abstract class Base {
    abstract function doWork();
}

final class Sealed {}

readonly class Data {}
?>""")
        result = analyze_php(tmp_path)

        base = next(s for s in result.symbols if s.name == "Base")
        assert "abstract" in base.modifiers

        do_work = next(s for s in result.symbols if s.name == "Base.doWork")
        assert "abstract" in do_work.modifiers

        sealed = next(s for s in result.symbols if s.name == "Sealed")
        assert "final" in sealed.modifiers

        data = next(s for s in result.symbols if s.name == "Data")
        assert "readonly" in data.modifiers


class TestNormalizePhpSignature:
    """Tests for PHP signature normalization (ADR-0014 §3)."""

    def test_basic_method(self) -> None:
        from hypergumbo_lang_mainstream.php import normalize_php_signature
        assert normalize_php_signature("(int $x, int $y): int") == "(int,int)int"

    def test_void_stripped(self) -> None:
        from hypergumbo_lang_mainstream.php import normalize_php_signature
        assert normalize_php_signature("(string $msg): void") == "(string)"

    def test_none(self) -> None:
        from hypergumbo_lang_mainstream.php import normalize_php_signature
        assert normalize_php_signature(None) is None


class TestPhpShapeId:
    """Tests for shape_id computation in PHP (ADR-0014 §1)."""

    def test_function_has_shape_id(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.php import analyze_php

        (tmp_path / "example.php").write_text(
            "<?php\nfunction greet(string $name): string { return 'Hello ' . $name; }\n"
        )
        result = analyze_php(tmp_path)
        func = next(s for s in result.symbols if s.kind == "function")
        assert func.shape_id is not None
        assert func.shape_id.startswith("sha256:")

    def test_class_has_shape_id(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.php import analyze_php

        (tmp_path / "example.php").write_text(
            "<?php\nclass Foo {\n  public function bar(): int { return 42; }\n}\n"
        )
        result = analyze_php(tmp_path)
        cls = next(s for s in result.symbols if s.kind == "class")
        assert cls.shape_id is not None
        assert cls.shape_id.startswith("sha256:")
