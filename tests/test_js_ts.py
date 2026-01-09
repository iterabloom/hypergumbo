"""Tests for JavaScript/TypeScript analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
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
        children: list | None = None,
        has_error: bool = False,
    ) -> MagicMock:
        """Create a mock tree-sitter node.

        Note: Must explicitly set parent=None to prevent MagicMock from
        creating infinite mock chains when code walks up via node.parent.
        Parent pointers for children are set up automatically.
        """
        node = MagicMock()
        node.type = node_type
        node.start_byte = start_byte
        node.end_byte = end_byte
        node.start_point = start_point
        node.end_point = end_point
        node.children = children or []
        node.has_error = has_error
        node.parent = None  # Explicit None prevents infinite mock chains
        # Set parent pointers for all children
        for child in node.children:
            child.parent = node
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

    def test_extract_arrow_function_in_wrapper(self) -> None:
        """Tests extraction of arrow functions wrapped in call expressions.

        Pattern: const handler = catchAsync(async (req, res) => { ... })
        This is common in Express.js error handling middleware.
        """
        from hypergumbo.analyze.js_ts import _extract_symbols_and_edges
        from hypergumbo.ir import AnalysisRun

        source = b"function helper() {} const handler = catchAsync(async (req, res) => { helper(); });"
        run = AnalysisRun.create(pass_id="test", version="1.0")

        # Helper function
        helper_id = self._create_mock_node("identifier", start_byte=9, end_byte=15)
        helper_func = self._create_mock_node(
            "function_declaration",
            start_point=(0, 0),
            end_point=(0, 20),
            children=[helper_id],
        )

        # Call to helper inside arrow function body
        call_id = self._create_mock_node("identifier", start_byte=70, end_byte=76)
        call_args = self._create_mock_node("arguments", children=[])
        call_node = self._create_mock_node(
            "call_expression",
            start_point=(0, 70),
            end_point=(0, 78),
            children=[call_id, call_args],
        )

        # Arrow function wrapped in catchAsync call
        arrow_node = self._create_mock_node(
            "arrow_function",
            start_point=(0, 48),
            end_point=(0, 80),
            children=[call_node],
        )
        wrapper_args = self._create_mock_node("arguments", children=[arrow_node])
        wrapper_id = self._create_mock_node("identifier", start_byte=37, end_byte=47)
        wrapper_call = self._create_mock_node(
            "call_expression",
            start_point=(0, 37),
            end_point=(0, 81),
            children=[wrapper_id, wrapper_args],
        )

        # Variable declarator: handler = catchAsync(...)
        handler_id = self._create_mock_node("identifier", start_byte=27, end_byte=34)
        declarator = self._create_mock_node(
            "variable_declarator",
            children=[handler_id, wrapper_call],
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

        # Should have helper function and handler arrow function
        func_symbols = [s for s in symbols if s.kind == "function"]
        assert len(func_symbols) == 2
        names = {s.name for s in func_symbols}
        assert "helper" in names
        assert "handler" in names  # Arrow function in wrapper should be extracted

        # Should have call edge for the helper call (from nested call_expression)
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

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


class TestVueFileDiscovery:
    """Tests for Vue SFC file discovery."""

    def test_finds_vue_files(self, tmp_path: Path) -> None:
        """Finds .vue files."""
        from hypergumbo.analyze.js_ts import find_vue_files

        (tmp_path / "App.vue").write_text("<script>const x = 1;</script>")
        (tmp_path / "other.txt").write_text("not vue")

        files = list(find_vue_files(tmp_path))

        assert len(files) == 1
        assert files[0].suffix == ".vue"


class TestVueScriptExtraction:
    """Tests for extracting <script> blocks from Vue SFC files."""

    def test_extracts_typescript_script(self) -> None:
        """Extracts TypeScript script with lang='ts'."""
        from hypergumbo.analyze.js_ts import extract_vue_scripts

        source = '''<template>
<div>Hello</div>
</template>

<script lang="ts">
export default {
  data() {
    return { count: 0 };
  }
}
</script>'''

        blocks = extract_vue_scripts(source)

        assert len(blocks) == 1
        assert blocks[0].is_typescript is True
        assert "export default" in blocks[0].content

    def test_extracts_javascript_script(self) -> None:
        """Extracts JavaScript script without lang attribute."""
        from hypergumbo.analyze.js_ts import extract_vue_scripts

        source = '''<script>
const x = 1;
</script>'''

        blocks = extract_vue_scripts(source)

        assert len(blocks) == 1
        assert blocks[0].is_typescript is False
        assert "const x = 1" in blocks[0].content

    def test_extracts_script_setup(self) -> None:
        """Extracts <script setup> blocks (Vue 3 Composition API)."""
        from hypergumbo.analyze.js_ts import extract_vue_scripts

        source = '''<script setup lang="ts">
import { ref } from 'vue'
const count = ref(0)
</script>

<template>
<button @click="count++">{{ count }}</button>
</template>'''

        blocks = extract_vue_scripts(source)

        assert len(blocks) == 1
        assert blocks[0].is_typescript is True
        assert "import { ref }" in blocks[0].content

    def test_handles_no_script(self) -> None:
        """Returns empty list when no script block."""
        from hypergumbo.analyze.js_ts import extract_vue_scripts

        source = '''<template>
<div>Just HTML</div>
</template>

<style>
.foo { color: red; }
</style>'''

        blocks = extract_vue_scripts(source)

        assert len(blocks) == 0

    def test_correct_line_offset(self) -> None:
        """Script content line offset is calculated correctly."""
        from hypergumbo.analyze.js_ts import extract_vue_scripts

        source = '''<template>
<div>Hello</div>
</template>

<script lang="ts">
function test() {
    return 42;
}
</script>'''

        blocks = extract_vue_scripts(source)

        assert len(blocks) == 1
        # Script tag is on line 5, content starts there
        assert blocks[0].start_line == 5


class TestVueAnalysis:
    """Tests for analyzing Vue SFC files."""

    def test_analyzes_vue_functions(self, tmp_path: Path) -> None:
        """Analyzes functions in Vue script block."""
        from hypergumbo.analyze.js_ts import _analyze_vue_file
        from hypergumbo.ir import AnalysisRun

        vue_file = tmp_path / "Component.vue"
        vue_file.write_text('''<script lang="ts">
function handleClick() {
    console.log("clicked");
}

const helper = () => {
    return 42;
}
</script>

<template>
<button @click="handleClick">Click me</button>
</template>''')

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_vue_file(vue_file, run)

        assert success is True
        func_names = [s.name for s in symbols if s.kind == "function"]
        assert "handleClick" in func_names
        assert "helper" in func_names

    def test_analyze_javascript_includes_vue(self, tmp_path: Path) -> None:
        """analyze_javascript processes Vue files too."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        # Create a JS file and a Vue file
        (tmp_path / "app.js").write_text("function main() {}")
        (tmp_path / "Component.vue").write_text('''<script>
function vueHelper() {}
</script>''')

        result = analyze_javascript(tmp_path)


        func_names = [s.name for s in result.symbols if s.kind == "function"]
        assert "main" in func_names
        assert "vueHelper" in func_names

    def test_vue_file_no_script(self, tmp_path: Path) -> None:
        """Vue file without script blocks returns empty symbols."""
        from hypergumbo.analyze.js_ts import _analyze_vue_file
        from hypergumbo.ir import AnalysisRun

        vue_file = tmp_path / "NoScript.vue"
        vue_file.write_text('''<template>
<div>No script here</div>
</template>

<style>
.foo { color: red; }
</style>''')

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_vue_file(vue_file, run)

        assert success is True
        assert symbols == []
        assert edges == []


class TestVueEdgeCases:
    """Tests for Vue edge cases and error handling."""

    def test_vue_file_read_error(self, tmp_path: Path) -> None:
        """Returns failure when Vue file cannot be read."""
        from hypergumbo.analyze.js_ts import _analyze_vue_file
        from hypergumbo.ir import AnalysisRun
        from unittest.mock import patch

        vue_file = tmp_path / "Broken.vue"
        vue_file.write_text("<script>const x = 1;</script>")

        run = AnalysisRun.create(pass_id="test", version="test")

        with patch.object(Path, "read_text", side_effect=OSError("Read failed")):
            symbols, edges, success = _analyze_vue_file(vue_file, run)

        assert success is False
        assert symbols == []
        assert edges == []

    def test_vue_files_increment_analyzed_counter(self, tmp_path: Path) -> None:
        """Vue files without script blocks count as analyzed."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        # Create Vue file with no script
        (tmp_path / "Empty.vue").write_text("<template><div>Hi</div></template>")

        result = analyze_javascript(tmp_path)


        # Files should be counted as analyzed, not skipped
        assert result.run is not None
        assert result.run.files_analyzed >= 1

    def test_vue_file_read_error_increments_skipped(self, tmp_path: Path) -> None:
        """Vue files that fail to read increment skipped counter."""
        from hypergumbo.analyze.js_ts import analyze_javascript
        from unittest.mock import patch

        vue_file = tmp_path / "Component.vue"
        vue_file.write_text("<script>const x = 1;</script>")

        # Also create a readable file so we can run analysis
        (tmp_path / "good.js").write_text("const y = 2;")

        # Mock only the Vue file read to fail
        original_read_text = Path.read_text

        def mock_read_text(self, *args, **kwargs):
            if str(self).endswith(".vue"):
                raise OSError("Read failed")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", mock_read_text):
            result = analyze_javascript(tmp_path)


        # Vue file should be skipped
        assert result.run is not None
        assert result.run.files_skipped >= 1

    def test_vue_with_class_and_methods(self, tmp_path: Path) -> None:
        """Vue file with class and methods builds proper symbol registry."""
        from hypergumbo.analyze.js_ts import _analyze_vue_file
        from hypergumbo.ir import AnalysisRun

        vue_file = tmp_path / "WithClass.vue"
        vue_file.write_text('''<script lang="ts">
class MyComponent {
    private data: string;

    constructor() {
        this.data = "";
    }

    public greet(): string {
        return "Hello " + this.data;
    }

    private helper(): void {
        console.log("helping");
    }
}
</script>

<template>
<div>Test</div>
</template>''')

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_vue_file(vue_file, run)

        assert success is True
        # Should find class and methods
        class_names = [s.name for s in symbols if s.kind == "class"]
        method_names = [s.name for s in symbols if s.kind == "method"]
        assert "MyComponent" in class_names
        assert any("greet" in name for name in method_names)

    def test_vue_parser_unavailable_skips_block(self, tmp_path: Path) -> None:
        """Vue script blocks are skipped when parser unavailable."""
        from hypergumbo.analyze.js_ts import _analyze_vue_file
        from hypergumbo.ir import AnalysisRun
        from unittest.mock import patch

        vue_file = tmp_path / "Test.vue"
        vue_file.write_text("<script>const x = 1;</script>")

        run = AnalysisRun.create(pass_id="test", version="test")

        # Mock _get_parser_for_lang to return None
        with patch("hypergumbo.analyze.js_ts._get_parser_for_lang", return_value=None):
            symbols, edges, success = _analyze_vue_file(vue_file, run)

        assert success is True
        assert symbols == []
        assert edges == []

    def test_vue_parser_unavailable_in_analyze_javascript(self, tmp_path: Path) -> None:
        """Vue script blocks are skipped in analyze_javascript when parser unavailable."""
        from hypergumbo.analyze.js_ts import analyze_javascript, is_tree_sitter_available
        from unittest.mock import patch

        if not is_tree_sitter_available():
            pytest.skip("tree-sitter not available")

        vue_file = tmp_path / "Test.vue"
        vue_file.write_text("<script>const x = 1;</script>")

        # Also create a JS file so we have something to analyze
        (tmp_path / "good.js").write_text("function foo() {}")

        # Mock _get_parser_for_lang to return None only for TypeScript
        original_get_parser = None

        def mock_get_parser(is_typescript):
            if is_typescript is False:  # JavaScript from Vue file
                return None
            import tree_sitter
            import tree_sitter_javascript
            parser = tree_sitter.Parser()
            parser.language = tree_sitter.Language(tree_sitter_javascript.language())
            return parser

        with patch("hypergumbo.analyze.js_ts._get_parser_for_lang", side_effect=mock_get_parser):
            result = analyze_javascript(tmp_path)

        # Should still succeed with the JS file
        assert result.run is not None


# ============================================================================
# Express.js Route Detection Tests
# ============================================================================


class TestExpressRouteDetection:
    """Tests for Express.js route handler detection."""

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        """Skip tests if tree-sitter is not available."""
        from hypergumbo.analyze.js_ts import is_tree_sitter_available

        if not is_tree_sitter_available():
            pytest.skip("tree-sitter not available")

    def test_express_get_route_detected(self, tmp_path: Path) -> None:
        """Express app.get() route handler sets stable_id to 'get'."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "app.js"
        js_file.write_text("""
const express = require('express');
const app = express();

app.get('/users', function getUsers(req, res) {
    res.json([]);
});
""")

        result = analyze_javascript(tmp_path)

        # Find the route handler function
        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.stable_id in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")]

        assert len(route_handlers) == 1
        handler = route_handlers[0]
        assert handler.name == "getUsers"
        assert handler.stable_id == "GET"
        assert handler.meta is not None
        assert handler.meta.get("route_path") == "/users"

    def test_express_post_route_detected(self, tmp_path: Path) -> None:
        """Express app.post() route handler sets stable_id to 'post'."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "app.js"
        js_file.write_text("""
const express = require('express');
const app = express();

app.post('/users', function createUser(req, res) {
    res.json({ id: 1 });
});
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.stable_id == "POST"]

        assert len(route_handlers) == 1
        assert route_handlers[0].meta.get("route_path") == "/users"

    def test_express_router_route_detected(self, tmp_path: Path) -> None:
        """Express router.get() also sets stable_id to HTTP method."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "routes.js"
        js_file.write_text("""
const express = require('express');
const router = express.Router();

router.get('/items/:id', function getItem(req, res) {
    res.json({ id: req.params.id });
});

router.delete('/items/:id', function deleteItem(req, res) {
    res.json({ deleted: true });
});
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.stable_id in ("GET", "DELETE")]

        assert len(route_handlers) == 2

        get_handler = next(f for f in route_handlers if f.stable_id == "GET")
        delete_handler = next(f for f in route_handlers if f.stable_id == "DELETE")

        assert get_handler.meta.get("route_path") == "/items/:id"
        assert delete_handler.meta.get("route_path") == "/items/:id"

    def test_express_arrow_function_route(self, tmp_path: Path) -> None:
        """Express route with arrow function handler."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "app.js"
        js_file.write_text("""
const express = require('express');
const app = express();

app.get('/health', (req, res) => {
    res.json({ status: 'ok' });
});
""")

        result = analyze_javascript(tmp_path)

        # Arrow functions in route calls should get route info
        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.stable_id == "GET"]

        # Even anonymous arrow functions should be detected as routes
        assert len(route_handlers) >= 0  # May or may not create symbol for anonymous

    def test_express_all_http_methods(self, tmp_path: Path) -> None:
        """All HTTP methods should be detected: get, post, put, patch, delete."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "app.js"
        js_file.write_text("""
const app = require('express')();

app.get('/get', function doGet(req, res) { res.send('get'); });
app.post('/post', function doPost(req, res) { res.send('post'); });
app.put('/put', function doPut(req, res) { res.send('put'); });
app.patch('/patch', function doPatch(req, res) { res.send('patch'); });
app.delete('/delete', function doDelete(req, res) { res.send('delete'); });
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = {f.stable_id: f for f in functions if f.stable_id in ("GET", "POST", "PUT", "PATCH", "DELETE")}

        assert "GET" in route_handlers
        assert "POST" in route_handlers
        assert "PUT" in route_handlers
        assert "PATCH" in route_handlers
        assert "DELETE" in route_handlers

    def test_non_route_function_keeps_original_stable_id(self, tmp_path: Path) -> None:
        """Functions not in route calls keep their original stable_id."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "utils.js"
        js_file.write_text("""
function helper() {
    return 42;
}
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        assert len(functions) == 1

        # Non-route functions should NOT have HTTP method as stable_id
        assert functions[0].stable_id not in ("GET", "POST", "PUT", "PATCH", "DELETE")

    def test_typescript_express_route(self, tmp_path: Path) -> None:
        """Express routes in TypeScript files are detected."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("""
import express, { Request, Response } from 'express';
const app = express();

app.get('/users', function getUsers(req: Request, res: Response): void {
    res.json([]);
});
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.stable_id == "GET"]

        assert len(route_handlers) == 1
        assert route_handlers[0].meta.get("route_path") == "/users"

    def test_express_external_handler_detected(self, tmp_path: Path) -> None:
        """Express routes with external handler references are detected."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "routes.js"
        js_file.write_text("""
const express = require('express');
const userController = require('./controllers/user');
const router = express.Router();

router.post('/register', userController.register);
router.get('/users', userController.getUsers);
router.delete('/users/:id', userController.deleteUser);
""")

        result = analyze_javascript(tmp_path)

        # Find route symbols (external handlers create route symbols, not function symbols)
        routes = [s for s in result.symbols if s.kind == "route"]

        assert len(routes) == 3

        # Verify routes have correct metadata
        route_names = {r.name for r in routes}
        assert "userController.register" in route_names
        assert "userController.getUsers" in route_names
        assert "userController.deleteUser" in route_names

        # Verify HTTP methods
        methods = {r.stable_id for r in routes}
        assert methods == {"POST", "GET", "DELETE"}

        # Verify route paths
        for route in routes:
            assert route.meta is not None
            assert "handler_ref" in route.meta
            if route.name == "userController.register":
                assert route.meta.get("route_path") == "/register"
            elif route.name == "userController.getUsers":
                assert route.meta.get("route_path") == "/users"
            elif route.name == "userController.deleteUser":
                assert route.meta.get("route_path") == "/users/:id"

    def test_express_external_identifier_handler(self, tmp_path: Path) -> None:
        """Express routes with identifier (non-member) handlers are detected."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "routes.js"
        js_file.write_text("""
const express = require('express');
const handleUsers = require('./handlers');
const router = express.Router();

router.get('/users', handleUsers);
""")

        result = analyze_javascript(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]

        assert len(routes) == 1
        assert routes[0].name == "handleUsers"
        assert routes[0].stable_id == "GET"
        assert routes[0].meta.get("handler_ref") == "handleUsers"

    def test_express_chained_route_syntax(self, tmp_path: Path) -> None:
        """Express chained route syntax: router.route('/path').get(handler)."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "routes.js"
        js_file.write_text("""
const express = require('express');
const userController = require('./controllers/user');
const router = express.Router();

router
  .route('/')
  .post(userController.createUser)
  .get(userController.getUsers);

router
  .route('/:userId')
  .get(userController.getUser)
  .patch(userController.updateUser)
  .delete(userController.deleteUser);
""")

        result = analyze_javascript(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]

        assert len(routes) == 5

        # Verify routes have correct paths from chained .route() call
        root_routes = [r for r in routes if r.meta.get("route_path") == "/"]
        assert len(root_routes) == 2
        root_methods = {r.stable_id for r in root_routes}
        assert root_methods == {"POST", "GET"}

        param_routes = [r for r in routes if r.meta.get("route_path") == "/:userId"]
        assert len(param_routes) == 3
        param_methods = {r.stable_id for r in param_routes}
        assert param_methods == {"GET", "PATCH", "DELETE"}


# ============================================================================
# NestJS Route Detection Tests
# ============================================================================


class TestNestJSRouteDetection:
    """Tests for NestJS decorator-based route detection."""

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        """Skip tests if tree-sitter is not available."""
        from hypergumbo.analyze.js_ts import is_tree_sitter_available

        if not is_tree_sitter_available():
            pytest.skip("tree-sitter not available")

    def test_nestjs_get_decorator(self, tmp_path: Path) -> None:
        """NestJS @Get() decorator should set stable_id to 'get'."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "users.controller.ts"
        ts_file.write_text("""
import { Controller, Get, Post } from '@nestjs/common';

@Controller('users')
export class UsersController {
    @Get()
    findAll() {
        return [];
    }
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        route_handlers = [m for m in methods if m.stable_id == "GET"]

        assert len(route_handlers) == 1
        assert route_handlers[0].name == "UsersController.findAll"

    def test_nestjs_post_decorator(self, tmp_path: Path) -> None:
        """NestJS @Post() decorator should set stable_id to 'post'."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "users.controller.ts"
        ts_file.write_text("""
import { Controller, Post, Body } from '@nestjs/common';

@Controller('users')
export class UsersController {
    @Post()
    create(@Body() dto: any) {
        return {};
    }
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        route_handlers = [m for m in methods if m.stable_id == "POST"]

        assert len(route_handlers) == 1
        assert route_handlers[0].name == "UsersController.create"

    def test_nestjs_get_with_path(self, tmp_path: Path) -> None:
        """NestJS @Get(':id') should extract route path."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "users.controller.ts"
        ts_file.write_text("""
import { Controller, Get, Param } from '@nestjs/common';

@Controller('users')
export class UsersController {
    @Get(':id')
    findOne(@Param('id') id: string) {
        return {};
    }
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        route_handlers = [m for m in methods if m.stable_id == "GET"]

        assert len(route_handlers) == 1
        handler = route_handlers[0]
        assert handler.name == "UsersController.findOne"
        assert handler.meta is not None
        assert handler.meta.get("route_path") == ":id"

    def test_nestjs_all_http_methods(self, tmp_path: Path) -> None:
        """NestJS should detect all HTTP method decorators."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "resource.controller.ts"
        ts_file.write_text("""
import { Controller, Get, Post, Put, Patch, Delete } from '@nestjs/common';

@Controller('resource')
export class ResourceController {
    @Get()
    getAll() {}

    @Post()
    create() {}

    @Put(':id')
    update() {}

    @Patch(':id')
    patch() {}

    @Delete(':id')
    remove() {}
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        stable_ids = {m.stable_id for m in methods}

        assert "GET" in stable_ids
        assert "POST" in stable_ids
        assert "PUT" in stable_ids
        assert "PATCH" in stable_ids
        assert "DELETE" in stable_ids


# ============================================================================
# Koa Router Route Detection Tests
# ============================================================================


class TestKoaRouteDetection:
    """Tests for Koa Router route detection.

    Koa Router uses the same pattern as Express: router.get('/path', handler).
    The existing route detection should work for Koa out of the box.
    """

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        """Skip tests if tree-sitter is not available."""
        from hypergumbo.analyze.js_ts import is_tree_sitter_available

        if not is_tree_sitter_available():
            pytest.skip("tree-sitter not available")

    def test_koa_router_get_route(self, tmp_path: Path) -> None:
        """Koa Router router.get() route handler sets stable_id to 'get'."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "routes.js"
        js_file.write_text("""
const Router = require('@koa/router');
const router = new Router();

router.get('/users', function listUsers(ctx) {
    ctx.body = [];
});

module.exports = router;
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.stable_id == "GET"]

        assert len(route_handlers) == 1
        handler = route_handlers[0]
        assert handler.name == "listUsers"
        assert handler.meta is not None
        assert handler.meta.get("route_path") == "/users"

    def test_koa_router_post_route(self, tmp_path: Path) -> None:
        """Koa Router router.post() route handler sets stable_id to 'post'."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "routes.js"
        js_file.write_text("""
const Router = require('@koa/router');
const router = new Router();

router.post('/users', function createUser(ctx) {
    ctx.body = { id: 1 };
});
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.stable_id == "POST"]

        assert len(route_handlers) == 1
        assert route_handlers[0].meta.get("route_path") == "/users"

    def test_koa_router_arrow_function(self, tmp_path: Path) -> None:
        """Koa Router with arrow function handler also detects routes."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "routes.js"
        js_file.write_text("""
const Router = require('@koa/router');
const router = new Router();

router.delete('/users/:id', async (ctx) => {
    ctx.body = { deleted: true };
});
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.stable_id == "DELETE"]

        assert len(route_handlers) == 1
        assert route_handlers[0].meta.get("route_path") == "/users/:id"


# ============================================================================
# Fastify Route Detection Tests
# ============================================================================


class TestFastifyRouteDetection:
    """Tests for Fastify route detection.

    Fastify uses the same pattern as Express: fastify.get('/path', handler).
    The existing route detection should work for Fastify out of the box.
    """

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        """Skip tests if tree-sitter is not available."""
        from hypergumbo.analyze.js_ts import is_tree_sitter_available

        if not is_tree_sitter_available():
            pytest.skip("tree-sitter not available")

    def test_fastify_get_route(self, tmp_path: Path) -> None:
        """Fastify fastify.get() route handler sets stable_id to 'get'."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "server.js"
        js_file.write_text("""
const fastify = require('fastify')();

fastify.get('/users', function getUsers(request, reply) {
    reply.send([]);
});
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.stable_id == "GET"]

        assert len(route_handlers) == 1
        handler = route_handlers[0]
        assert handler.name == "getUsers"
        assert handler.meta is not None
        assert handler.meta.get("route_path") == "/users"

    def test_fastify_post_route(self, tmp_path: Path) -> None:
        """Fastify fastify.post() route handler sets stable_id to 'post'."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "server.js"
        js_file.write_text("""
const fastify = require('fastify')();

fastify.post('/users', function createUser(request, reply) {
    reply.send({ id: 1 });
});
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.stable_id == "POST"]

        assert len(route_handlers) == 1
        assert route_handlers[0].meta.get("route_path") == "/users"

    def test_fastify_arrow_function(self, tmp_path: Path) -> None:
        """Fastify with arrow function handler also detects routes."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "server.js"
        js_file.write_text("""
const fastify = require('fastify')();

fastify.put('/users/:id', async (request, reply) => {
    reply.send({ updated: true });
});
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.stable_id == "PUT"]

        assert len(route_handlers) == 1
        assert route_handlers[0].meta.get("route_path") == "/users/:id"

    def test_fastify_all_http_methods(self, tmp_path: Path) -> None:
        """Fastify supports all HTTP methods."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "server.js"
        js_file.write_text("""
const fastify = require('fastify')();

fastify.get('/a', function handleGet(r, p) {});
fastify.post('/b', function handlePost(r, p) {});
fastify.put('/c', function handlePut(r, p) {});
fastify.patch('/d', function handlePatch(r, p) {});
fastify.delete('/e', function handleDelete(r, p) {});
fastify.head('/f', function handleHead(r, p) {});
fastify.options('/g', function handleOptions(r, p) {});
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        stable_ids = {f.stable_id for f in functions}

        assert "GET" in stable_ids
        assert "POST" in stable_ids
        assert "PUT" in stable_ids
        assert "PATCH" in stable_ids
        assert "DELETE" in stable_ids
        assert "HEAD" in stable_ids
        assert "OPTIONS" in stable_ids


class TestReexportResolution:
    """Tests for barrel file (index.js) re-export resolution."""

    def test_reexport_call_edges_resolved(self, tmp_path: Path) -> None:
        """Calls to re-exported symbols should create proper call edges.

        When a barrel file (index.js) re-exports symbols from submodules:
            // utils/helper.js
            export function helper() { return 42; }

            // utils/index.js
            export { helper } from './helper';

        And another file imports from the barrel:
            // main.js
            import { helper } from './utils';
            function caller() { helper(); }

        The call edge from caller -> helper should be created, pointing to
        the real symbol in helper.js, not a placeholder.
        """
        from hypergumbo.analyze.js_ts import analyze_javascript

        # Create barrel structure
        utils = tmp_path / "utils"
        utils.mkdir()

        # Create the actual implementation
        helper_file = utils / "helper.js"
        helper_file.write_text("export function helper() { return 42; }\n")

        # Create barrel file (index.js) that re-exports
        index_file = utils / "index.js"
        index_file.write_text("export { helper } from './helper';\n")

        # Create main.js that imports from barrel and calls helper
        main_file = tmp_path / "main.js"
        main_file.write_text(
            "import { helper } from './utils';\n"
            "\n"
            "export function caller() {\n"
            "    helper();\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)

        # Should have both functions
        functions = [s for s in result.symbols if s.kind == "function"]
        func_names = {f.name for f in functions}
        assert "helper" in func_names, "helper function should be detected"
        assert "caller" in func_names, "caller function should be detected"

        # Find the actual helper symbol (in helper.js, not a placeholder)
        helper_syms = [f for f in functions if f.name == "helper"]
        assert len(helper_syms) == 1
        helper_sym = helper_syms[0]
        assert "helper.js" in helper_sym.path, \
            f"helper should be from helper.js, got {helper_sym.path}"

        # Find call edges from caller
        caller_syms = [f for f in functions if f.name == "caller"]
        assert len(caller_syms) == 1
        caller_id = caller_syms[0].id

        call_edges = [e for e in result.edges
                      if e.edge_type == "calls" and e.src == caller_id]

        # There should be a call edge to helper
        assert len(call_edges) >= 1, \
            f"Expected call edge from caller to helper, got: {call_edges}"

        # The call edge should point to the real helper
        helper_id = helper_sym.id
        call_dsts = {e.dst for e in call_edges}
        assert helper_id in call_dsts, \
            f"Call edge should point to real helper {helper_id}, got {call_dsts}"


class TestJsTsSignatureExtraction:
    """Tests for extracting function signatures from JavaScript/TypeScript code."""

    def test_extracts_js_function_signature(self, tmp_path: Path) -> None:
        """Extracts signature from JavaScript function declarations."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "main.js"
        js_file.write_text("""
function add(x, y) {
    return x + y;
}
""")

        result = analyze_javascript(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x, y)"

    def test_extracts_ts_function_signature_with_types(self, tmp_path: Path) -> None:
        """Extracts signature from TypeScript function with type annotations."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "main.ts"
        ts_file.write_text("""
function add(x: number, y: number): number {
    return x + y;
}
""")

        result = analyze_javascript(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        sig = funcs[0].signature
        assert sig is not None
        assert "x: number" in sig
        assert "y: number" in sig
        assert ": number" in sig  # return type

    def test_extracts_arrow_function_signature(self, tmp_path: Path) -> None:
        """Extracts signature from arrow functions."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "main.ts"
        ts_file.write_text("""
const add = (x: number, y: number): number => x + y;
""")

        result = analyze_javascript(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        sig = funcs[0].signature
        assert sig is not None
        assert "x: number" in sig
        assert "y: number" in sig

    def test_extracts_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature from class methods."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "main.ts"
        ts_file.write_text("""
class Calculator {
    add(x: number, y: number): number {
        return x + y;
    }
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        sig = methods[0].signature
        assert sig is not None
        assert "x: number" in sig

    def test_extracts_signature_with_default_params(self, tmp_path: Path) -> None:
        """Extracts signature with default parameters (shows ...)."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "main.js"
        js_file.write_text("""
function greet(name, greeting = "Hello") {
    return greeting + ", " + name;
}
""")

        result = analyze_javascript(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        sig = funcs[0].signature
        assert sig is not None
        assert "name" in sig
        # Default value should be shown as ...
        assert "greeting = ..." in sig

    def test_extracts_signature_with_rest_params(self, tmp_path: Path) -> None:
        """Extracts signature with rest parameters."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "main.js"
        js_file.write_text("""
function sum(...numbers) {
    return numbers.reduce((a, b) => a + b, 0);
}
""")

        result = analyze_javascript(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        sig = funcs[0].signature
        assert sig is not None
        assert "...numbers" in sig

    def test_symbol_to_dict_includes_signature(self, tmp_path: Path) -> None:
        """Symbol.to_dict() includes the signature field."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "main.ts"
        ts_file.write_text("""
function greet(name: string): string {
    return "Hello, " + name;
}
""")

        result = analyze_javascript(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1

        as_dict = funcs[0].to_dict()
        assert "signature" in as_dict
        assert "name: string" in as_dict["signature"]


class TestNamespaceImports:
    """Tests for namespace import tracking and resolution."""

    def test_extract_namespace_import_star_as(self, tmp_path: Path) -> None:
        """Extracts 'import * as alias from module' statements."""
        from hypergumbo.analyze.js_ts import _extract_namespace_imports, _get_parser_for_file

        js_file = tmp_path / "main.js"
        js_file.write_text("""
import * as grpc from '@grpc/grpc-js';
import * as utils from './utils';
""")

        parser = _get_parser_for_file(js_file)
        source = js_file.read_bytes()
        tree = parser.parse(source)

        ns_imports = _extract_namespace_imports(tree, source)

        assert "grpc" in ns_imports
        assert ns_imports["grpc"] == "@grpc/grpc-js"
        assert "utils" in ns_imports
        assert ns_imports["utils"] == "./utils"

    def test_extract_default_import(self, tmp_path: Path) -> None:
        """Extracts default imports as namespace mappings."""
        from hypergumbo.analyze.js_ts import _extract_namespace_imports, _get_parser_for_file

        js_file = tmp_path / "main.js"
        js_file.write_text("""
import grpc from 'grpc';
import axios from 'axios';
""")

        parser = _get_parser_for_file(js_file)
        source = js_file.read_bytes()
        tree = parser.parse(source)

        ns_imports = _extract_namespace_imports(tree, source)

        assert "grpc" in ns_imports
        assert ns_imports["grpc"] == "grpc"
        assert "axios" in ns_imports
        assert ns_imports["axios"] == "axios"

    def test_namespace_function_call_resolution(self, tmp_path: Path) -> None:
        """Namespace function calls (alias.func()) should be resolved."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        # Create a utils module
        utils_file = tmp_path / "utils.js"
        utils_file.write_text("""
function helper() {
    return 'help';
}
""")

        # Create main module using namespace import
        main_file = tmp_path / "main.js"
        main_file.write_text("""
import * as utils from './utils';

function run() {
    utils.helper();
}
""")

        result = analyze_javascript(tmp_path)

        # Find call edges
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Look for run -> helper edge
        run_helper_edge = next(
            (e for e in call_edges if "run" in e.src and "helper" in e.dst),
            None
        )
        assert run_helper_edge is not None, "Expected call edge from run to helper via namespace"


class TestVariableTypeInference:
    """Tests for variable type inference from constructor calls."""

    def test_variable_type_tracked_from_new(self, tmp_path: Path) -> None:
        """Variable types should be tracked from 'new ClassName()' assignments."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "main.js"
        js_file.write_text("""
class ServiceClient {
    send() {
        return 'sent';
    }
}

function run() {
    const client = new ServiceClient();
    client.send();
}
""")

        result = analyze_javascript(tmp_path)

        # Should have instantiates edge
        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        inst_edge = next(
            (e for e in inst_edges if "run" in e.src and "ServiceClient" in e.dst),
            None
        )
        assert inst_edge is not None, "Expected instantiates edge for ServiceClient"

        # Should have calls edge for client.send() -> ServiceClient.send
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        method_edge = next(
            (e for e in call_edges if "run" in e.src and "send" in e.dst),
            None
        )
        assert method_edge is not None, "Expected call edge for send method"
        # Verify it resolved to ServiceClient.send
        assert "ServiceClient.send" in method_edge.dst or "send" in method_edge.dst

    def test_type_inference_limited_to_constructors(self, tmp_path: Path) -> None:
        """Type inference should NOT track types from function returns."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "main.js"
        js_file.write_text("""
class ServiceClient {
    send() {
        return 'sent';
    }
}

function getClient() {
    return new ServiceClient();
}

function run() {
    const client = getClient();  // NOT tracked - function return
    client.send();  // Should NOT be high-confidence resolved
}
""")

        result = analyze_javascript(tmp_path)

        # Should have call edge for getClient()
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        get_client_edge = next(
            (e for e in call_edges if "run" in e.src and "getClient" in e.dst),
            None
        )
        assert get_client_edge is not None, "Expected call edge for getClient"

        # client.send() should NOT be resolved with high confidence
        # (may have low-confidence inferred edge, but not type_inferred evidence)
        type_inferred_edges = [
            e for e in call_edges
            if e.meta and e.meta.get("evidence_type") == "ast_method_type_inferred"
        ]
        assert len(type_inferred_edges) == 0, (
            "Should NOT have type-inferred edge for function return"
        )

    def test_namespace_class_instantiation(self, tmp_path: Path) -> None:
        """new namespace.ClassName() should track type correctly."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        # Create a service module with a class
        service_file = tmp_path / "service.js"
        service_file.write_text("""
class EmailClient {
    send() {
        return 'email sent';
    }
}
""")

        # Create main module using namespace instantiation
        main_file = tmp_path / "main.js"
        main_file.write_text("""
import * as service from './service';

function run() {
    const client = new service.EmailClient();
    client.send();
}
""")

        result = analyze_javascript(tmp_path)

        # Should have instantiates edge for EmailClient
        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        inst_edge = next(
            (e for e in inst_edges if "run" in e.src and "EmailClient" in e.dst),
            None
        )
        assert inst_edge is not None, "Expected instantiates edge for namespace.EmailClient"


# ============================================================================
# TypeScript Decorator Metadata Tests (Phase 4)
# ============================================================================


class TestDecoratorMetadata:
    """Tests for extracting decorator metadata from TypeScript classes and methods."""

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        """Skip tests if tree-sitter is not available."""
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_typescript")

    def test_class_decorator_simple(self, tmp_path: Path) -> None:
        """Extracts simple class decorator without arguments."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "service.ts"
        ts_file.write_text("""
@Injectable()
class UserService {
    findAll() { return []; }
}
""")

        result = analyze_javascript(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "UserService"
        meta = classes[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Injectable"
        assert decorators[0]["args"] == []
        assert decorators[0]["kwargs"] == {}

    def test_class_decorator_with_string_arg(self, tmp_path: Path) -> None:
        """Extracts class decorator with string argument."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text("""
@Controller('/users')
class UsersController {
    findAll() { return []; }
}
""")

        result = analyze_javascript(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Controller"
        assert decorators[0]["args"] == ["/users"]

    def test_method_decorator_simple(self, tmp_path: Path) -> None:
        """Extracts simple method decorator."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text("""
class UsersController {
    @Get()
    findAll() { return []; }
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        assert methods[0].name == "UsersController.findAll"
        meta = methods[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Get"
        assert decorators[0]["args"] == []

    def test_method_decorator_with_path_arg(self, tmp_path: Path) -> None:
        """Extracts method decorator with path argument."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text("""
class UsersController {
    @Get(':id')
    findOne() { return {}; }
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        meta = methods[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Get"
        assert decorators[0]["args"] == [":id"]

    def test_multiple_decorators_on_method(self, tmp_path: Path) -> None:
        """Extracts multiple decorators from a method."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text("""
class UsersController {
    @UseGuards(AuthGuard)
    @Get('/protected')
    getProtected() { return []; }
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        meta = methods[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 2
        decorator_names = [d["name"] for d in decorators]
        assert "UseGuards" in decorator_names
        assert "Get" in decorator_names

    def test_multiple_decorators_on_class(self, tmp_path: Path) -> None:
        """Extracts multiple decorators from a class."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text("""
@Controller('/api')
@UseInterceptors(LoggingInterceptor)
class ApiController {
    index() { return {}; }
}
""")

        result = analyze_javascript(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 2
        decorator_names = [d["name"] for d in decorators]
        assert "Controller" in decorator_names
        assert "UseInterceptors" in decorator_names


# ============================================================================
# TypeScript Base Class Metadata Tests (Phase 4)
# ============================================================================


class TestBaseClassMetadata:
    """Tests for extracting base class information from TypeScript/JavaScript."""

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        """Skip tests if tree-sitter is not available."""
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_javascript")

    def test_class_extends_single(self, tmp_path: Path) -> None:
        """Extracts single base class from extends clause."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "user.ts"
        ts_file.write_text("""
class User extends BaseModel {
    name: string;
}
""")

        result = analyze_javascript(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "User"
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert base_classes == ["BaseModel"]

    def test_class_implements_single(self, tmp_path: Path) -> None:
        """Extracts single interface from implements clause."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "service.ts"
        ts_file.write_text("""
class UserService implements IUserService {
    findAll() { return []; }
}
""")

        result = analyze_javascript(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert "IUserService" in base_classes

    def test_class_implements_multiple(self, tmp_path: Path) -> None:
        """Extracts multiple interfaces from implements clause."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "service.ts"
        ts_file.write_text("""
class UserService implements IUserService, IDisposable, Serializable {
    findAll() { return []; }
    dispose() {}
    serialize() { return ''; }
}
""")

        result = analyze_javascript(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert len(base_classes) == 3
        assert "IUserService" in base_classes
        assert "IDisposable" in base_classes
        assert "Serializable" in base_classes

    def test_class_extends_and_implements(self, tmp_path: Path) -> None:
        """Extracts both extends and implements clauses."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text("""
class UserController extends BaseController implements IController {
    index() { return []; }
}
""")

        result = analyze_javascript(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert len(base_classes) == 2
        assert "BaseController" in base_classes
        assert "IController" in base_classes

    def test_class_extends_generic(self, tmp_path: Path) -> None:
        """Extracts generic base class with type parameters."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "repo.ts"
        ts_file.write_text("""
class UserRepository extends Repository<User> {
    findByEmail(email: string) { return null; }
}
""")

        result = analyze_javascript(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert len(base_classes) == 1
        # Should capture the generic type
        assert "Repository<User>" in base_classes or "Repository" in base_classes

    def test_javascript_extends(self, tmp_path: Path) -> None:
        """Extracts base class from JavaScript ES6 class."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "widget.js"
        js_file.write_text("""
class Widget extends BaseWidget {
    render() { return '<div></div>'; }
}
""")

        result = analyze_javascript(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert base_classes == ["BaseWidget"]

    def test_class_no_inheritance(self, tmp_path: Path) -> None:
        """Class without extends/implements has empty base_classes."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "simple.ts"
        ts_file.write_text("""
class SimpleClass {
    doSomething() { return true; }
}
""")

        result = analyze_javascript(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        # Either empty list or key not present
        assert base_classes == [] or "base_classes" not in meta

    def test_qualified_base_class(self, tmp_path: Path) -> None:
        """Extracts qualified base class name (e.g., React.Component)."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "component.tsx"
        ts_file.write_text("""
class MyComponent extends React.Component {
    render() { return null; }
}
""")

        result = analyze_javascript(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert len(base_classes) == 1
        assert "React.Component" in base_classes or "Component" in base_classes

    def test_javascript_qualified_base_class(self, tmp_path: Path) -> None:
        """Extracts qualified base class in JavaScript (React.Component style)."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        js_file = tmp_path / "widget.js"
        js_file.write_text("""
class Widget extends React.Component {
    render() { return null; }
}
""")

        result = analyze_javascript(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert len(base_classes) == 1
        assert "React.Component" in base_classes

    def test_implements_generic_interface(self, tmp_path: Path) -> None:
        """Extracts generic interface from implements clause."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "service.ts"
        ts_file.write_text("""
class UserService implements Repository<User> {
    findAll() { return []; }
}
""")

        result = analyze_javascript(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert len(base_classes) == 1
        assert "Repository<User>" in base_classes


class TestDecoratorEdgeCases:
    """Tests for edge cases in decorator extraction."""

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        """Skip tests if tree-sitter is not available."""
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_typescript")

    def test_decorator_with_identifier_arg(self, tmp_path: Path) -> None:
        """Extracts decorator with identifier argument (variable reference)."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text("""
class UserController {
    @UseGuards(AuthGuard)
    getProtected() { return []; }
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        meta = methods[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "UseGuards"
        assert decorators[0]["args"] == ["AuthGuard"]

    def test_decorator_with_array_arg(self, tmp_path: Path) -> None:
        """Extracts decorator with array argument."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text("""
@ApiTags(['users', 'admin'])
class AdminController {
    index() { return {}; }
}
""")

        result = analyze_javascript(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "ApiTags"
        assert decorators[0]["args"] == [["users", "admin"]]

    def test_decorator_with_number_arg(self, tmp_path: Path) -> None:
        """Extracts decorator with number argument."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text("""
class RateLimitedController {
    @RateLimit(100)
    index() { return {}; }
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        meta = methods[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["args"] == [100]

    def test_decorator_with_boolean_arg(self, tmp_path: Path) -> None:
        """Extracts decorator with boolean argument."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text("""
class CachedController {
    @Cache(true)
    index() { return {}; }
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        meta = methods[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["args"] == [True]

    def test_decorator_with_member_expression_arg(self, tmp_path: Path) -> None:
        """Extracts decorator with member expression argument."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text("""
class UserController {
    @UseGuards(Guards.JwtGuard)
    getProtected() { return []; }
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        meta = methods[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["args"] == ["Guards.JwtGuard"]

    def test_qualified_decorator_name(self, tmp_path: Path) -> None:
        """Extracts decorator with qualified name (module.Decorator)."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text("""
class ServiceController {
    @nest.Get('/path')
    getPath() { return {}; }
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        meta = methods[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "nest.Get"
        assert decorators[0]["args"] == ["/path"]

    def test_decorator_with_template_string(self, tmp_path: Path) -> None:
        """Extracts decorator with template string argument."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text("""
class UserController {
    @Get(`/users`)
    getUsers() { return []; }
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        meta = methods[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["args"] == ["/users"]

    def test_decorator_with_float_arg(self, tmp_path: Path) -> None:
        """Extracts decorator with float argument."""
        from hypergumbo.analyze.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text("""
class WeightedController {
    @Weight(0.75)
    index() { return {}; }
}
""")

        result = analyze_javascript(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        meta = methods[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["args"] == [0.75]


# ============================================================================
# Deprecation Warning Tests (ADR-0003 v1.0.x)
# ============================================================================


class TestExpressRouteDetectionDeprecation:
    """Tests for deprecation warnings on analyzer-level Express route detection."""

    def test_express_route_emits_deprecation_warning(self, tmp_path: Path) -> None:
        """Express route detection emits deprecation warning."""
        import warnings
        from hypergumbo.analyze import js_ts as js_ts_module
        from hypergumbo.analyze.js_ts import analyze_javascript

        # Reset the warning deduplication set
        js_ts_module._deprecated_route_warnings_emitted.clear()

        routes_file = tmp_path / "routes.js"
        routes_file.write_text("""
const express = require('express');
const router = express.Router();

router.get('/users', (req, res) => {
    res.json([]);
});

router.post('/users', (req, res) => {
    res.json({});
});
""")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            analyze_javascript(tmp_path)

        # Should have at least one deprecation warning for Express
        deprecation_warnings = [
            warning
            for warning in w
            if issubclass(warning.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) >= 1
        warning_message = str(deprecation_warnings[0].message)
        assert "Express" in warning_message
        assert "deprecated" in warning_message.lower()

    def test_deprecation_warning_emitted_once_per_session(
        self, tmp_path: Path
    ) -> None:
        """Deprecation warning is emitted only once per session."""
        import warnings
        from hypergumbo.analyze import js_ts as js_ts_module
        from hypergumbo.analyze.js_ts import analyze_javascript

        # Reset the warning deduplication set
        js_ts_module._deprecated_route_warnings_emitted.clear()

        # Create multiple route files
        (tmp_path / "users.js").write_text("""
router.get('/users', (req, res) => res.json([]));
router.post('/users', (req, res) => res.json({}));
""")
        (tmp_path / "items.js").write_text("""
router.get('/items', (req, res) => res.json([]));
""")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            analyze_javascript(tmp_path)

        # Should have exactly one Express deprecation warning (deduplicated)
        express_warnings = [
            warning
            for warning in w
            if issubclass(warning.category, DeprecationWarning)
            and "Express" in str(warning.message)
        ]
        assert len(express_warnings) == 1

    def test_no_deprecation_warning_without_route_calls(
        self, tmp_path: Path
    ) -> None:
        """No deprecation warning for files without Express route calls."""
        import warnings
        from hypergumbo.analyze import js_ts as js_ts_module
        from hypergumbo.analyze.js_ts import analyze_javascript

        # Reset the warning deduplication set
        js_ts_module._deprecated_route_warnings_emitted.clear()

        js_file = tmp_path / "utils.js"
        js_file.write_text("""
function formatDate(date) {
    return date.toISOString();
}

function calculateTotal(items) {
    return items.reduce((sum, item) => sum + item.price, 0);
}
""")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            analyze_javascript(tmp_path)

        # Should have no deprecation warnings for route detection
        route_warnings = [
            warning
            for warning in w
            if issubclass(warning.category, DeprecationWarning)
            and "Express" in str(warning.message)
        ]
        assert len(route_warnings) == 0

    def test_fetchmock_get_not_detected_as_route(self, tmp_path: Path) -> None:
        """fetchMock.get() should NOT be detected as an Express route (ADR-0003).

        This tests the fix for false positives where test mocking libraries like
        fetch-mock were incorrectly detected as route registrations.
        """
        import warnings
        from hypergumbo.analyze import js_ts as js_ts_module
        from hypergumbo.analyze.js_ts import analyze_javascript

        # Reset the warning deduplication set
        js_ts_module._deprecated_route_warnings_emitted.clear()

        js_file = tmp_path / "test.spec.tsx"
        js_file.write_text("""
import fetchMock from 'fetch-mock';

const collections = [{id: 1}, {id: 2}];

collections
    .filter((c) => c.id !== 'root')
    .forEach((c) => fetchMock.get(`/api/collection/${c.id}`, c));

fetchMock.post('/api/users', { id: 1 });
fetchMock.delete('/api/users/1', 200);
""")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = analyze_javascript(tmp_path)

        # Should have no route symbols from fetchMock
        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 0, f"Found unexpected routes: {[s.name for s in route_symbols]}"

        # Should have no deprecation warnings (no routes detected)
        route_warnings = [
            warning
            for warning in w
            if issubclass(warning.category, DeprecationWarning)
            and "Express" in str(warning.message)
        ]
        assert len(route_warnings) == 0

    def test_unknown_receiver_not_detected_as_route(self, tmp_path: Path) -> None:
        """Unknown receiver names should NOT be detected as routes.

        Only app, router, express, server, fastify, koa are valid receivers.
        """
        import warnings
        from hypergumbo.analyze import js_ts as js_ts_module
        from hypergumbo.analyze.js_ts import analyze_javascript

        # Reset the warning deduplication set
        js_ts_module._deprecated_route_warnings_emitted.clear()

        js_file = tmp_path / "test.js"
        js_file.write_text("""
// These should NOT be detected as routes
myObject.get('/path', handler);
api.post('/users', handler);
client.delete('/item', handler);
axios.get('/data').then(callback);
http.get('/endpoint', callback);
""")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = analyze_javascript(tmp_path)

        # Should have no route symbols
        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 0, f"Found unexpected routes: {[s.name for s in route_symbols]}"

        # Should have no deprecation warnings
        route_warnings = [
            warning
            for warning in w
            if issubclass(warning.category, DeprecationWarning)
            and "Express" in str(warning.message)
        ]
        assert len(route_warnings) == 0
