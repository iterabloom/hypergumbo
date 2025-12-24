"""Tests for JavaScript/TypeScript analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
import sys


class TestFindJsTsFiles:
    """Tests for JS/TS file discovery."""

    def test_finds_js_files(self, tmp_path: Path) -> None:
        """Finds .js files."""
        from hypergumbo.analyze.js_ts import find_js_ts_files

        (tmp_path / "app.js").write_text("const x = 1;")
        (tmp_path / "other.txt").write_text("not js")

        files = list(find_js_ts_files(tmp_path))

        assert len(files) == 1
        assert files[0].suffix == ".js"

    def test_finds_ts_files(self, tmp_path: Path) -> None:
        """Finds .ts files."""
        from hypergumbo.analyze.js_ts import find_js_ts_files

        (tmp_path / "app.ts").write_text("const x: number = 1;")

        files = list(find_js_ts_files(tmp_path))

        assert len(files) == 1
        assert files[0].suffix == ".ts"

    def test_finds_jsx_tsx_files(self, tmp_path: Path) -> None:
        """Finds .jsx and .tsx files."""
        from hypergumbo.analyze.js_ts import find_js_ts_files

        (tmp_path / "App.jsx").write_text("export default () => <div />;")
        (tmp_path / "App.tsx").write_text("export default () => <div />;")

        files = list(find_js_ts_files(tmp_path))

        assert len(files) == 2
        suffixes = {f.suffix for f in files}
        assert suffixes == {".jsx", ".tsx"}

    def test_excludes_node_modules(self, tmp_path: Path) -> None:
        """Excludes node_modules directory."""
        from hypergumbo.analyze.js_ts import find_js_ts_files

        (tmp_path / "app.js").write_text("const x = 1;")
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "pkg.js").write_text("module.exports = {};")

        files = list(find_js_ts_files(tmp_path))

        # Should only find app.js, not pkg.js in node_modules
        assert len(files) == 1
        assert files[0].name == "app.js"


class TestTreeSitterAvailability:
    """Tests for tree-sitter availability checking."""

    def test_is_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter is available."""
        from hypergumbo.analyze.js_ts import is_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()  # Non-None = available
            assert is_tree_sitter_available() is True

    def test_is_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.js_ts import is_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_tree_sitter_available() is False

    def test_is_tree_sitter_available_no_js_grammar(self) -> None:
        """Returns False when tree-sitter-javascript is not available."""
        from hypergumbo.analyze.js_ts import is_tree_sitter_available

        def mock_find_spec(name: str):
            if name == "tree_sitter":
                return object()  # tree_sitter is available
            return None  # tree_sitter_javascript is not

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_tree_sitter_available() is False


class TestAnalyzeJavascriptFallback:
    """Tests for fallback behavior when tree-sitter unavailable."""

    def test_returns_empty_when_tree_sitter_unavailable(self, tmp_path: Path) -> None:
        """Returns empty result with skipped pass when tree-sitter unavailable."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("function foo() {}")

        with patch("hypergumbo.analyze.js_ts.is_tree_sitter_available", return_value=False):
            result = analyze_javascript(tmp_path)

        assert result.symbols == []
        assert result.edges == []
        assert result.run is not None
        assert result.skipped is True
        assert "tree-sitter" in result.skip_reason.lower()


class TestAnalyzeJavascriptWithTreeSitter:
    """Tests for JS/TS analysis with tree-sitter."""

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        """Skip tests if tree-sitter not installed."""
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_javascript")

    def test_extracts_function_declaration(self, tmp_path: Path) -> None:
        """Extracts function declarations as symbols."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("function greet(name) {\n  return 'Hello ' + name;\n}")

        result = analyze_javascript(tmp_path)

        assert len(result.symbols) >= 1
        func_symbols = [s for s in result.symbols if s.kind == "function"]
        assert len(func_symbols) == 1
        assert func_symbols[0].name == "greet"
        assert func_symbols[0].language == "javascript"

    def test_extracts_arrow_function(self, tmp_path: Path) -> None:
        """Extracts arrow functions assigned to variables."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("const add = (a, b) => a + b;")

        result = analyze_javascript(tmp_path)

        func_symbols = [s for s in result.symbols if s.kind == "function"]
        assert len(func_symbols) == 1
        assert func_symbols[0].name == "add"

    def test_extracts_class_declaration(self, tmp_path: Path) -> None:
        """Extracts class declarations."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("class User {\n  constructor(name) {\n    this.name = name;\n  }\n}")

        result = analyze_javascript(tmp_path)

        class_symbols = [s for s in result.symbols if s.kind == "class"]
        assert len(class_symbols) == 1
        assert class_symbols[0].name == "User"

    def test_extracts_es6_import(self, tmp_path: Path) -> None:
        """Extracts ES6 import statements as edges."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("import { helper } from './utils';\n\nfunction main() { helper(); }")

        result = analyze_javascript(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1
        assert any("utils" in e.dst for e in import_edges)

    def test_extracts_require_call(self, tmp_path: Path) -> None:
        """Extracts CommonJS require() calls as edges."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("const fs = require('fs');\n\nfunction main() { fs.readFile('x'); }")

        result = analyze_javascript(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1
        assert any("fs" in e.dst for e in import_edges)

    def test_extracts_function_calls(self, tmp_path: Path) -> None:
        """Extracts function call edges."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        code = """
function helper() {
  return 42;
}

function main() {
  helper();
}
"""
        (tmp_path / "app.js").write_text(code)

        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1
        # main calls helper
        assert any("helper" in e.dst for e in call_edges)

    def test_typescript_with_types(self, tmp_path: Path) -> None:
        """Handles TypeScript files with type annotations."""
        pytest.importorskip("tree_sitter_typescript")

        from hypergumbo.analyze.js_ts import analyze_javascript

        code = """
interface User {
  name: string;
}

function greet(user: User): string {
  return 'Hello ' + user.name;
}
"""
        (tmp_path / "app.ts").write_text(code)

        result = analyze_javascript(tmp_path)

        func_symbols = [s for s in result.symbols if s.kind == "function"]
        assert len(func_symbols) >= 1
        assert any(s.name == "greet" for s in func_symbols)

    def test_jsx_component(self, tmp_path: Path) -> None:
        """Handles JSX component files."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        code = """
function App() {
  return <div>Hello</div>;
}

export default App;
"""
        (tmp_path / "App.jsx").write_text(code)

        result = analyze_javascript(tmp_path)

        func_symbols = [s for s in result.symbols if s.kind == "function"]
        assert any(s.name == "App" for s in func_symbols)

    def test_tracks_provenance(self, tmp_path: Path) -> None:
        """Sets origin and origin_run_id on symbols and edges."""
        from hypergumbo.analyze.js_ts import analyze_javascript, PASS_ID

        code = """
function foo() {}
function bar() { foo(); }
"""
        (tmp_path / "app.js").write_text(code)

        result = analyze_javascript(tmp_path)

        assert result.run is not None
        assert result.run.pass_id == PASS_ID

        for symbol in result.symbols:
            assert symbol.origin == PASS_ID
            assert symbol.origin_run_id == result.run.execution_id

        for edge in result.edges:
            assert edge.origin == PASS_ID
            assert edge.origin_run_id == result.run.execution_id

    def test_import_edge_confidence(self, tmp_path: Path) -> None:
        """Import edges have appropriate confidence scores."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("import { x } from './utils';")

        result = analyze_javascript(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1
        # Static imports should have high confidence
        for edge in import_edges:
            assert edge.confidence >= 0.9

    def test_require_edge_evidence_type(self, tmp_path: Path) -> None:
        """Require calls have correct evidence type."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("const x = require('./utils');")

        result = analyze_javascript(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1
        assert any(e.evidence_type == "require_static" for e in import_edges)

    def test_dynamic_import_lower_confidence(self, tmp_path: Path) -> None:
        """Dynamic imports/requires have lower confidence."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        code = """
const name = 'utils';
const x = require(name);
"""
        (tmp_path / "app.js").write_text(code)

        result = analyze_javascript(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Dynamic require should have lower confidence
        dynamic_edges = [e for e in import_edges if e.evidence_type == "require_dynamic"]
        if dynamic_edges:
            assert all(e.confidence <= 0.5 for e in dynamic_edges)

    def test_handles_syntax_errors(self, tmp_path: Path) -> None:
        """Gracefully handles files with syntax errors."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "good.js").write_text("function foo() {}")
        (tmp_path / "bad.js").write_text("function { broken")

        result = analyze_javascript(tmp_path)

        # Tree-sitter has error recovery, so both files are analyzed
        # Should still extract from good file
        assert result.run is not None
        assert result.run.files_analyzed >= 1
        # Should find foo function from good.js
        func_names = [s.name for s in result.symbols if s.kind == "function"]
        assert "foo" in func_names

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        """Analysis run has correct metadata."""
        from hypergumbo.analyze.js_ts import analyze_javascript, PASS_ID, PASS_VERSION

        (tmp_path / "app.js").write_text("function foo() {}")

        result = analyze_javascript(tmp_path)

        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.version == PASS_VERSION
        assert result.run.files_analyzed >= 1
        assert result.run.duration_ms >= 0

    def test_symbol_has_span(self, tmp_path: Path) -> None:
        """Symbols include span information."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("function foo() {\n  return 1;\n}")

        result = analyze_javascript(tmp_path)

        func_symbols = [s for s in result.symbols if s.name == "foo"]
        assert len(func_symbols) == 1
        assert func_symbols[0].span.start_line >= 1
        assert func_symbols[0].span.end_line >= func_symbols[0].span.start_line

    def test_exports_default_function(self, tmp_path: Path) -> None:
        """Handles export default function syntax."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("export default function handler() { return 1; }")

        result = analyze_javascript(tmp_path)

        func_symbols = [s for s in result.symbols if s.kind == "function"]
        assert len(func_symbols) >= 1

    def test_exports_class_declaration(self, tmp_path: Path) -> None:
        """Handles export class syntax."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("export class ApiClient { fetch() { return 1; } }")

        result = analyze_javascript(tmp_path)

        class_symbols = [s for s in result.symbols if s.kind == "class"]
        assert len(class_symbols) == 1
        assert class_symbols[0].name == "ApiClient"

    def test_typescript_exports_class(self, tmp_path: Path) -> None:
        """Handles TypeScript export class syntax."""
        pytest.importorskip("tree_sitter_typescript")

        from hypergumbo.analyze.js_ts import analyze_javascript

        code = """
export class ApiClient {
  private config: string;

  constructor(config: string) {
    this.config = config;
  }

  async fetchUser(id: number): Promise<any> {
    return fetch(this.config + '/users/' + id);
  }
}
"""
        (tmp_path / "api.ts").write_text(code)

        result = analyze_javascript(tmp_path)

        class_symbols = [s for s in result.symbols if s.kind == "class"]
        assert len(class_symbols) == 1
        assert class_symbols[0].name == "ApiClient"
        assert class_symbols[0].language == "typescript"

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Handles empty directories gracefully."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        result = analyze_javascript(tmp_path)

        assert result.symbols == []
        assert result.edges == []
        assert result.run is not None
        assert result.run.files_analyzed == 0


class TestMockedTreeSitter:
    """Tests that mock tree-sitter for code coverage."""

    def _create_mock_node(
        self,
        node_type: str,
        start_byte: int = 0,
        end_byte: int = 10,
        start_point: tuple = (0, 0),
        end_point: tuple = (0, 10),
        children: list = None,
        has_error: bool = False,
    ) -> MagicMock:
        """Create a mock tree-sitter node."""
        node = MagicMock()
        node.type = node_type
        node.start_byte = start_byte
        node.end_byte = end_byte
        node.start_point = start_point
        node.end_point = end_point
        node.children = children or []
        node.has_error = has_error
        return node

    def test_get_parser_for_js_file(self, tmp_path: Path) -> None:
        """Gets JavaScript parser for .js files."""
        from hypergumbo.analyze.js_ts import _get_parser_for_file

        js_file = tmp_path / "app.js"
        js_file.write_text("const x = 1;")

        # Mock tree-sitter modules
        mock_ts = MagicMock()
        mock_ts_js = MagicMock()
        mock_parser = MagicMock()
        mock_ts.Parser.return_value = mock_parser
        mock_lang = MagicMock()
        mock_ts_js.language.return_value = mock_lang

        with patch.dict(sys.modules, {
            "tree_sitter": mock_ts,
            "tree_sitter_javascript": mock_ts_js,
        }):
            parser = _get_parser_for_file(js_file)

        assert parser is not None
        mock_ts_js.language.assert_called_once()

    def test_get_parser_for_ts_file(self, tmp_path: Path) -> None:
        """Gets TypeScript parser for .ts files."""
        from hypergumbo.analyze.js_ts import _get_parser_for_file

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("const x: number = 1;")

        mock_ts = MagicMock()
        mock_ts_js = MagicMock()
        mock_ts_typescript = MagicMock()
        mock_parser = MagicMock()
        mock_ts.Parser.return_value = mock_parser

        with patch.dict(sys.modules, {
            "tree_sitter": mock_ts,
            "tree_sitter_javascript": mock_ts_js,
            "tree_sitter_typescript": mock_ts_typescript,
        }):
            parser = _get_parser_for_file(ts_file)

        assert parser is not None
        mock_ts_typescript.language_typescript.assert_called_once()

    def test_get_parser_for_tsx_file(self, tmp_path: Path) -> None:
        """Gets TSX parser for .tsx files."""
        from hypergumbo.analyze.js_ts import _get_parser_for_file

        tsx_file = tmp_path / "App.tsx"
        tsx_file.write_text("const App = () => <div />;")

        mock_ts = MagicMock()
        mock_ts_js = MagicMock()
        mock_ts_typescript = MagicMock()
        mock_parser = MagicMock()
        mock_ts.Parser.return_value = mock_parser

        with patch.dict(sys.modules, {
            "tree_sitter": mock_ts,
            "tree_sitter_javascript": mock_ts_js,
            "tree_sitter_typescript": mock_ts_typescript,
        }):
            parser = _get_parser_for_file(tsx_file)

        assert parser is not None
        mock_ts_typescript.language_tsx.assert_called_once()

    def test_get_parser_ts_fallback_to_js(self, tmp_path: Path) -> None:
        """Falls back to JS parser when TS grammar not available."""
        from hypergumbo.analyze.js_ts import _get_parser_for_file

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("const x = 1;")

        mock_ts = MagicMock()
        mock_ts_js = MagicMock()
        mock_parser = MagicMock()
        mock_ts.Parser.return_value = mock_parser

        # Simulate ts_typescript not being available
        with patch.dict(sys.modules, {
            "tree_sitter": mock_ts,
            "tree_sitter_javascript": mock_ts_js,
            "tree_sitter_typescript": None,
        }):
            # Import fails for tree_sitter_typescript
            import builtins
            original_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name == "tree_sitter_typescript":
                    raise ImportError("No module")
                return original_import(name, *args, **kwargs)

            with patch.object(builtins, "__import__", mock_import):
                parser = _get_parser_for_file(ts_file)

        assert parser is not None
        mock_ts_js.language.assert_called()

    def test_get_parser_no_tree_sitter(self, tmp_path: Path) -> None:
        """Returns None when tree-sitter not available."""
        from hypergumbo.analyze.js_ts import _get_parser_for_file

        js_file = tmp_path / "app.js"
        js_file.write_text("const x = 1;")

        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name in ("tree_sitter", "tree_sitter_javascript"):
                raise ImportError("No module")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", mock_import):
            parser = _get_parser_for_file(js_file)

        assert parser is None

    def test_node_text_helper(self) -> None:
        """Tests _node_text helper function."""
        from hypergumbo.analyze.js_ts import _node_text

        node = MagicMock()
        node.start_byte = 0
        node.end_byte = 5
        source = b"hello world"

        text = _node_text(node, source)

        assert text == "hello"

    def test_find_name_in_children(self) -> None:
        """Tests _find_name_in_children helper function."""
        from hypergumbo.analyze.js_ts import _find_name_in_children

        # Child with identifier type
        identifier_child = MagicMock()
        identifier_child.type = "identifier"
        identifier_child.start_byte = 0
        identifier_child.end_byte = 3

        node = MagicMock()
        node.children = [identifier_child]

        source = b"foo"
        name = _find_name_in_children(node, source)

        assert name == "foo"

    def test_find_name_in_children_property(self) -> None:
        """Tests _find_name_in_children with property_identifier."""
        from hypergumbo.analyze.js_ts import _find_name_in_children

        prop_child = MagicMock()
        prop_child.type = "property_identifier"
        prop_child.start_byte = 0
        prop_child.end_byte = 3

        node = MagicMock()
        node.children = [prop_child]

        source = b"bar"
        name = _find_name_in_children(node, source)

        assert name == "bar"

    def test_find_name_in_children_none(self) -> None:
        """Returns None when no identifier found."""
        from hypergumbo.analyze.js_ts import _find_name_in_children

        other_child = MagicMock()
        other_child.type = "other"

        node = MagicMock()
        node.children = [other_child]

        source = b"something"
        name = _find_name_in_children(node, source)

        assert name is None

    def test_find_name_in_children_type_identifier(self) -> None:
        """Finds type_identifier for TypeScript classes."""
        from hypergumbo.analyze.js_ts import _find_name_in_children

        type_id_child = MagicMock()
        type_id_child.type = "type_identifier"
        type_id_child.start_byte = 0
        type_id_child.end_byte = 9

        node = MagicMock()
        node.children = [type_id_child]

        source = b"ApiClient"
        name = _find_name_in_children(node, source)

        assert name == "ApiClient"

    def test_get_language_for_file(self, tmp_path: Path) -> None:
        """Tests language detection based on file extension."""
        from hypergumbo.analyze.js_ts import _get_language_for_file

        assert _get_language_for_file(tmp_path / "app.js") == "javascript"
        assert _get_language_for_file(tmp_path / "app.jsx") == "javascript"
        assert _get_language_for_file(tmp_path / "app.ts") == "typescript"
        assert _get_language_for_file(tmp_path / "app.tsx") == "typescript"

    def test_make_symbol_id(self) -> None:
        """Tests symbol ID generation."""
        from hypergumbo.analyze.js_ts import _make_symbol_id

        symbol_id = _make_symbol_id("app.js", 1, 5, "foo", "function", "javascript")

        assert symbol_id == "javascript:app.js:1-5:foo:function"

    def test_analyze_file_returns_failure_on_io_error(self, tmp_path: Path) -> None:
        """Returns failure when file cannot be read."""
        from hypergumbo.analyze.js_ts import _analyze_file
        from hypergumbo.ir import AnalysisRun

        run = AnalysisRun.create(pass_id="test", version="1.0")
        nonexistent = tmp_path / "nonexistent.js"

        # Mock parser to be available
        with patch("hypergumbo.analyze.js_ts._get_parser_for_file") as mock_get_parser:
            mock_get_parser.return_value = MagicMock()

            symbols, edges, success = _analyze_file(nonexistent, run)

        assert success is False
        assert symbols == []
        assert edges == []

    def test_analyze_file_returns_failure_when_no_parser(self, tmp_path: Path) -> None:
        """Returns failure when parser not available."""
        from hypergumbo.analyze.js_ts import _analyze_file
        from hypergumbo.ir import AnalysisRun

        run = AnalysisRun.create(pass_id="test", version="1.0")
        js_file = tmp_path / "app.js"
        js_file.write_text("const x = 1;")

        with patch("hypergumbo.analyze.js_ts._get_parser_for_file", return_value=None):
            symbols, edges, success = _analyze_file(js_file, run)

        assert success is False

    def test_analyze_javascript_with_mocked_tree_sitter(self, tmp_path: Path) -> None:
        """Tests full analysis with mocked tree-sitter."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("function foo() {}")

        # Create mock tree structure
        root_node = self._create_mock_node("program")
        func_node = self._create_mock_node(
            "function_declaration",
            start_point=(0, 0),
            end_point=(0, 18),
        )
        id_node = self._create_mock_node("identifier", start_byte=9, end_byte=12)
        func_node.children = [id_node]
        root_node.children = [func_node]

        mock_tree = MagicMock()
        mock_tree.root_node = root_node

        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_tree

        with patch("hypergumbo.analyze.js_ts.is_tree_sitter_available", return_value=True):
            with patch("hypergumbo.analyze.js_ts._get_parser_for_file", return_value=mock_parser):
                result = analyze_javascript(tmp_path)

        assert result.skipped is False
        assert result.run is not None
        assert result.run.files_analyzed == 1

    def test_extract_symbols_function_declaration(self) -> None:
        """Tests extraction of function declarations."""
        from hypergumbo.analyze.js_ts import _extract_symbols_and_edges
        from hypergumbo.ir import AnalysisRun

        source = b"function greet(name) { return name; }"
        run = AnalysisRun.create(pass_id="test", version="1.0")

        # Create mock tree
        id_node = self._create_mock_node("identifier", start_byte=9, end_byte=14)
        func_node = self._create_mock_node(
            "function_declaration",
            start_point=(0, 0),
            end_point=(0, 37),
            children=[id_node],
        )
        root = self._create_mock_node("program", children=[func_node])
        tree = MagicMock()
        tree.root_node = root

        symbols, edges = _extract_symbols_and_edges(
            tree, source, Path("app.js"), "javascript", run
        )

        assert len(symbols) == 1
        assert symbols[0].name == "greet"
        assert symbols[0].kind == "function"

    def test_extract_symbols_class_declaration(self) -> None:
        """Tests extraction of class declarations."""
        from hypergumbo.analyze.js_ts import _extract_symbols_and_edges
        from hypergumbo.ir import AnalysisRun

        source = b"class User { }"
        run = AnalysisRun.create(pass_id="test", version="1.0")

        id_node = self._create_mock_node("identifier", start_byte=6, end_byte=10)
        class_node = self._create_mock_node(
            "class_declaration",
            start_point=(0, 0),
            end_point=(0, 14),
            children=[id_node],
        )
        root = self._create_mock_node("program", children=[class_node])
        tree = MagicMock()
        tree.root_node = root

        symbols, edges = _extract_symbols_and_edges(
            tree, source, Path("app.js"), "javascript", run
        )

        assert len(symbols) == 1
        assert symbols[0].name == "User"
        assert symbols[0].kind == "class"

    def test_extract_arrow_function(self) -> None:
        """Tests extraction of arrow functions assigned to const."""
        from hypergumbo.analyze.js_ts import _extract_symbols_and_edges
        from hypergumbo.ir import AnalysisRun

        source = b"const add = (a, b) => a + b;"
        run = AnalysisRun.create(pass_id="test", version="1.0")

        id_node = self._create_mock_node("identifier", start_byte=6, end_byte=9)
        arrow_node = self._create_mock_node(
            "arrow_function",
            start_point=(0, 12),
            end_point=(0, 27),
            children=[],
        )
        declarator = self._create_mock_node(
            "variable_declarator",
            children=[id_node, arrow_node],
        )
        lexical = self._create_mock_node(
            "lexical_declaration",
            children=[declarator],
        )
        root = self._create_mock_node("program", children=[lexical])
        tree = MagicMock()
        tree.root_node = root

        symbols, edges = _extract_symbols_and_edges(
            tree, source, Path("app.js"), "javascript", run
        )

        assert len(symbols) == 1
        assert symbols[0].name == "add"
        assert symbols[0].kind == "function"

    def test_extract_arrow_function_with_body(self) -> None:
        """Tests extraction of arrow functions with nested calls."""
        from hypergumbo.analyze.js_ts import _extract_symbols_and_edges
        from hypergumbo.ir import AnalysisRun

        source = b"function helper() {} const add = (a, b) => { helper(); return a + b; };"
        run = AnalysisRun.create(pass_id="test", version="1.0")

        # Helper function
        helper_id = self._create_mock_node("identifier", start_byte=9, end_byte=15)
        helper_func = self._create_mock_node(
            "function_declaration",
            start_point=(0, 0),
            end_point=(0, 20),
            children=[helper_id],
        )

        # Call to helper inside arrow function
        call_id = self._create_mock_node("identifier", start_byte=45, end_byte=51)
        args = self._create_mock_node("arguments", children=[])
        call_node = self._create_mock_node(
            "call_expression",
            start_point=(0, 45),
            end_point=(0, 53),
            children=[call_id, args],
        )

        # Arrow function with body containing call
        arrow_id = self._create_mock_node("identifier", start_byte=27, end_byte=30)
        arrow_node = self._create_mock_node(
            "arrow_function",
            start_point=(0, 33),
            end_point=(0, 70),
            children=[call_node],
        )
        declarator = self._create_mock_node(
            "variable_declarator",
            children=[arrow_id, arrow_node],
        )
        lexical = self._create_mock_node(
            "lexical_declaration",
            children=[declarator],
        )

        root = self._create_mock_node("program", children=[helper_func, lexical])
        tree = MagicMock()
        tree.root_node = root

        symbols, edges = _extract_symbols_and_edges(
            tree, source, Path("app.js"), "javascript", run
        )

        # Should have helper function and add arrow function
        func_symbols = [s for s in symbols if s.kind == "function"]
        assert len(func_symbols) == 2

        # Should have call edge from add to helper
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1

    def test_extract_es6_import(self) -> None:
        """Tests extraction of ES6 import statements."""
        from hypergumbo.analyze.js_ts import _extract_symbols_and_edges
        from hypergumbo.ir import AnalysisRun

        source = b"import { helper } from './utils';"
        run = AnalysisRun.create(pass_id="test", version="1.0")

        string_node = self._create_mock_node("string", start_byte=23, end_byte=32)
        import_node = self._create_mock_node(
            "import_statement",
            start_point=(0, 0),
            end_point=(0, 33),
            children=[string_node],
        )
        root = self._create_mock_node("program", children=[import_node])
        tree = MagicMock()
        tree.root_node = root

        symbols, edges = _extract_symbols_and_edges(
            tree, source, Path("app.js"), "javascript", run
        )

        assert len(edges) == 1
        assert edges[0].edge_type == "imports"
        assert edges[0].evidence_type == "import_static"
        assert edges[0].confidence == 0.95

    def test_extract_require_static(self) -> None:
        """Tests extraction of static require() calls."""
        from hypergumbo.analyze.js_ts import _extract_symbols_and_edges
        from hypergumbo.ir import AnalysisRun

        source = b"const fs = require('fs');"
        run = AnalysisRun.create(pass_id="test", version="1.0")

        id_node = self._create_mock_node("identifier", start_byte=11, end_byte=18)
        string_node = self._create_mock_node("string", start_byte=19, end_byte=23)
        args_node = self._create_mock_node("arguments", children=[string_node])
        call_node = self._create_mock_node(
            "call_expression",
            start_point=(0, 11),
            end_point=(0, 24),
            children=[id_node, args_node],
        )
        root = self._create_mock_node("program", children=[call_node])
        tree = MagicMock()
        tree.root_node = root

        symbols, edges = _extract_symbols_and_edges(
            tree, source, Path("app.js"), "javascript", run
        )

        assert len(edges) == 1
        assert edges[0].edge_type == "imports"
        assert edges[0].evidence_type == "require_static"
        assert edges[0].confidence == 0.90

    def test_extract_require_dynamic(self) -> None:
        """Tests extraction of dynamic require() calls."""
        from hypergumbo.analyze.js_ts import _extract_symbols_and_edges
        from hypergumbo.ir import AnalysisRun

        source = b"const m = require(name);"
        run = AnalysisRun.create(pass_id="test", version="1.0")

        require_id = self._create_mock_node("identifier", start_byte=10, end_byte=17)
        var_id = self._create_mock_node("identifier", start_byte=18, end_byte=22)
        args_node = self._create_mock_node("arguments", children=[var_id])
        call_node = self._create_mock_node(
            "call_expression",
            start_point=(0, 10),
            end_point=(0, 23),
            children=[require_id, args_node],
        )
        root = self._create_mock_node("program", children=[call_node])
        tree = MagicMock()
        tree.root_node = root

        symbols, edges = _extract_symbols_and_edges(
            tree, source, Path("app.js"), "javascript", run
        )

        assert len(edges) == 1
        assert edges[0].evidence_type == "require_dynamic"
        assert edges[0].confidence == 0.40

    def test_extract_function_call(self) -> None:
        """Tests extraction of function calls within functions."""
        from hypergumbo.analyze.js_ts import _extract_symbols_and_edges
        from hypergumbo.ir import AnalysisRun

        source = b"function helper() {} function main() { helper(); }"
        run = AnalysisRun.create(pass_id="test", version="1.0")

        # Helper function
        helper_id = self._create_mock_node("identifier", start_byte=9, end_byte=15)
        helper_func = self._create_mock_node(
            "function_declaration",
            start_point=(0, 0),
            end_point=(0, 20),
            children=[helper_id],
        )

        # Call to helper inside main
        call_id = self._create_mock_node("identifier", start_byte=39, end_byte=45)
        args = self._create_mock_node("arguments", children=[])
        call_node = self._create_mock_node(
            "call_expression",
            start_point=(0, 39),
            end_point=(0, 47),
            children=[call_id, args],
        )

        # Main function with call inside
        main_id = self._create_mock_node("identifier", start_byte=30, end_byte=34)
        main_func = self._create_mock_node(
            "function_declaration",
            start_point=(0, 21),
            end_point=(0, 50),
            children=[main_id, call_node],
        )

        root = self._create_mock_node("program", children=[helper_func, main_func])
        tree = MagicMock()
        tree.root_node = root

        symbols, edges = _extract_symbols_and_edges(
            tree, source, Path("app.js"), "javascript", run
        )

        # Should have 2 functions and 1 call edge
        func_symbols = [s for s in symbols if s.kind == "function"]
        call_edges = [e for e in edges if e.edge_type == "calls"]

        assert len(func_symbols) == 2
        assert len(call_edges) == 1
        assert call_edges[0].evidence_type == "ast_call_direct"

    def test_extract_export_default_function(self) -> None:
        """Tests extraction of export default function."""
        from hypergumbo.analyze.js_ts import _extract_symbols_and_edges
        from hypergumbo.ir import AnalysisRun

        source = b"export default function handler() {}"
        run = AnalysisRun.create(pass_id="test", version="1.0")

        id_node = self._create_mock_node("identifier", start_byte=24, end_byte=31)
        func_node = self._create_mock_node(
            "function_declaration",
            start_point=(0, 15),
            end_point=(0, 36),
            children=[id_node],
        )
        export_node = self._create_mock_node(
            "export_statement",
            start_point=(0, 0),
            end_point=(0, 36),
            children=[func_node],
        )
        root = self._create_mock_node("program", children=[export_node])
        tree = MagicMock()
        tree.root_node = root

        symbols, edges = _extract_symbols_and_edges(
            tree, source, Path("app.js"), "javascript", run
        )

        assert len(symbols) == 1
        assert symbols[0].name == "handler"
        assert symbols[0].kind == "function"

    def test_analyze_with_parse_errors(self, tmp_path: Path) -> None:
        """Continues analysis even with parse errors."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("function { broken")

        # Mock tree-sitter with error node
        root = self._create_mock_node("program", has_error=True)
        tree = MagicMock()
        tree.root_node = root

        mock_parser = MagicMock()
        mock_parser.parse.return_value = tree

        with patch("hypergumbo.analyze.js_ts.is_tree_sitter_available", return_value=True):
            with patch("hypergumbo.analyze.js_ts._get_parser_for_file", return_value=mock_parser):
                result = analyze_javascript(tmp_path)

        # Should still succeed but with limited results
        assert result.run is not None
        assert result.run.files_analyzed == 1

    def test_analyze_with_file_errors(self, tmp_path: Path) -> None:
        """Tracks files that fail to parse."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "good.js").write_text("function foo() {}")
        (tmp_path / "bad.js").write_text("broken")

        # Mock: good.js succeeds, bad.js fails
        root = self._create_mock_node("program")
        id_node = self._create_mock_node("identifier", start_byte=9, end_byte=12)
        func_node = self._create_mock_node(
            "function_declaration",
            start_point=(0, 0),
            end_point=(0, 17),
            children=[id_node],
        )
        root.children = [func_node]
        tree = MagicMock()
        tree.root_node = root

        call_count = [0]

        def mock_analyze_file(file_path, run):
            call_count[0] += 1
            if "bad" in str(file_path):
                return [], [], False  # Simulate failure
            # Return success for good.js
            from hypergumbo.ir import Symbol, Span
            s = Symbol(
                id="js:good.js:1-1:foo:function",
                name="foo",
                kind="function",
                language="javascript",
                path=str(file_path),
                span=Span(start_line=1, end_line=1, start_col=0, end_col=17),
            )
            return [s], [], True

        with patch("hypergumbo.analyze.js_ts.is_tree_sitter_available", return_value=True):
            with patch("hypergumbo.analyze.js_ts._analyze_file", side_effect=mock_analyze_file):
                result = analyze_javascript(tmp_path)

        # Should have analyzed 2 files
        assert call_count[0] == 2
        assert result.run is not None
        assert result.run.files_analyzed == 1
        assert result.run.files_skipped == 1
