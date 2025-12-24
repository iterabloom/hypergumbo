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

    def test_extracts_class_methods(self, tmp_path: Path) -> None:
        """Extracts methods from class declarations."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        code = """
class UserService {
  constructor(db) {
    this.db = db;
  }

  async createUser(email) {
    return { email };
  }

  static validate(data) {
    return true;
  }
}
"""
        (tmp_path / "app.js").write_text(code)

        result = analyze_javascript(tmp_path)

        method_symbols = [s for s in result.symbols if s.kind == "method"]
        method_names = [m.name for m in method_symbols]

        # Method names now include class prefix
        assert "UserService.constructor" in method_names
        assert "UserService.createUser" in method_names
        assert "UserService.validate" in method_names
        assert len(method_symbols) == 3

    def test_extracts_getters_and_setters(self, tmp_path: Path) -> None:
        """Extracts getters and setters from class declarations."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        code = """
class User {
  constructor(name) {
    this._name = name;
  }

  get name() {
    return this._name;
  }

  set name(value) {
    this._name = value;
  }

  get age() {
    return 0;
  }
}
"""
        (tmp_path / "app.js").write_text(code)

        result = analyze_javascript(tmp_path)

        getter_symbols = [s for s in result.symbols if s.kind == "getter"]
        setter_symbols = [s for s in result.symbols if s.kind == "setter"]

        getter_names = [g.name for g in getter_symbols]
        setter_names = [s.name for s in setter_symbols]

        # Getter/setter names now include class prefix
        assert "User.name" in getter_names
        assert "User.age" in getter_names
        assert len(getter_symbols) == 2

        assert "User.name" in setter_names
        assert len(setter_symbols) == 1

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

    def test_typescript_interfaces(self, tmp_path: Path) -> None:
        """Extracts TypeScript interface declarations."""
        pytest.importorskip("tree_sitter_typescript")

        from hypergumbo.analyze.js_ts import analyze_javascript

        code = """
interface User {
  id: number;
  name: string;
}

interface ApiResponse<T> {
  data: T;
  status: number;
}

export interface Config {
  apiUrl: string;
  timeout: number;
}
"""
        (tmp_path / "types.ts").write_text(code)

        result = analyze_javascript(tmp_path)

        interface_symbols = [s for s in result.symbols if s.kind == "interface"]
        interface_names = [i.name for i in interface_symbols]

        assert "User" in interface_names
        assert "ApiResponse" in interface_names
        assert "Config" in interface_names
        assert len(interface_symbols) == 3

        # Verify language is TypeScript
        for iface in interface_symbols:
            assert iface.language == "typescript"

    def test_typescript_type_aliases(self, tmp_path: Path) -> None:
        """Extracts TypeScript type alias declarations."""
        pytest.importorskip("tree_sitter_typescript")

        from hypergumbo.analyze.js_ts import analyze_javascript

        code = """
type UserId = string;

type Result<T> = {
  data: T;
  error?: string;
};

export type Config = {
  apiUrl: string;
};
"""
        (tmp_path / "types.ts").write_text(code)

        result = analyze_javascript(tmp_path)

        type_symbols = [s for s in result.symbols if s.kind == "type"]
        type_names = [t.name for t in type_symbols]

        assert "UserId" in type_names
        assert "Result" in type_names
        assert "Config" in type_names
        assert len(type_symbols) == 3

    def test_typescript_enums(self, tmp_path: Path) -> None:
        """Extracts TypeScript enum declarations."""
        pytest.importorskip("tree_sitter_typescript")

        from hypergumbo.analyze.js_ts import analyze_javascript

        code = """
enum Status {
  Active,
  Inactive,
  Pending
}

export enum Color {
  Red = "red",
  Green = "green"
}

const enum Direction {
  Up,
  Down
}
"""
        (tmp_path / "enums.ts").write_text(code)

        result = analyze_javascript(tmp_path)

        enum_symbols = [s for s in result.symbols if s.kind == "enum"]
        enum_names = [e.name for e in enum_symbols]

        assert "Status" in enum_names
        assert "Color" in enum_names
        assert "Direction" in enum_names
        assert len(enum_symbols) == 3

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

    def test_extract_class_with_methods_builds_registry(self) -> None:
        """Tests that method registry is built correctly for cross-file resolution."""
        from hypergumbo.analyze.js_ts import _extract_symbols_and_edges
        from hypergumbo.ir import AnalysisRun

        source = b"class Svc { save() {} }"
        run = AnalysisRun.create(pass_id="test", version="1.0")

        class_id = self._create_mock_node("identifier", start_byte=6, end_byte=9)
        method_id = self._create_mock_node("property_identifier", start_byte=12, end_byte=16)
        method_node = self._create_mock_node(
            "method_definition",
            start_point=(0, 12),
            end_point=(0, 21),
            children=[method_id],
        )
        class_body = self._create_mock_node("class_body", children=[method_node])
        class_node = self._create_mock_node(
            "class_declaration",
            start_point=(0, 0),
            end_point=(0, 23),
            children=[class_id, class_body],
        )
        root = self._create_mock_node("program", children=[class_node])
        tree = MagicMock()
        tree.root_node = root

        symbols, edges = _extract_symbols_and_edges(
            tree, source, Path("app.js"), "javascript", run
        )

        # Should have class + method
        assert len(symbols) == 2
        class_symbols = [s for s in symbols if s.kind == "class"]
        method_symbols = [s for s in symbols if s.kind == "method"]
        assert len(class_symbols) == 1
        assert len(method_symbols) == 1
        # Method name should include class prefix
        assert "Svc.save" in method_symbols[0].name

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
        """Tracks files that fail to read."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "good.js").write_text("function foo() {}")
        (tmp_path / "bad.js").write_text("function bar() {}")

        # Mock file read to fail for bad.js
        original_read_bytes = Path.read_bytes

        def mock_read_bytes(self: Path) -> bytes:
            if "bad" in self.name:
                raise IOError("Mock read error")
            return original_read_bytes(self)

        with patch.object(Path, "read_bytes", mock_read_bytes):
            result = analyze_javascript(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 1  # good.js
        assert result.run.files_skipped == 1  # bad.js


class TestSvelteFileDiscovery:
    """Tests for Svelte file discovery."""

    def test_finds_svelte_files(self, tmp_path: Path) -> None:
        """Finds .svelte files."""
        from hypergumbo.analyze.js_ts import find_svelte_files

        (tmp_path / "App.svelte").write_text("<script>const x = 1;</script>")
        (tmp_path / "other.txt").write_text("not svelte")

        files = list(find_svelte_files(tmp_path))

        assert len(files) == 1
        assert files[0].suffix == ".svelte"


class TestSvelteScriptExtraction:
    """Tests for extracting <script> blocks from Svelte files."""

    def test_extracts_typescript_script(self) -> None:
        """Extracts TypeScript script with lang='ts'."""
        from hypergumbo.analyze.js_ts import extract_svelte_scripts

        source = '''<script lang="ts">
const x: number = 1;
function foo() { return x; }
</script>

<div>Hello</div>'''

        blocks = extract_svelte_scripts(source)

        assert len(blocks) == 1
        assert blocks[0].is_typescript is True
        assert "const x: number" in blocks[0].content
        assert blocks[0].start_line == 1  # Content starts after <script> on line 1

    def test_extracts_javascript_script(self) -> None:
        """Extracts JavaScript script without lang attribute."""
        from hypergumbo.analyze.js_ts import extract_svelte_scripts

        source = '''<script>
const x = 1;
</script>'''

        blocks = extract_svelte_scripts(source)

        assert len(blocks) == 1
        assert blocks[0].is_typescript is False
        assert "const x = 1" in blocks[0].content

    def test_extracts_multiple_scripts(self) -> None:
        """Extracts multiple script blocks."""
        from hypergumbo.analyze.js_ts import extract_svelte_scripts

        source = '''<script lang="ts">
export let name: string;
</script>

<script context="module" lang="ts">
export const preload = () => {};
</script>

<div>{name}</div>'''

        blocks = extract_svelte_scripts(source)

        # Should find both script blocks (context="module" is also matched)
        assert len(blocks) >= 1  # At least the first one
        assert any(b.is_typescript for b in blocks)

    def test_handles_no_script(self) -> None:
        """Returns empty list when no script block."""
        from hypergumbo.analyze.js_ts import extract_svelte_scripts

        source = '''<div>Just HTML</div>
<style>
.foo { color: red; }
</style>'''

        blocks = extract_svelte_scripts(source)

        assert len(blocks) == 0

    def test_correct_line_offset(self) -> None:
        """Script content line offset is calculated correctly."""
        from hypergumbo.analyze.js_ts import extract_svelte_scripts

        source = '''<!-- Comment -->
<style>
.foo { color: red; }
</style>

<script lang="ts">
function test() {
    return 42;
}
</script>'''

        blocks = extract_svelte_scripts(source)

        assert len(blocks) == 1
        # Script tag is on line 6, content starts there
        assert blocks[0].start_line == 6


class TestSvelteAnalysis:
    """Tests for analyzing Svelte files."""

    def test_analyzes_svelte_functions(self, tmp_path: Path) -> None:
        """Analyzes functions in Svelte script block."""
        from hypergumbo.analyze.js_ts import _analyze_svelte_file
        from hypergumbo.ir import AnalysisRun

        svelte_file = tmp_path / "Component.svelte"
        svelte_file.write_text('''<script lang="ts">
function handleClick() {
    console.log("clicked");
}

const double = (x: number) => x * 2;
</script>

<button on:click={handleClick}>Click me</button>''')

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_svelte_file(svelte_file, run)

        assert success is True
        assert len(symbols) >= 1
        names = [s.name for s in symbols]
        assert "handleClick" in names

    def test_svelte_line_numbers_adjusted(self, tmp_path: Path) -> None:
        """Line numbers are adjusted for script block offset."""
        from hypergumbo.analyze.js_ts import _analyze_svelte_file
        from hypergumbo.ir import AnalysisRun

        svelte_file = tmp_path / "Component.svelte"
        svelte_file.write_text('''<!-- Header comment -->
<style>
.container { margin: 0; }
</style>

<script lang="ts">
function myFunc() {
    return 42;
}
</script>''')

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_svelte_file(svelte_file, run)

        assert success is True
        # Find myFunc symbol
        my_func = next((s for s in symbols if s.name == "myFunc"), None)
        assert my_func is not None
        # Function is defined on line 7-9 of the original file
        assert my_func.span.start_line >= 7

    def test_analyze_javascript_includes_svelte(self, tmp_path: Path) -> None:
        """analyze_javascript processes Svelte files too."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        # Create a JS file and a Svelte file
        (tmp_path / "app.js").write_text("function jsFunc() {}")
        (tmp_path / "Component.svelte").write_text('''<script lang="ts">
function svelteFunc() {}
</script>''')

        result = analyze_javascript(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 2  # Both files
        names = [s.name for s in result.symbols]
        assert "jsFunc" in names
        assert "svelteFunc" in names

    def test_svelte_no_script_blocks(self, tmp_path: Path) -> None:
        """Svelte file without script blocks returns empty symbols."""
        from hypergumbo.analyze.js_ts import _analyze_svelte_file
        from hypergumbo.ir import AnalysisRun

        svelte_file = tmp_path / "Static.svelte"
        svelte_file.write_text('''<style>
.container { margin: 0; }
</style>

<div class="container">
  <h1>Static content</h1>
</div>''')

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_svelte_file(svelte_file, run)

        assert success is True
        assert len(symbols) == 0
        assert len(edges) == 0


class TestSvelteEdgeCases:
    """Tests for Svelte edge cases and error handling."""

    def test_get_parser_for_lang_import_error(self) -> None:
        """Returns None when tree-sitter is not available."""
        from hypergumbo.analyze.js_ts import _get_parser_for_lang

        # Mark tree-sitter modules as unavailable in sys.modules
        with patch.dict(sys.modules, {
            "tree_sitter": None,
            "tree_sitter_javascript": None,
        }):
            result = _get_parser_for_lang(is_typescript=True)
            assert result is None

    def test_get_parser_for_lang_ts_fallback_to_js(self) -> None:
        """Falls back to JavaScript parser when TypeScript unavailable."""
        from hypergumbo.analyze.js_ts import _get_parser_for_lang

        mock_ts = MagicMock()
        mock_ts_js = MagicMock()
        mock_parser = MagicMock()
        mock_ts.Parser.return_value = mock_parser
        mock_lang = MagicMock()
        mock_ts_js.language.return_value = mock_lang

        # Test the fallback path by mocking tree_sitter_typescript to raise ImportError
        with patch.dict(sys.modules, {
            "tree_sitter": mock_ts,
            "tree_sitter_javascript": mock_ts_js,
            "tree_sitter_typescript": None,  # Mark as unavailable
        }):
            result = _get_parser_for_lang(is_typescript=True)
            # When TypeScript import fails, should fall back to JavaScript parser
            assert result is mock_parser
            mock_ts_js.language.assert_called()

    def test_get_parser_for_lang_javascript(self) -> None:
        """Gets JavaScript parser when is_typescript=False."""
        from hypergumbo.analyze.js_ts import _get_parser_for_lang

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
            result = _get_parser_for_lang(is_typescript=False)
            assert result is mock_parser
            mock_ts_js.language.assert_called()

    def test_svelte_file_read_error(self, tmp_path: Path) -> None:
        """Returns failure when Svelte file cannot be read."""
        from hypergumbo.analyze.js_ts import _analyze_svelte_file
        from hypergumbo.ir import AnalysisRun

        svelte_file = tmp_path / "Component.svelte"
        # Don't create the file - will cause read error

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_svelte_file(svelte_file, run)

        assert success is False
        assert len(symbols) == 0
        assert len(edges) == 0

    def test_svelte_parser_unavailable(self, tmp_path: Path) -> None:
        """Skips script block when parser is unavailable."""
        from hypergumbo.analyze.js_ts import _analyze_svelte_file
        from hypergumbo.ir import AnalysisRun

        svelte_file = tmp_path / "Component.svelte"
        svelte_file.write_text('''<script lang="ts">
function test() {}
</script>''')

        run = AnalysisRun.create(pass_id="test", version="test")

        with patch("hypergumbo.analyze.js_ts._get_parser_for_lang", return_value=None):
            symbols, edges, success = _analyze_svelte_file(svelte_file, run)

        # Still succeeds but with no symbols
        assert success is True
        assert len(symbols) == 0

    def test_svelte_file_skipped_increments_counter(self, tmp_path: Path) -> None:
        """Svelte files that fail to read increment skipped counter."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        svelte_file = tmp_path / "Component.svelte"
        svelte_file.write_text('''<script lang="ts">
function test() {}
</script>''')

        # Mock file read to fail for svelte files
        original_read_text = Path.read_text

        def mock_read_text(self: Path, *args, **kwargs) -> str:
            if self.suffix == ".svelte":
                raise IOError("Mock read error")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", mock_read_text):
            result = analyze_javascript(tmp_path)

        assert result.run is not None
        assert result.run.files_skipped == 1


class TestParserUnavailableEdgeCases:
    """Tests for edge cases when parser is unavailable."""

    def test_js_parser_unavailable_skips_files(self, tmp_path: Path) -> None:
        """JS files are skipped when parser is unavailable."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("function foo() {}")

        with patch("hypergumbo.analyze.js_ts.is_tree_sitter_available", return_value=True):
            with patch("hypergumbo.analyze.js_ts._get_parser_for_file", return_value=None):
                result = analyze_javascript(tmp_path)

        assert result.run is not None
        assert result.run.files_skipped == 1
        assert result.run.files_analyzed == 0

    def test_svelte_parser_unavailable_in_main_analysis(self, tmp_path: Path) -> None:
        """Svelte script blocks are skipped when parser unavailable."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "App.svelte").write_text('''<script lang="ts">
function test() {}
</script>''')

        with patch("hypergumbo.analyze.js_ts.is_tree_sitter_available", return_value=True):
            with patch("hypergumbo.analyze.js_ts._get_parser_for_lang", return_value=None):
                result = analyze_javascript(tmp_path)

        assert result.run is not None
        # File is analyzed but script blocks are skipped
        assert result.run.files_analyzed == 1
        # No symbols extracted since parser is None
        assert len(result.symbols) == 0


class TestSvelteMethodResolution:
    """Tests for method resolution in Svelte files."""

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        """Skip tests if tree-sitter not installed."""
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_javascript")

    def test_svelte_with_class_methods(self, tmp_path: Path) -> None:
        """Svelte files with class methods build proper method registry."""
        from hypergumbo.analyze.js_ts import _analyze_svelte_file
        from hypergumbo.ir import AnalysisRun

        svelte_file = tmp_path / "Component.svelte"
        svelte_file.write_text('''<script lang="ts">
class UserService {
    save() {
        return true;
    }

    create() {
        this.save();
        return {};
    }
}
</script>''')

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_svelte_file(svelte_file, run)

        assert success is True
        # Should have class and methods
        method_symbols = [s for s in symbols if s.kind == "method"]
        assert len(method_symbols) == 2

        # Should have this.method() call edge
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_analyze_svelte_file_no_scripts_in_main(self, tmp_path: Path) -> None:
        """Svelte files with no script blocks count as analyzed."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        # Create Svelte file with no script
        (tmp_path / "Static.svelte").write_text('''<style>
.foo { color: red; }
</style>
<div>Just HTML</div>''')

        result = analyze_javascript(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 1  # Still counts as analyzed


class TestCrossFileResolution:
    """Tests for cross-file call resolution."""

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        """Skip tests if tree-sitter not installed."""
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_javascript")

    def test_this_method_call(self, tmp_path: Path) -> None:
        """Detects this.method() calls within a class."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        code = """
class UserService {
    save() {
        return true;
    }

    create() {
        this.save();
        return {};
    }
}
"""
        (tmp_path / "service.js").write_text(code)

        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        this_calls = [e for e in call_edges if e.evidence_type == "ast_method_this"]
        assert len(this_calls) == 1
        assert "save" in this_calls[0].dst
        assert this_calls[0].confidence == 0.95

    def test_inferred_method_call(self, tmp_path: Path) -> None:
        """Detects obj.method() calls with inferred type."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        code = """
class Logger {
    writeMessage(msg) {
        return msg;
    }
}

function main(logger) {
    logger.writeMessage("hello");
}
"""
        (tmp_path / "app.js").write_text(code)

        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        inferred_calls = [e for e in call_edges if e.evidence_type == "ast_method_inferred"]
        assert len(inferred_calls) == 1
        assert "writeMessage" in inferred_calls[0].dst
        assert inferred_calls[0].confidence == 0.60

    def test_new_class_instantiation(self, tmp_path: Path) -> None:
        """Detects new ClassName() instantiation."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        code = """
class User {
    constructor(name) {
        this.name = name;
    }
}

function createUser() {
    return new User("test");
}
"""
        (tmp_path / "app.js").write_text(code)

        result = analyze_javascript(tmp_path)

        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        assert len(inst_edges) == 1
        assert "User" in inst_edges[0].dst
        assert inst_edges[0].evidence_type == "ast_new"
        assert inst_edges[0].confidence == 0.95

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Resolves function calls across files."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "utils.js").write_text("function helper() { return 42; }")
        (tmp_path / "main.js").write_text("function main() { helper(); }")

        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1
        # main calls helper (cross-file)
        assert any("helper" in e.dst for e in call_edges)

    def test_cross_file_class_instantiation(self, tmp_path: Path) -> None:
        """Resolves class instantiation across files."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        (tmp_path / "models.js").write_text("class User { constructor() {} }")
        (tmp_path / "main.js").write_text("function createUser() { return new User(); }")

        result = analyze_javascript(tmp_path)

        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        assert len(inst_edges) == 1
        assert "User" in inst_edges[0].dst


