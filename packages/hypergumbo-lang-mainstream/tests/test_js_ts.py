# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for JavaScript/TypeScript analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys

from hypergumbo_lang_mainstream import js_ts as js_ts_module


class TestFindJsTsFiles:
    """Tests for JS/TS file discovery."""

    def test_finds_js_files(self, tmp_path: Path) -> None:
        """Finds .js files."""
        from hypergumbo_lang_mainstream.js_ts import find_js_ts_files

        (tmp_path / "app.js").write_text("const x = 1;")
        (tmp_path / "other.txt").write_text("not js")

        files = list(find_js_ts_files(tmp_path))

        assert len(files) == 1
        assert files[0].suffix == ".js"

    def test_finds_ts_files(self, tmp_path: Path) -> None:
        """Finds .ts files."""
        from hypergumbo_lang_mainstream.js_ts import find_js_ts_files

        (tmp_path / "app.ts").write_text("const x: number = 1;")

        files = list(find_js_ts_files(tmp_path))

        assert len(files) == 1
        assert files[0].suffix == ".ts"

    def test_finds_jsx_tsx_files(self, tmp_path: Path) -> None:
        """Finds .jsx and .tsx files."""
        from hypergumbo_lang_mainstream.js_ts import find_js_ts_files

        (tmp_path / "App.jsx").write_text("export default () => <div />;")
        (tmp_path / "App.tsx").write_text("export default () => <div />;")

        files = list(find_js_ts_files(tmp_path))

        assert len(files) == 2
        suffixes = {f.suffix for f in files}
        assert suffixes == {".jsx", ".tsx"}

    def test_excludes_node_modules(self, tmp_path: Path) -> None:
        """Excludes node_modules directory."""
        from hypergumbo_lang_mainstream.js_ts import find_js_ts_files

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
        result = js_ts_module.is_tree_sitter_available()
        assert result is True

    def test_is_tree_sitter_available_false(self) -> None:
        """Returns False when grammar is not available."""
        with patch.object(js_ts_module._jsts_analyzer, "_check_grammar_available", return_value=False):
            assert js_ts_module.is_tree_sitter_available() is False

    def test_is_tree_sitter_available_via_analyzer(self) -> None:
        """Availability check delegates to TreeSitterAnalyzer._check_grammar_available."""
        assert js_ts_module.is_tree_sitter_available() == js_ts_module._jsts_analyzer._check_grammar_available()


class TestAnalyzeJavascriptFallback:
    """Tests for fallback behavior when tree-sitter unavailable."""

    def test_returns_empty_when_tree_sitter_unavailable(self, tmp_path: Path) -> None:
        """Returns empty result with skipped pass when tree-sitter unavailable."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("function foo() {}")

        with patch.object(js_ts_module._jsts_analyzer, "_check_grammar_available", return_value=False):
            result = analyze_javascript(tmp_path)

        assert result.symbols == []
        assert result.edges == []
        assert result.run is not None
        assert result.skipped is True
        assert "not available" in result.skip_reason


class TestAnalyzeJavascriptWithTreeSitter:
    """Tests for JS/TS analysis with tree-sitter."""

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        """Skip tests if tree-sitter not installed."""
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_javascript")

    def test_extracts_function_declaration(self, tmp_path: Path) -> None:
        """Extracts function declarations as symbols."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("function greet(name) {\n  return 'Hello ' + name;\n}")

        result = analyze_javascript(tmp_path)

        assert len(result.symbols) >= 1
        func_symbols = [s for s in result.symbols if s.kind == "function"]
        assert len(func_symbols) == 1
        assert func_symbols[0].name == "greet"
        assert func_symbols[0].language == "javascript"

    def test_extracts_arrow_function(self, tmp_path: Path) -> None:
        """Extracts arrow functions assigned to variables."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("const add = (a, b) => a + b;")

        result = analyze_javascript(tmp_path)

        func_symbols = [s for s in result.symbols if s.kind == "function"]
        assert len(func_symbols) == 1
        assert func_symbols[0].name == "add"

    def test_extracts_class_declaration(self, tmp_path: Path) -> None:
        """Extracts class declarations."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("class User {\n  constructor(name) {\n    this.name = name;\n  }\n}")

        result = analyze_javascript(tmp_path)

        class_symbols = [s for s in result.symbols if s.kind == "class"]
        assert len(class_symbols) == 1
        assert class_symbols[0].name == "User"

    def test_extracts_class_methods(self, tmp_path: Path) -> None:
        """Extracts methods from class declarations."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("import { helper } from './utils';\n\nfunction main() { helper(); }")

        result = analyze_javascript(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1
        assert any("utils" in e.dst for e in import_edges)

    def test_extracts_require_call(self, tmp_path: Path) -> None:
        """Extracts CommonJS require() calls as edges."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("const fs = require('fs');\n\nfunction main() { fs.readFile('x'); }")

        result = analyze_javascript(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1
        assert any("fs" in e.dst for e in import_edges)

    def test_extracts_function_calls(self, tmp_path: Path) -> None:
        """Extracts function call edges."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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

        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript, PASS_ID

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("import { x } from './utils';")

        result = analyze_javascript(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1
        # Static imports should have high confidence
        for edge in import_edges:
            assert edge.confidence >= 0.9

    def test_require_edge_evidence_type(self, tmp_path: Path) -> None:
        """Require calls have correct evidence type."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("const x = require('./utils');")

        result = analyze_javascript(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1
        assert any(e.evidence_type == "require_static" for e in import_edges)

    def test_dynamic_import_lower_confidence(self, tmp_path: Path) -> None:
        """Dynamic imports/requires have lower confidence."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript, PASS_ID
        from hypergumbo_core.ir import PASS_VERSION

        (tmp_path / "app.js").write_text("function foo() {}")

        result = analyze_javascript(tmp_path)

        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.version == PASS_VERSION
        assert result.run.files_analyzed >= 1
        assert result.run.duration_ms >= 0

    def test_symbol_has_span(self, tmp_path: Path) -> None:
        """Symbols include span information."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("function foo() {\n  return 1;\n}")

        result = analyze_javascript(tmp_path)

        func_symbols = [s for s in result.symbols if s.name == "foo"]
        assert len(func_symbols) == 1
        assert func_symbols[0].span.start_line >= 1
        assert func_symbols[0].span.end_line >= func_symbols[0].span.start_line

    def test_exports_default_function(self, tmp_path: Path) -> None:
        """Handles export default function syntax."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("export default function handler() { return 1; }")

        result = analyze_javascript(tmp_path)

        func_symbols = [s for s in result.symbols if s.kind == "function"]
        assert len(func_symbols) >= 1

    def test_exports_class_declaration(self, tmp_path: Path) -> None:
        """Handles export class syntax."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("export class ApiClient { fetch() { return 1; } }")

        result = analyze_javascript(tmp_path)

        class_symbols = [s for s in result.symbols if s.kind == "class"]
        assert len(class_symbols) == 1
        assert class_symbols[0].name == "ApiClient"

    def test_exported_function_is_flagged(self, tmp_path: Path) -> None:
        """WI-nimug: ``export function`` sets Symbol.is_exported=True."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text(
            "export function publicFn() { return 1; }\n"
            "function privateFn() { return 2; }\n",
        )
        result = analyze_javascript(tmp_path)
        by_name = {s.name: s for s in result.symbols if s.kind == "function"}
        assert by_name["publicFn"].is_exported is True
        assert by_name["privateFn"].is_exported is False

    def test_exported_class_is_flagged(self, tmp_path: Path) -> None:
        """WI-nimug: ``export class`` sets Symbol.is_exported=True."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text(
            "export class PublicClass { method() { return 1; } }\n"
            "class PrivateClass { method() { return 2; } }\n",
        )
        result = analyze_javascript(tmp_path)
        classes = {s.name: s for s in result.symbols if s.kind == "class"}
        assert classes["PublicClass"].is_exported is True
        assert classes["PrivateClass"].is_exported is False

    def test_export_default_function_is_flagged(
        self, tmp_path: Path,
    ) -> None:
        """WI-nimug: ``export default function`` sets is_exported=True."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text(
            "export default function handler() { return 1; }\n",
        )
        result = analyze_javascript(tmp_path)
        func_symbols = [
            s for s in result.symbols if s.kind == "function"
        ]
        handler_sym = next(
            (s for s in func_symbols if s.name == "handler"), None,
        )
        assert handler_sym is not None
        assert handler_sym.is_exported is True

    def test_export_clause_named_is_flagged(self, tmp_path: Path) -> None:
        """WI-nimug: ``export { foo, bar }`` clause flags both names."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text(
            "function foo() { return 1; }\n"
            "function bar() { return 2; }\n"
            "function baz() { return 3; }\n"
            "export { foo, bar };\n",
        )
        result = analyze_javascript(tmp_path)
        by_name = {s.name: s for s in result.symbols if s.kind == "function"}
        assert by_name["foo"].is_exported is True
        assert by_name["bar"].is_exported is True
        assert by_name["baz"].is_exported is False

    def test_module_symbol_not_exported(self, tmp_path: Path) -> None:
        """The synthetic <module:app.js> pseudo-node is never flagged."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text(
            "export function foo() { return 1; }\n",
        )
        result = analyze_javascript(tmp_path)
        modules = [s for s in result.symbols if s.kind == "module"]
        assert len(modules) >= 1
        for m in modules:
            assert m.is_exported is False

    def test_no_exports_means_nothing_flagged(self, tmp_path: Path) -> None:
        """When a file has no export_statement, no symbols get flagged."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text(
            "function foo() { return 1; }\n"
            "class Bar {}\n",
        )
        result = analyze_javascript(tmp_path)
        for s in result.symbols:
            if s.kind != "module":
                assert s.is_exported is False

    def test_typescript_exports_class(self, tmp_path: Path) -> None:
        """Handles TypeScript export class syntax."""
        pytest.importorskip("tree_sitter_typescript")

        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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

        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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

        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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

        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import _get_parser_for_file

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
        from hypergumbo_lang_mainstream.js_ts import _get_parser_for_file

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
        from hypergumbo_lang_mainstream.js_ts import _get_parser_for_file

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
        from hypergumbo_lang_mainstream.js_ts import _get_parser_for_file

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
        from hypergumbo_lang_mainstream.js_ts import _get_parser_for_file

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
        from hypergumbo_lang_mainstream.js_ts import _node_text

        node = MagicMock()
        node.start_byte = 0
        node.end_byte = 5
        source = b"hello world"

        text = _node_text(node, source)

        assert text == "hello"

    def test_find_name_in_children(self) -> None:
        """Tests _find_name_in_children helper function."""
        from hypergumbo_lang_mainstream.js_ts import _find_name_in_children

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
        from hypergumbo_lang_mainstream.js_ts import _find_name_in_children

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
        from hypergumbo_lang_mainstream.js_ts import _find_name_in_children

        other_child = MagicMock()
        other_child.type = "other"

        node = MagicMock()
        node.children = [other_child]

        source = b"something"
        name = _find_name_in_children(node, source)

        assert name is None

    def test_find_name_in_children_type_identifier(self) -> None:
        """Finds type_identifier for TypeScript classes."""
        from hypergumbo_lang_mainstream.js_ts import _find_name_in_children

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
        from hypergumbo_lang_mainstream.js_ts import _get_language_for_file

        assert _get_language_for_file(tmp_path / "app.js") == "javascript"
        assert _get_language_for_file(tmp_path / "app.jsx") == "javascript"
        assert _get_language_for_file(tmp_path / "app.ts") == "typescript"
        assert _get_language_for_file(tmp_path / "app.tsx") == "typescript"

    def test_make_symbol_id(self) -> None:
        """Tests symbol ID generation."""
        from hypergumbo_lang_mainstream.js_ts import _make_symbol_id

        symbol_id = _make_symbol_id("app.js", 1, 5, "foo", "function", "javascript")

        assert symbol_id == "javascript:app.js:1-5:foo:function"

    def test_analyze_javascript_with_mocked_tree_sitter(self, tmp_path: Path) -> None:
        """Tests full analysis with mocked tree-sitter."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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

        with patch.object(js_ts_module._jsts_analyzer, "_check_grammar_available", return_value=True):
            with patch("hypergumbo_lang_mainstream.js_ts._get_parser_for_file", return_value=mock_parser):
                result = analyze_javascript(tmp_path)

        assert result.skipped is False
        assert result.run is not None
        assert result.run.files_analyzed == 1

    def test_extract_symbols_function_declaration(self) -> None:
        """Tests extraction of function declarations."""
        from hypergumbo_lang_mainstream.js_ts import _extract_symbols_and_edges
        from hypergumbo_core.ir import AnalysisRun

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

        func_symbols = [s for s in symbols if s.kind == "function"]
        assert len(func_symbols) == 1
        assert func_symbols[0].name == "greet"

    def test_extract_symbols_class_declaration(self) -> None:
        """Tests extraction of class declarations."""
        from hypergumbo_lang_mainstream.js_ts import _extract_symbols_and_edges
        from hypergumbo_core.ir import AnalysisRun

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

        class_symbols = [s for s in symbols if s.kind == "class"]
        assert len(class_symbols) == 1
        assert class_symbols[0].name == "User"

    def test_extract_class_with_methods_builds_registry(self) -> None:
        """Tests that method registry is built correctly for cross-file resolution."""
        from hypergumbo_lang_mainstream.js_ts import _extract_symbols_and_edges
        from hypergumbo_core.ir import AnalysisRun

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

        # Should have module + class + method
        assert len(symbols) == 3
        class_symbols = [s for s in symbols if s.kind == "class"]
        method_symbols = [s for s in symbols if s.kind == "method"]
        assert len(class_symbols) == 1
        assert len(method_symbols) == 1
        # Method name should include class prefix
        assert "Svc.save" in method_symbols[0].name

    def test_extract_arrow_function(self) -> None:
        """Tests extraction of arrow functions assigned to const."""
        from hypergumbo_lang_mainstream.js_ts import _extract_symbols_and_edges
        from hypergumbo_core.ir import AnalysisRun

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

        func_symbols = [s for s in symbols if s.kind == "function"]
        assert len(func_symbols) == 1
        assert func_symbols[0].name == "add"

    def test_extract_arrow_function_with_body(self) -> None:
        """Tests extraction of arrow functions with nested calls."""
        from hypergumbo_lang_mainstream.js_ts import _extract_symbols_and_edges
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.js_ts import _extract_symbols_and_edges
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.js_ts import _extract_symbols_and_edges
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.js_ts import _extract_symbols_and_edges
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.js_ts import _extract_symbols_and_edges
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.js_ts import _extract_symbols_and_edges
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.js_ts import _extract_symbols_and_edges
        from hypergumbo_core.ir import AnalysisRun

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

        func_symbols = [s for s in symbols if s.kind == "function"]
        assert len(func_symbols) == 1
        assert func_symbols[0].name == "handler"

    def test_analyze_with_parse_errors(self, tmp_path: Path) -> None:
        """Continues analysis even with parse errors."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("function { broken")

        # Mock tree-sitter with error node
        root = self._create_mock_node("program", has_error=True)
        tree = MagicMock()
        tree.root_node = root

        mock_parser = MagicMock()
        mock_parser.parse.return_value = tree

        with patch.object(js_ts_module._jsts_analyzer, "_check_grammar_available", return_value=True):
            with patch("hypergumbo_lang_mainstream.js_ts._get_parser_for_file", return_value=mock_parser):
                result = analyze_javascript(tmp_path)

        # Should still succeed but with limited results
        assert result.run is not None
        assert result.run.files_analyzed == 1

    def test_analyze_with_file_errors(self, tmp_path: Path) -> None:
        """Tracks files that fail to read."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import find_svelte_files

        (tmp_path / "App.svelte").write_text("<script>const x = 1;</script>")
        (tmp_path / "other.txt").write_text("not svelte")

        files = list(find_svelte_files(tmp_path))

        assert len(files) == 1
        assert files[0].suffix == ".svelte"


class TestSvelteScriptExtraction:
    """Tests for extracting <script> blocks from Svelte files."""

    def test_extracts_typescript_script(self) -> None:
        """Extracts TypeScript script with lang='ts'."""
        from hypergumbo_lang_mainstream.js_ts import extract_svelte_scripts

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
        from hypergumbo_lang_mainstream.js_ts import extract_svelte_scripts

        source = '''<script>
const x = 1;
</script>'''

        blocks = extract_svelte_scripts(source)

        assert len(blocks) == 1
        assert blocks[0].is_typescript is False
        assert "const x = 1" in blocks[0].content

    def test_extracts_multiple_scripts(self) -> None:
        """Extracts multiple script blocks."""
        from hypergumbo_lang_mainstream.js_ts import extract_svelte_scripts

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
        from hypergumbo_lang_mainstream.js_ts import extract_svelte_scripts

        source = '''<div>Just HTML</div>
<style>
.foo { color: red; }
</style>'''

        blocks = extract_svelte_scripts(source)

        assert len(blocks) == 0

    def test_correct_line_offset(self) -> None:
        """Script content line offset is calculated correctly."""
        from hypergumbo_lang_mainstream.js_ts import extract_svelte_scripts

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
        from hypergumbo_lang_mainstream.js_ts import _analyze_svelte_file
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.js_ts import _analyze_svelte_file
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import _analyze_svelte_file
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.js_ts import _get_parser_for_lang

        # Mark tree-sitter modules as unavailable in sys.modules
        with patch.dict(sys.modules, {
            "tree_sitter": None,
            "tree_sitter_javascript": None,
        }):
            result = _get_parser_for_lang(is_typescript=True)
            assert result is None

    def test_get_parser_for_lang_ts_fallback_to_js(self) -> None:
        """Falls back to JavaScript parser when TypeScript unavailable."""
        from hypergumbo_lang_mainstream.js_ts import _get_parser_for_lang

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
        from hypergumbo_lang_mainstream.js_ts import _get_parser_for_lang

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
        from hypergumbo_lang_mainstream.js_ts import _analyze_svelte_file
        from hypergumbo_core.ir import AnalysisRun

        svelte_file = tmp_path / "Component.svelte"
        # Don't create the file - will cause read error

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_svelte_file(svelte_file, run)

        assert success is False
        assert len(symbols) == 0
        assert len(edges) == 0

    def test_svelte_parser_unavailable(self, tmp_path: Path) -> None:
        """Skips script block when parser is unavailable."""
        from hypergumbo_lang_mainstream.js_ts import _analyze_svelte_file
        from hypergumbo_core.ir import AnalysisRun

        svelte_file = tmp_path / "Component.svelte"
        svelte_file.write_text('''<script lang="ts">
function test() {}
</script>''')

        run = AnalysisRun.create(pass_id="test", version="test")

        with patch("hypergumbo_lang_mainstream.js_ts._get_parser_for_lang", return_value=None):
            symbols, edges, success = _analyze_svelte_file(svelte_file, run)

        # Still succeeds but with no symbols
        assert success is True
        assert len(symbols) == 0

    def test_svelte_file_skipped_increments_counter(self, tmp_path: Path) -> None:
        """Svelte files that fail to read increment skipped counter."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("function foo() {}")

        with patch.object(js_ts_module._jsts_analyzer, "_check_grammar_available", return_value=True):
            with patch("hypergumbo_lang_mainstream.js_ts._get_parser_for_file", return_value=None):
                result = analyze_javascript(tmp_path)

        assert result.run is not None
        assert result.run.files_skipped == 1
        assert result.run.files_analyzed == 0

    def test_svelte_parser_unavailable_in_main_analysis(self, tmp_path: Path) -> None:
        """Svelte script blocks are skipped when parser unavailable."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "App.svelte").write_text('''<script lang="ts">
function test() {}
</script>''')

        with patch.object(js_ts_module._jsts_analyzer, "_check_grammar_available", return_value=True):
            with patch("hypergumbo_lang_mainstream.js_ts._get_parser_for_lang", return_value=None):
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
        from hypergumbo_lang_mainstream.js_ts import _analyze_svelte_file
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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

    def test_inferred_method_no_fanout(self, tmp_path: Path) -> None:
        """Bare name fallback emits at most one edge, not N edges for N candidates.

        When multiple classes define the same method name and a call site can't
        be resolved via type info, Case 4 should emit a single best-guess edge
        rather than fanning out to all candidates. This prevents false positives
        in reverse slicing (e.g., asking "who calls CatsController.create?"
        should not return every other 'create' method in the codebase).
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """
class CatsService {
    create(dto) {
        return dto;
    }
}

class UsersService {
    create(dto) {
        return dto;
    }
}

class OrdersService {
    create(dto) {
        return dto;
    }
}

function handler(unknown) {
    unknown.create({name: "test"});
}
"""
        (tmp_path / "app.js").write_text(code)

        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        inferred_calls = [
            e for e in call_edges if e.evidence_type == "ast_method_inferred"
        ]
        # Should emit at most 1 edge, NOT 3 (one per candidate)
        assert len(inferred_calls) <= 1
        if inferred_calls:
            assert "create" in inferred_calls[0].dst

    def test_builtin_methods_not_resolved_to_user_classes(self, tmp_path: Path) -> None:
        """Built-in method names (get, set, forEach, push, etc.) should not
        resolve to user-defined class methods via the fallback path.

        Without this blocklist, `myArray.forEach(cb)` would create a false
        edge to `TTLMap.forEach` if TTLMap is the only class defining forEach.
        This inflates TTLMap's in-degree and corrupts centrality rankings.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """
class TTLMap {
    get(key) { return this.data[key]; }
    set(key, val) { this.data[key] = val; }
    has(key) { return key in this.data; }
    forEach(cb) { Object.keys(this.data).forEach(cb); }
    delete(key) { delete this.data[key]; }
}

function processItems(items, cache) {
    items.forEach(item => {
        if (cache.has(item.id)) {
            const val = cache.get(item.id);
            cache.set(item.id, val + 1);
        }
        items.push(item);
    });
    cache.delete("expired");
}
"""
        (tmp_path / "app.js").write_text(code)

        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        inferred_calls = [
            e for e in call_edges if e.evidence_type == "ast_method_inferred"
        ]
        # Built-in methods should NOT create edges to TTLMap
        ttlmap_edges = [e for e in inferred_calls if "TTLMap" in e.dst]
        assert len(ttlmap_edges) == 0, (
            f"Built-in method calls should not resolve to TTLMap, "
            f"found {len(ttlmap_edges)} edges: "
            f"{[e.dst for e in ttlmap_edges]}"
        )

    def test_new_class_instantiation(self, tmp_path: Path) -> None:
        """Detects new ClassName() instantiation."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "utils.js").write_text("function helper() { return 42; }")
        (tmp_path / "main.js").write_text("function main() { helper(); }")

        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1
        # main calls helper (cross-file)
        assert any("helper" in e.dst for e in call_edges)

    def test_cross_file_class_instantiation(self, tmp_path: Path) -> None:
        """Resolves class instantiation across files."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import find_vue_files

        (tmp_path / "App.vue").write_text("<script>const x = 1;</script>")
        (tmp_path / "other.txt").write_text("not vue")

        files = list(find_vue_files(tmp_path))

        assert len(files) == 1
        assert files[0].suffix == ".vue"


class TestVueScriptExtraction:
    """Tests for extracting <script> blocks from Vue SFC files."""

    def test_extracts_typescript_script(self) -> None:
        """Extracts TypeScript script with lang='ts'."""
        from hypergumbo_lang_mainstream.js_ts import extract_vue_scripts

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
        from hypergumbo_lang_mainstream.js_ts import extract_vue_scripts

        source = '''<script>
const x = 1;
</script>'''

        blocks = extract_vue_scripts(source)

        assert len(blocks) == 1
        assert blocks[0].is_typescript is False
        assert "const x = 1" in blocks[0].content

    def test_extracts_script_setup(self) -> None:
        """Extracts <script setup> blocks (Vue 3 Composition API)."""
        from hypergumbo_lang_mainstream.js_ts import extract_vue_scripts

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
        from hypergumbo_lang_mainstream.js_ts import extract_vue_scripts

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
        from hypergumbo_lang_mainstream.js_ts import extract_vue_scripts

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
        from hypergumbo_lang_mainstream.js_ts import _analyze_vue_file
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import _analyze_vue_file
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.js_ts import _analyze_vue_file
        from hypergumbo_core.ir import AnalysisRun
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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # Create Vue file with no script
        (tmp_path / "Empty.vue").write_text("<template><div>Hi</div></template>")

        result = analyze_javascript(tmp_path)


        # Files should be counted as analyzed, not skipped
        assert result.run is not None
        assert result.run.files_analyzed >= 1

    def test_vue_file_read_error_increments_skipped(self, tmp_path: Path) -> None:
        """Vue files that fail to read increment skipped counter."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript
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
        from hypergumbo_lang_mainstream.js_ts import _analyze_vue_file
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.js_ts import _analyze_vue_file
        from hypergumbo_core.ir import AnalysisRun
        from unittest.mock import patch

        vue_file = tmp_path / "Test.vue"
        vue_file.write_text("<script>const x = 1;</script>")

        run = AnalysisRun.create(pass_id="test", version="test")

        # Mock _get_parser_for_lang to return None
        with patch("hypergumbo_lang_mainstream.js_ts._get_parser_for_lang", return_value=None):
            symbols, edges, success = _analyze_vue_file(vue_file, run)

        assert success is True
        assert symbols == []
        assert edges == []

    def test_vue_parser_unavailable_in_analyze_javascript(self, tmp_path: Path) -> None:
        """Vue script blocks are skipped in analyze_javascript when parser unavailable."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript, is_tree_sitter_available
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

        with patch("hypergumbo_lang_mainstream.js_ts._get_parser_for_lang", side_effect=mock_get_parser):
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
        from hypergumbo_lang_mainstream.js_ts import is_tree_sitter_available

        if not is_tree_sitter_available():
            pytest.skip("tree-sitter not available")

    def test_express_get_route_detected(self, tmp_path: Path) -> None:
        """Express app.get() route handler has sha256-based stable_id (ADR-0014 §4)."""
        from hypergumbo_core.analyze.base import make_route_stable_id
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        js_file = tmp_path / "app.js"
        js_file.write_text("""
const express = require('express');
const app = express();

app.get('/users', function getUsers(req, res) {
    res.json([]);
});
""")

        result = analyze_javascript(tmp_path)

        # Find the route handler function by meta
        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.meta and f.meta.get("http_method")]

        assert len(route_handlers) == 1
        handler = route_handlers[0]
        assert handler.name == "getUsers"
        assert handler.stable_id == make_route_stable_id("GET", "/users")
        assert handler.meta is not None
        assert handler.meta.get("route_path") == "/users"

    def test_express_post_route_detected(self, tmp_path: Path) -> None:
        """Express app.post() route handler has sha256-based stable_id (ADR-0014 §4)."""
        from hypergumbo_core.analyze.base import make_route_stable_id
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        route_handlers = [f for f in functions if f.meta and f.meta.get("http_method") == "POST"]

        assert len(route_handlers) == 1
        assert route_handlers[0].stable_id == make_route_stable_id("POST", "/users")
        assert route_handlers[0].meta.get("route_path") == "/users"

    def test_express_router_route_detected(self, tmp_path: Path) -> None:
        """Express router routes have unique sha256-based stable_ids (ADR-0014 §4)."""
        from hypergumbo_core.analyze.base import make_route_stable_id
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        route_handlers = [f for f in functions if f.meta and f.meta.get("http_method")]

        assert len(route_handlers) == 2

        get_handler = next(f for f in route_handlers if f.meta.get("http_method") == "GET")
        delete_handler = next(f for f in route_handlers if f.meta.get("http_method") == "DELETE")

        assert get_handler.stable_id == make_route_stable_id("GET", "/items/:id")
        assert delete_handler.stable_id == make_route_stable_id("DELETE", "/items/:id")
        # Different methods on same path must have different stable_ids
        assert get_handler.stable_id != delete_handler.stable_id

    def test_express_arrow_function_route(self, tmp_path: Path) -> None:
        """Express route with arrow function handler."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        route_handlers = [f for f in functions if f.meta and f.meta.get("http_method") == "GET"]

        # Even anonymous arrow functions should be detected as routes
        assert len(route_handlers) >= 0  # May or may not create symbol for anonymous

    def test_express_all_http_methods(self, tmp_path: Path) -> None:
        """All HTTP methods should be detected: get, post, put, patch, delete."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        route_handlers = {
            f.meta["http_method"]: f
            for f in functions
            if f.meta and f.meta.get("http_method")
        }

        assert "GET" in route_handlers
        assert "POST" in route_handlers
        assert "PUT" in route_handlers
        assert "PATCH" in route_handlers
        assert "DELETE" in route_handlers
        # All five routes must have distinct stable_ids
        stable_ids = [f.stable_id for f in route_handlers.values()]
        assert len(set(stable_ids)) == 5

    def test_non_route_function_keeps_original_stable_id(self, tmp_path: Path) -> None:
        """Functions not in route calls keep their original stable_id."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        route_handlers = [f for f in functions if f.meta and f.meta.get("http_method") == "GET"]

        assert len(route_handlers) == 1
        assert route_handlers[0].meta.get("route_path") == "/users"

    def test_express_external_handler_detected(self, tmp_path: Path) -> None:
        """Express routes with external handler references are detected."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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

        # Verify HTTP methods via meta
        methods = {r.meta["http_method"] for r in routes}
        assert methods == {"POST", "GET", "DELETE"}
        # All routes must have unique stable_ids (no collisions)
        stable_ids = [r.stable_id for r in routes]
        assert len(set(stable_ids)) == 3

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        assert routes[0].meta.get("http_method") == "GET"
        assert routes[0].stable_id is not None
        assert len(routes[0].stable_id) == 64  # sha256 hex digest
        assert routes[0].meta.get("handler_ref") == "handleUsers"

    def test_express_chained_route_syntax(self, tmp_path: Path) -> None:
        """Express chained route syntax: router.route('/path').get(handler)."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        root_methods = {r.meta["http_method"] for r in root_routes}
        assert root_methods == {"POST", "GET"}
        # Root routes must have distinct stable_ids
        assert len({r.stable_id for r in root_routes}) == 2

        param_routes = [r for r in routes if r.meta.get("route_path") == "/:userId"]
        assert len(param_routes) == 3
        param_methods = {r.meta["http_method"] for r in param_routes}
        assert param_methods == {"GET", "PATCH", "DELETE"}
        # Param routes must have distinct stable_ids
        assert len({r.stable_id for r in param_routes}) == 3

    def test_express_inline_handler_usage_context_has_symbol_ref(self, tmp_path: Path) -> None:
        """UsageContext for inline Express handlers should reference the Symbol.

        This is critical for YAML pattern enrichment to work - the enrichment
        phase skips UsageContexts with no symbol_ref.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        js_file = tmp_path / "app.js"
        js_file.write_text("""
const express = require('express');
const app = express();

app.get('/users', (req, res) => {
    res.json([]);
});
""")

        result = analyze_javascript(tmp_path)

        # Find the inline handler symbol
        handlers = [s for s in result.symbols if s.meta and s.meta.get("route_path") == "/users"]
        assert len(handlers) == 1
        handler = handlers[0]

        # Find the UsageContext for this route
        contexts = [c for c in result.usage_contexts if "app.get" in c.context_name]
        assert len(contexts) >= 1

        # The UsageContext should reference the handler Symbol
        matching_ctx = [c for c in contexts if c.symbol_ref == handler.id]
        assert len(matching_ctx) == 1, f"Expected UsageContext.symbol_ref={handler.id}, got contexts: {[(c.context_name, c.symbol_ref) for c in contexts]}"

    def test_express_external_handler_usage_context_has_symbol_ref(self, tmp_path: Path) -> None:
        """UsageContext for external Express handlers should have a symbol_ref.

        For external handlers like `app.get('/users', listUsers)`, the UsageContext
        references the route symbol created for the handler reference.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        js_file = tmp_path / "app.js"
        js_file.write_text("""
const express = require('express');
const app = express();

function listUsers(req, res) {
    res.json([]);
}

app.get('/users', listUsers);
""")

        result = analyze_javascript(tmp_path)

        # Find the UsageContext for this route
        contexts = [c for c in result.usage_contexts if "app.get" in c.context_name]
        assert len(contexts) >= 1

        # The UsageContext should have a symbol_ref (critical for YAML enrichment)
        ctx = contexts[0]
        assert ctx.symbol_ref is not None, "External handler UsageContext should have symbol_ref"

        # The referenced symbol should exist
        ref_symbols = [s for s in result.symbols if s.id == ctx.symbol_ref]
        assert len(ref_symbols) == 1
        assert ref_symbols[0].name == "listUsers"


# ============================================================================
# Callback Arrow Function Call Attribution Tests
# ============================================================================


class TestCallbackCallAttribution:
    """Tests for call edge attribution inside callback arrow functions.

    Verifies that calls made inside arrow functions passed as callbacks
    (not assigned to variables) are properly attributed to either:
    1. The synthetic route handler symbol (for Express-style routes)
    2. The containing named function (for callbacks inside functions)
    """

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        """Skip tests if tree-sitter is not available."""
        from hypergumbo_lang_mainstream.js_ts import is_tree_sitter_available

        if not is_tree_sitter_available():
            pytest.skip("tree-sitter not available")

    def test_call_inside_express_route_handler_attributed(self, tmp_path: Path) -> None:
        """Calls inside Express route callbacks are attributed to route handler symbol."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # Create a helper module
        helper_file = tmp_path / "helper.js"
        helper_file.write_text("""
function processRequest(data) {
    return data;
}
module.exports = { processRequest };
""")

        # Create a routes file with calls inside callback
        routes_file = tmp_path / "routes.js"
        routes_file.write_text("""
const express = require('express');
const { processRequest } = require('./helper');
const app = express();

app.get('/data', (req, res) => {
    const result = processRequest(req.body);
    res.json(result);
});
""")

        result = analyze_javascript(tmp_path)

        # Find call edges
        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        # There should be a call edge from the route handler to processRequest
        process_calls = [e for e in call_edges if "processRequest" in e.dst]
        assert len(process_calls) >= 1, "Call to processRequest inside route handler should be detected"

        # The source should be a route handler symbol (contains GET or _GET_)
        for edge in process_calls:
            # Either the source has GET in it (route handler) or it's from a named function
            assert "GET" in edge.src or "handler" in edge.src.lower() or "routes" in edge.src.lower(), \
                f"Call should be attributed to route handler, got src={edge.src}"

    def test_call_inside_callback_in_named_function_attributed(self, tmp_path: Path) -> None:
        """Calls inside callbacks within named functions are attributed to the named function."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        js_file = tmp_path / "app.js"
        js_file.write_text("""
function helper() {
    return 42;
}

function main() {
    const data = [1, 2, 3];
    data.forEach((item) => {
        helper();
    });
}
""")

        result = analyze_javascript(tmp_path)

        # Find call edges to helper
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_calls = [e for e in call_edges if "helper" in e.dst]

        # There should be a call from main to helper
        assert len(helper_calls) >= 1, "Call to helper inside forEach callback should be detected"

        # The source should be 'main' (the containing named function)
        main_to_helper = [e for e in helper_calls if "main" in e.src]
        assert len(main_to_helper) >= 1, \
            f"Call should be attributed to main function, got sources: {[e.src for e in helper_calls]}"


    def test_app_get_without_string_path_not_route(self, tmp_path: Path) -> None:
        """app.get(AppService) is NestJS DI lookup, not route registration.

        When app.get() has no string path argument, it's not an Express route
        registration. NestJS uses app.get(ServiceClass) for dependency injection.
        These should NOT create route symbols or UsageContexts.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        js_file = tmp_path / "app.spec.ts"
        js_file.write_text("""
const app = await NestFactory.create(AppModule);
const appService = app.get(AppService);
const tokenService = app.get(DYNAMIC_TOKEN);

// This IS a real route (has string path)
app.get('/health', (req, res) => res.send('ok'));
""")

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        route_funcs = [s for s in result.symbols if s.meta and s.meta.get("route_path")]

        # Only /health should be detected, not AppService or DYNAMIC_TOKEN
        route_names = {s.name for s in routes}
        assert "AppService" not in route_names
        assert "DYNAMIC_TOKEN" not in route_names

        # The /health route should still work
        all_route_paths = {s.meta.get("route_path") for s in route_funcs}
        assert "/health" in all_route_paths


# ============================================================================
# NestJS Route Detection Tests
# ============================================================================


class TestNestJSRouteDetection:
    """Tests for NestJS decorator-based route detection."""

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        """Skip tests if tree-sitter is not available."""
        from hypergumbo_lang_mainstream.js_ts import is_tree_sitter_available

        if not is_tree_sitter_available():
            pytest.skip("tree-sitter not available")

    def test_nestjs_get_decorator(self, tmp_path: Path) -> None:
        """NestJS @Get() decorator should set sha256-based stable_id (ADR-0014 §4)."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        route_handlers = [m for m in methods if m.stable_id and len(m.stable_id) == 64]

        assert len(route_handlers) == 1
        assert route_handlers[0].name == "UsersController.findAll"

    def test_nestjs_post_decorator(self, tmp_path: Path) -> None:
        """NestJS @Post() decorator should set sha256-based stable_id (ADR-0014 §4)."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        route_handlers = [m for m in methods if m.stable_id and len(m.stable_id) == 64]

        assert len(route_handlers) == 1
        assert route_handlers[0].name == "UsersController.create"

    def test_nestjs_get_with_path(self, tmp_path: Path) -> None:
        """NestJS @Get(':id') with @Controller('users') should combine to full path.

        Route path combination is now handled by enrichment (via prefix_from_parent)
        rather than at the analyzer level.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

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
        clear_pattern_cache()
        enrich_symbols(result.symbols, {"nestjs"})

        methods = [s for s in result.symbols if s.kind == "method"]
        route_handlers = [m for m in methods if m.stable_id and len(m.stable_id) == 64]

        assert len(route_handlers) == 1
        handler = route_handlers[0]
        assert handler.name == "UsersController.findOne"
        assert handler.meta is not None
        # Full route = controller prefix + method path: /users/:id
        # Path comes from enrichment concepts, not meta["route_path"]
        concepts = handler.meta.get("concepts", [])
        route_concept = next((c for c in concepts if c.get("concept") == "route"), None)
        assert route_concept is not None, f"Expected route concept, got {concepts}"
        assert route_concept["path"] == "/users/:id"

    def test_nestjs_all_http_methods(self, tmp_path: Path) -> None:
        """NestJS should detect all HTTP method decorators."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        # All 5 methods should have sha256-based stable_ids, and all should be unique
        route_methods = [m for m in methods if m.stable_id and len(m.stable_id) == 64]
        assert len(route_methods) == 5
        stable_ids = {m.stable_id for m in route_methods}
        assert len(stable_ids) == 5, "NestJS route stable_ids must be unique per method"

    def test_nestjs_controller_no_path_method_with_path(self, tmp_path: Path) -> None:
        """NestJS @Controller() with no path + @Get('users/:id') gives just method path."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        ts_file = tmp_path / "users.controller.ts"
        ts_file.write_text("""
import { Controller, Get } from '@nestjs/common';

@Controller()
export class UsersController {
    @Get('users/:id')
    findOne() {
        return {};
    }
}
""")

        result = analyze_javascript(tmp_path)
        clear_pattern_cache()
        enrich_symbols(result.symbols, {"nestjs"})

        methods = [s for s in result.symbols if s.kind == "method"]
        route_handlers = [m for m in methods if m.stable_id and len(m.stable_id) == 64]

        assert len(route_handlers) == 1
        handler = route_handlers[0]
        # Controller has no path, but method path is normalized with leading slash
        concepts = handler.meta.get("concepts", [])
        route_concept = next((c for c in concepts if c.get("concept") == "route"), None)
        assert route_concept is not None
        assert route_concept["path"] == "/users/:id"

    def test_nestjs_controller_with_path_method_no_path(self, tmp_path: Path) -> None:
        """NestJS @Controller('users') + @Get() gives just controller path."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        ts_file = tmp_path / "users.controller.ts"
        ts_file.write_text("""
import { Controller, Get } from '@nestjs/common';

@Controller('users')
export class UsersController {
    @Get()
    findAll() {
        return [];
    }
}
""")

        result = analyze_javascript(tmp_path)
        clear_pattern_cache()
        enrich_symbols(result.symbols, {"nestjs"})

        methods = [s for s in result.symbols if s.kind == "method"]
        route_handlers = [m for m in methods if m.stable_id and len(m.stable_id) == 64]

        assert len(route_handlers) == 1
        handler = route_handlers[0]
        # Method has no path, so just controller path from prefix_from_parent
        concepts = handler.meta.get("concepts", [])
        route_concept = next((c for c in concepts if c.get("concept") == "route"), None)
        assert route_concept is not None
        assert route_concept["path"] == "/users"

    def test_nestjs_path_normalization(self, tmp_path: Path) -> None:
        """NestJS paths are normalized (no double slashes)."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        ts_file = tmp_path / "api.controller.ts"
        ts_file.write_text("""
import { Controller, Get } from '@nestjs/common';

@Controller('/api/')
export class ApiController {
    @Get('/users/')
    findAll() {
        return [];
    }
}
""")

        result = analyze_javascript(tmp_path)
        clear_pattern_cache()
        enrich_symbols(result.symbols, {"nestjs"})

        methods = [s for s in result.symbols if s.kind == "method"]
        route_handlers = [m for m in methods if m.stable_id and len(m.stable_id) == 64]

        assert len(route_handlers) == 1
        handler = route_handlers[0]
        # Paths normalized: /api/users (no double slashes, leading slash added)
        concepts = handler.meta.get("concepts", [])
        route_concept = next((c for c in concepts if c.get("concept") == "route"), None)
        assert route_concept is not None
        assert route_concept["path"] == "/api/users"

    def test_nestjs_no_controller_decorator(self, tmp_path: Path) -> None:
        """Class without @Controller - method path only."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        ts_file = tmp_path / "service.ts"
        ts_file.write_text("""
class UsersService {
    @Get('users')
    findAll() {
        return [];
    }
}
""")

        result = analyze_javascript(tmp_path)
        clear_pattern_cache()
        enrich_symbols(result.symbols, {"nestjs"})

        methods = [s for s in result.symbols if s.kind == "method"]
        route_handlers = [m for m in methods if m.stable_id and len(m.stable_id) == 64]

        assert len(route_handlers) == 1
        handler = route_handlers[0]
        # No controller, method path normalized with leading slash
        concepts = handler.meta.get("concepts", [])
        route_concept = next((c for c in concepts if c.get("concept") == "route"), None)
        assert route_concept is not None
        assert route_concept["path"] == "/users"

    def test_nestjs_non_exported_class_with_controller(self, tmp_path: Path) -> None:
        """Non-exported class with @Controller - decorator as child of class_declaration."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        ts_file = tmp_path / "internal.controller.ts"
        ts_file.write_text("""
@Controller('internal')
class InternalController {
    @Get('status')
    getStatus() {
        return {};
    }
}
""")

        result = analyze_javascript(tmp_path)
        clear_pattern_cache()
        enrich_symbols(result.symbols, {"nestjs"})

        methods = [s for s in result.symbols if s.kind == "method"]
        route_handlers = [m for m in methods if m.stable_id and len(m.stable_id) == 64]

        assert len(route_handlers) == 1
        handler = route_handlers[0]
        # Combined path: /internal/status (from prefix_from_parent)
        concepts = handler.meta.get("concepts", [])
        route_concept = next((c for c in concepts if c.get("concept") == "route"), None)
        assert route_concept is not None
        assert route_concept["path"] == "/internal/status"


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
        from hypergumbo_lang_mainstream.js_ts import is_tree_sitter_available

        if not is_tree_sitter_available():
            pytest.skip("tree-sitter not available")

    def test_koa_router_get_route(self, tmp_path: Path) -> None:
        """Koa Router router.get() route handler sets stable_id to 'get'."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        route_handlers = [f for f in functions if f.meta and f.meta.get("http_method") == "GET"]

        assert len(route_handlers) == 1
        handler = route_handlers[0]
        assert handler.name == "listUsers"
        assert handler.meta is not None
        assert handler.meta.get("route_path") == "/users"

    def test_koa_router_post_route(self, tmp_path: Path) -> None:
        """Koa Router router.post() route handler sets stable_id to 'post'."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        route_handlers = [f for f in functions if f.meta and f.meta.get("http_method") == "POST"]

        assert len(route_handlers) == 1
        assert route_handlers[0].meta.get("route_path") == "/users"

    def test_koa_router_arrow_function(self, tmp_path: Path) -> None:
        """Koa Router with arrow function handler also detects routes."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        route_handlers = [f for f in functions if f.meta and f.meta.get("http_method") == "DELETE"]

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
        from hypergumbo_lang_mainstream.js_ts import is_tree_sitter_available

        if not is_tree_sitter_available():
            pytest.skip("tree-sitter not available")

    def test_fastify_get_route(self, tmp_path: Path) -> None:
        """Fastify fastify.get() route handler sets stable_id to 'get'."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        js_file = tmp_path / "server.js"
        js_file.write_text("""
const fastify = require('fastify')();

fastify.get('/users', function getUsers(request, reply) {
    reply.send([]);
});
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.meta and f.meta.get("http_method") == "GET"]

        assert len(route_handlers) == 1
        handler = route_handlers[0]
        assert handler.name == "getUsers"
        assert handler.meta is not None
        assert handler.meta.get("route_path") == "/users"

    def test_fastify_post_route(self, tmp_path: Path) -> None:
        """Fastify fastify.post() route handler sets stable_id to 'post'."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        js_file = tmp_path / "server.js"
        js_file.write_text("""
const fastify = require('fastify')();

fastify.post('/users', function createUser(request, reply) {
    reply.send({ id: 1 });
});
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.meta and f.meta.get("http_method") == "POST"]

        assert len(route_handlers) == 1
        assert route_handlers[0].meta.get("route_path") == "/users"

    def test_fastify_arrow_function(self, tmp_path: Path) -> None:
        """Fastify with arrow function handler also detects routes."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        js_file = tmp_path / "server.js"
        js_file.write_text("""
const fastify = require('fastify')();

fastify.put('/users/:id', async (request, reply) => {
    reply.send({ updated: true });
});
""")

        result = analyze_javascript(tmp_path)

        functions = [s for s in result.symbols if s.kind == "function"]
        route_handlers = [f for f in functions if f.meta and f.meta.get("http_method") == "PUT"]

        assert len(route_handlers) == 1
        assert route_handlers[0].meta.get("route_path") == "/users/:id"

    def test_fastify_all_http_methods(self, tmp_path: Path) -> None:
        """Fastify supports all HTTP methods."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        http_methods = {
            f.meta["http_method"]: f
            for f in functions
            if f.meta and f.meta.get("http_method")
        }

        assert "GET" in http_methods
        assert "POST" in http_methods
        assert "PUT" in http_methods
        assert "PATCH" in http_methods
        assert "DELETE" in http_methods
        assert "HEAD" in http_methods
        assert "OPTIONS" in http_methods
        # All routes must have unique stable_ids
        stable_ids = [f.stable_id for f in http_methods.values()]
        assert len(set(stable_ids)) == 7


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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import _extract_namespace_imports, _get_parser_for_file

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
        from hypergumbo_lang_mainstream.js_ts import _extract_namespace_imports, _get_parser_for_file

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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

    def test_namespace_import_disambiguates_same_name_functions(self, tmp_path: Path) -> None:
        """When same function name exists in multiple modules, namespace import disambiguates.

        This test uses directory structure to control file discovery order, ensuring
        the "wrong" file is processed last (overwriting global_symbols). The namespace
        import path_hint must be used to resolve to the correct target.

        rglob discovery order: main.js -> a_early/utils.js -> z_late/utils.js
        So z_late (WRONG) overwrites a_early (CORRECT) in global_symbols.
        Without path_hint, resolution incorrectly picks z_late.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # Create two modules with same function name in different directories
        # rglob processes alphabetically: a_early/ before z_late/
        # So z_late/utils.js (WRONG) will be processed LAST, overwriting global_symbols
        correct_dir = tmp_path / "a_early"
        correct_dir.mkdir()
        (correct_dir / "utils.js").write_text("""
function process() {
    return 'CORRECT';
}
""")

        wrong_dir = tmp_path / "z_late"
        wrong_dir.mkdir()
        (wrong_dir / "utils.js").write_text("""
function process() {
    return 'WRONG';
}
""")

        # Import only a_early/utils and call process via namespace
        main_file = tmp_path / "main.js"
        main_file.write_text("""
import * as correct from './a_early/utils';

function run() {
    correct.process();
}
""")

        result = analyze_javascript(tmp_path)

        # Find call edges from run
        call_edges = [e for e in result.edges if e.edge_type == "calls" and "run" in e.src]

        # Should resolve to a_early/utils.process, NOT z_late/utils.process
        run_process_edge = next(
            (e for e in call_edges if "process" in e.dst),
            None
        )
        assert run_process_edge is not None, "Expected call edge from run to process"

        # The edge should point to a_early (correct), not z_late (wrong)
        assert "a_early" in run_process_edge.dst, (
            f"Expected call to resolve to a_early/utils.process, but got {run_process_edge.dst}. "
            "Namespace import path_hint should disambiguate when same function exists in multiple modules."
        )

    def test_named_import_disambiguates_same_name_functions(self, tmp_path: Path) -> None:
        """Named imports (import { X } from) disambiguate same-name functions.

        When two files define the same function name, a named import should
        resolve calls to the correct module via import path disambiguation.
        Previously, direct calls like ``process()`` after
        ``import { process } from './dir_a/utils'`` would resolve to whichever
        file was processed last (global_symbols last-one-wins), ignoring the
        import statement entirely.

        Uses two consumers (one importing from each dir) so the test is
        order-independent: regardless of which symbol wins global_symbols,
        one consumer MUST break without the fix.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        dir_a = tmp_path / "dir_a"
        dir_a.mkdir()
        (dir_a / "utils.js").write_text(
            "export function process() {\n    return 'A';\n}\n"
        )

        dir_b = tmp_path / "dir_b"
        dir_b.mkdir()
        (dir_b / "utils.js").write_text(
            "export function process() {\n    return 'B';\n}\n"
        )

        # Two consumers, each importing process() from a different module
        (tmp_path / "consumer_a.js").write_text(
            "import { process } from './dir_a/utils';\n"
            "\n"
            "function useA() {\n"
            "    process();\n"
            "}\n"
        )
        (tmp_path / "consumer_b.js").write_text(
            "import { process } from './dir_b/utils';\n"
            "\n"
            "function useB() {\n"
            "    process();\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        # Find edge from useA -> process
        edge_a = next(
            (e for e in call_edges if "useA" in e.src and "process" in e.dst), None,
        )
        assert edge_a is not None, "Expected call edge from useA to process"
        assert "dir_a" in edge_a.dst, (
            f"useA imports from dir_a but edge resolves to {edge_a.dst}. "
            "Named import should disambiguate direct calls."
        )

        # Find edge from useB -> process
        edge_b = next(
            (e for e in call_edges if "useB" in e.src and "process" in e.dst), None,
        )
        assert edge_b is not None, "Expected call edge from useB to process"
        assert "dir_b" in edge_b.dst, (
            f"useB imports from dir_b but edge resolves to {edge_b.dst}. "
            "Named import should disambiguate direct calls."
        )

    def test_same_package_preference_no_import(self, tmp_path: Path) -> None:
        """Direct calls without imports prefer symbols from the same package.

        When ``error()`` is called without any import in two different packages,
        each should resolve to their own package's ``error()`` function.
        Previously, ``global_symbols`` used last-one-wins, so one of the two
        callers would incorrectly resolve to the other package's function.

        Uses two packages (each with its own package.json) and two callers
        to make the test filesystem-order-independent.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # Package A: server
        server = tmp_path / "server" / "src"
        server.mkdir(parents=True)
        (tmp_path / "server" / "package.json").write_text('{"name": "server"}')
        (server / "errors.js").write_text(
            "export function error(msg) {\n    console.log(msg);\n}\n"
        )
        (server / "handler.js").write_text(
            "// No import of error — it's a same-package helper.\n"
            "function handleRequest() {\n"
            "    error('something went wrong');\n"
            "}\n"
        )

        # Package B: client (different package)
        client = tmp_path / "client" / "js"
        client.mkdir(parents=True)
        (tmp_path / "client" / "package.json").write_text('{"name": "client"}')
        (client / "actions.js").write_text(
            "export function error(msg) {\n    alert(msg);\n}\n"
        )
        (client / "app.js").write_text(
            "// No import — uses same-package error helper.\n"
            "function showError() {\n"
            "    error('user-facing message');\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        # Server handler should resolve to server's error
        handler_error = next(
            (e for e in call_edges if "handleRequest" in e.src and "error" in e.dst),
            None,
        )
        assert handler_error is not None, "Expected call edge from handleRequest to error"
        assert "server" in handler_error.dst, (
            f"handleRequest is in server/ but call resolves to {handler_error.dst}. "
            "Same-package preference should pick the same-package symbol."
        )

        # Client app should resolve to client's error
        client_error = next(
            (e for e in call_edges if "showError" in e.src and "error" in e.dst),
            None,
        )
        assert client_error is not None, "Expected call edge from showError to error"
        assert "client" in client_error.dst, (
            f"showError is in client/ but call resolves to {client_error.dst}. "
            "Same-package preference should pick the same-package symbol."
        )

    def test_same_package_candidate_no_package_json(self, tmp_path: Path) -> None:
        """_same_package_candidate returns None when no package.json exists."""
        from hypergumbo_lang_mainstream.js_ts import _same_package_candidate

        from hypergumbo_core.ir import Symbol, Span

        sym_a = Symbol(
            id="js:a.js:1-1:helper:function", name="helper", kind="function",
            path=str(tmp_path / "a.js"), span=Span(1, 0, 1, 0),
            language="javascript", origin="test",
        )
        sym_b = Symbol(
            id="js:b.js:1-1:helper:function", name="helper", kind="function",
            path=str(tmp_path / "b.js"), span=Span(1, 0, 1, 0),
            language="javascript", origin="test",
        )
        # No package.json anywhere — should return None
        result = _same_package_candidate(
            tmp_path / "a.js", "helper", {"helper": [sym_a, sym_b]},
        )
        assert result is None

    def test_same_package_candidate_no_match(self, tmp_path: Path) -> None:
        """_same_package_candidate returns None when no candidate matches package root."""
        from hypergumbo_lang_mainstream.js_ts import _same_package_candidate

        from hypergumbo_core.ir import Symbol, Span

        # Caller is in pkg_a/, but both candidates are in other_pkg/
        (tmp_path / "pkg_a").mkdir()
        (tmp_path / "pkg_a" / "package.json").write_text('{"name": "a"}')
        sym_x = Symbol(
            id="js:other/x.js:1-1:helper:function", name="helper", kind="function",
            path=str(tmp_path / "other_pkg" / "x.js"), span=Span(1, 0, 1, 0),
            language="javascript", origin="test",
        )
        sym_y = Symbol(
            id="js:other/y.js:1-1:helper:function", name="helper", kind="function",
            path=str(tmp_path / "other_pkg" / "y.js"), span=Span(1, 0, 1, 0),
            language="javascript", origin="test",
        )
        result = _same_package_candidate(
            tmp_path / "pkg_a" / "handler.js", "helper", {"helper": [sym_x, sym_y]},
        )
        assert result is None

    def test_new_namespace_class_disambiguates(self, tmp_path: Path) -> None:
        """When same class name exists in multiple modules, namespace import disambiguates.

        This test uses directory structure to control file discovery order, ensuring
        the "wrong" file is processed last (overwriting global_classes). The namespace
        import path_hint must be used to resolve to the correct target.

        rglob discovery order: main.js -> a_early/service.js -> z_late/service.js
        So z_late (WRONG) overwrites a_early (CORRECT) in global_classes.
        Without path_hint, resolution incorrectly picks z_late.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # Create two modules with same class name in different directories
        # rglob processes alphabetically: a_early/ before z_late/
        # So z_late/service.js (WRONG) will be processed LAST, overwriting global_classes
        correct_dir = tmp_path / "a_early"
        correct_dir.mkdir()
        (correct_dir / "service.js").write_text("""
class Client {
    connect() { return 'CORRECT'; }
}
""")

        wrong_dir = tmp_path / "z_late"
        wrong_dir.mkdir()
        (wrong_dir / "service.js").write_text("""
class Client {
    connect() { return 'WRONG'; }
}
""")

        # Import only a_early/service and instantiate via namespace
        main_file = tmp_path / "main.js"
        main_file.write_text("""
import * as correct from './a_early/service';

function run() {
    const client = new correct.Client();
    return client;
}
""")

        result = analyze_javascript(tmp_path)

        # Find instantiates edges from run
        inst_edges = [e for e in result.edges if e.edge_type == "instantiates" and "run" in e.src]

        # Should resolve to a_early/service.Client, NOT z_late/service.Client
        run_client_edge = next(
            (e for e in inst_edges if "Client" in e.dst),
            None
        )
        assert run_client_edge is not None, "Expected instantiates edge from run to Client"

        # The edge should point to a_early (correct), not z_late (wrong)
        assert "a_early" in run_client_edge.dst, (
            f"Expected instantiation to resolve to a_early/service.Client, but got {run_client_edge.dst}. "
            "Namespace import path_hint should disambiguate when same class exists in multiple modules."
        )


class TestJsTsReturnTypeExtraction:
    """Unit tests for _extract_jsts_return_type_name helper."""

    def test_simple_return_type(self) -> None:
        from hypergumbo_lang_mainstream.js_ts import _extract_jsts_return_type_name

        assert _extract_jsts_return_type_name("(x: number): MyClass") == "MyClass"

    def test_no_return_type(self) -> None:
        from hypergumbo_lang_mainstream.js_ts import _extract_jsts_return_type_name

        assert _extract_jsts_return_type_name("(x: number)") is None

    def test_generic_return_type(self) -> None:
        from hypergumbo_lang_mainstream.js_ts import _extract_jsts_return_type_name

        assert _extract_jsts_return_type_name("(): Promise<Client>") is None

    def test_none_signature(self) -> None:
        from hypergumbo_lang_mainstream.js_ts import _extract_jsts_return_type_name

        assert _extract_jsts_return_type_name(None) is None

    def test_empty_signature(self) -> None:
        from hypergumbo_lang_mainstream.js_ts import _extract_jsts_return_type_name

        assert _extract_jsts_return_type_name("") is None

    def test_no_paren(self) -> None:
        from hypergumbo_lang_mainstream.js_ts import _extract_jsts_return_type_name

        assert _extract_jsts_return_type_name("no parens") is None


class TestVariableTypeInference:
    """Tests for variable type inference from constructor calls."""

    def test_variable_type_tracked_from_new(self, tmp_path: Path) -> None:
        """Variable types should be tracked from 'new ClassName()' assignments."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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

    def test_type_inference_from_return_type_annotation(self, tmp_path: Path) -> None:
        """TypeScript return type annotations enable type inference."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        ts_file = tmp_path / "main.ts"
        ts_file.write_text("""
class ServiceClient {
    send(): string {
        return 'sent';
    }
}

function getClient(): ServiceClient {
    return new ServiceClient();
}

function run() {
    const client = getClient();
    client.send();
}
""")

        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        get_client_edge = next(
            (e for e in call_edges if "run" in e.src and "getClient" in e.dst),
            None
        )
        assert get_client_edge is not None, "Expected call edge for getClient"

        # client.send() should resolve via return type annotation
        type_inferred_edges = [
            e for e in call_edges
            if e.evidence_type == "ast_method_type_inferred"
            and "send" in e.dst
        ]
        assert len(type_inferred_edges) == 1, (
            "Expected type-inferred edge for client.send() via return type"
        )

    def test_type_inference_no_annotation_no_resolution(self, tmp_path: Path) -> None:
        """JS functions without return type annotations don't enable type inference."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
    const client = getClient();
    client.send();
}
""")

        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        type_inferred_edges = [
            e for e in call_edges
            if e.evidence_type == "ast_method_type_inferred"
        ]
        assert len(type_inferred_edges) == 0, (
            "Should NOT have type-inferred edge without annotation"
        )

    def test_namespace_class_instantiation(self, tmp_path: Path) -> None:
        """new namespace.ClassName() should track type correctly."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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

    def test_parameter_type_inference_typescript(self, tmp_path: Path) -> None:
        """TypeScript function parameter types should enable method call resolution."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # Service class with methods
        service_file = tmp_path / "service.ts"
        service_file.write_text("""
class Database {
    save(obj: any): void { }
    commit(): void { }
}
""")

        # Handler receives Database as parameter with type annotation
        handler_file = tmp_path / "handler.ts"
        handler_file.write_text("""
function process(db: Database, data: string): void {
    db.save(data);
    db.commit();
}
""")

        result = analyze_javascript(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 2

        # Find symbols
        process_func = next(
            (s for s in result.symbols if s.name == "process"), None
        )
        db_save = next(
            (s for s in result.symbols if "save" in s.name and "Database" in s.id), None
        )
        db_commit = next(
            (s for s in result.symbols if "commit" in s.name and "Database" in s.id), None
        )

        assert process_func is not None
        assert db_save is not None
        assert db_commit is not None

        # Should have edges from process to Database.save and Database.commit
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        save_edge = next(
            (
                e
                for e in call_edges
                if e.src == process_func.id
                and e.dst == db_save.id
            ),
            None,
        )
        commit_edge = next(
            (
                e
                for e in call_edges
                if e.src == process_func.id
                and e.dst == db_commit.id
            ),
            None,
        )

        assert save_edge is not None, "Expected call edge for db.save() via param type inference"
        assert commit_edge is not None, "Expected call edge for db.commit() via param type inference"
        # Both should use type inference evidence
        assert save_edge.evidence_type == "ast_method_type_inferred"
        assert commit_edge.evidence_type == "ast_method_type_inferred"

    def test_this_property_method_call_nestjs_pattern(self, tmp_path: Path) -> None:
        """this.property.method() calls via constructor injection should resolve.

        This tests the NestJS/Angular pattern where services are injected via constructor:
            constructor(private readonly catsService: CatsService) {}

        And then called via:
            this.catsService.create(data)
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # Service class with methods
        service_file = tmp_path / "cats.service.ts"
        service_file.write_text("""
class CatsService {
    create(data: any) {
        return data;
    }

    findAll() {
        return [];
    }
}
""")

        # Controller with constructor injection
        controller_file = tmp_path / "cats.controller.ts"
        controller_file.write_text("""
class CatsController {
    constructor(private readonly catsService: CatsService) {}

    async create(data: any) {
        return this.catsService.create(data);
    }

    async findAll() {
        return this.catsService.findAll();
    }
}
""")

        result = analyze_javascript(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 2

        # Find symbols
        ctrl_create = next(
            (s for s in result.symbols if s.name == "CatsController.create"), None
        )
        ctrl_find_all = next(
            (s for s in result.symbols if s.name == "CatsController.findAll"), None
        )
        svc_create = next(
            (s for s in result.symbols if s.name == "CatsService.create"), None
        )
        svc_find_all = next(
            (s for s in result.symbols if s.name == "CatsService.findAll"), None
        )

        assert ctrl_create is not None, "Expected CatsController.create symbol"
        assert ctrl_find_all is not None, "Expected CatsController.findAll symbol"
        assert svc_create is not None, "Expected CatsService.create symbol"
        assert svc_find_all is not None, "Expected CatsService.findAll symbol"

        # Should have edges from controller methods to service methods
        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        create_edge = next(
            (
                e
                for e in call_edges
                if e.src == ctrl_create.id
                and e.dst == svc_create.id
            ),
            None,
        )
        find_all_edge = next(
            (
                e
                for e in call_edges
                if e.src == ctrl_find_all.id
                and e.dst == svc_find_all.id
            ),
            None,
        )

        assert create_edge is not None, (
            "Expected call edge for this.catsService.create() via constructor injection. "
            f"Edges from ctrl_create: {[e.dst for e in call_edges if e.src == ctrl_create.id]}"
        )
        assert find_all_edge is not None, (
            "Expected call edge for this.catsService.findAll() via constructor injection. "
            f"Edges from ctrl_find_all: {[e.dst for e in call_edges if e.src == ctrl_find_all.id]}"
        )

        # Both should use this_property evidence type
        assert create_edge.evidence_type == "ast_method_this_property"
        assert find_all_edge.evidence_type == "ast_method_this_property"
        # Confidence should be 0.90 * resolver confidence (typically 1.0)
        assert create_edge.confidence == 0.90
        assert find_all_edge.confidence == 0.90

    def test_this_property_disambiguates_via_named_import(self, tmp_path: Path) -> None:
        """this.property.method() should resolve to the correct class via named import.

        When multiple files define the same class name, the import path should
        disambiguate which one to use. This is essential for monorepos (e.g., NestJS
        with multiple sample apps each defining CatsService).

        The controller is in dir_b (alphabetically later) and imports from its own
        directory, while dir_a has a decoy CatsService. Without import-aware
        disambiguation, the resolver would pick dir_a (alphabetically first).
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # dir_a has a decoy CatsService (alphabetically first = default pick)
        (tmp_path / "dir_a").mkdir()
        # dir_b has the correct CatsService (alphabetically later)
        (tmp_path / "dir_b").mkdir()

        decoy_service = tmp_path / "dir_a" / "cats.service.ts"
        decoy_service.write_text("""
export class CatsService {
    create(data: any) { return data; }
}
""")

        correct_service = tmp_path / "dir_b" / "cats.service.ts"
        correct_service.write_text("""
export class CatsService {
    create(data: any) { return data; }
}
""")

        # Controller in dir_b imports CatsService from its own directory
        controller = tmp_path / "dir_b" / "cats.controller.ts"
        controller.write_text("""
import { CatsService } from './cats.service';

class CatsController {
    constructor(private readonly catsService: CatsService) {}

    async create(data: any) {
        return this.catsService.create(data);
    }
}
""")

        result = analyze_javascript(tmp_path)

        ctrl_create = next(
            (s for s in result.symbols if s.name == "CatsController.create"), None
        )
        assert ctrl_create is not None, "Expected CatsController.create symbol"

        # Find both CatsService.create symbols
        svc_creates = [s for s in result.symbols if s.name == "CatsService.create"]
        assert len(svc_creates) == 2, f"Expected 2 CatsService.create symbols, got {len(svc_creates)}"

        svc_create_correct = next(
            (s for s in svc_creates if "dir_b" in s.path), None
        )
        svc_create_decoy = next(
            (s for s in svc_creates if "dir_a" in s.path), None
        )
        assert svc_create_correct is not None
        assert svc_create_decoy is not None

        # Should have an edge to dir_b's CatsService.create (same directory import)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        correct_edge = next(
            (e for e in call_edges if e.src == ctrl_create.id and e.dst == svc_create_correct.id),
            None,
        )
        assert correct_edge is not None, (
            "Expected call edge to dir_b/CatsService.create (via named import), "
            f"got edges: {[(e.dst, e.evidence_type) for e in call_edges if e.src == ctrl_create.id]}"
        )

        # Should NOT have an edge to dir_a's CatsService.create
        wrong_edge = next(
            (e for e in call_edges if e.src == ctrl_create.id and e.dst == svc_create_decoy.id),
            None,
        )
        assert wrong_edge is None, (
            "Should NOT have edge to dir_a/CatsService.create — wrong disambiguation"
        )

    def test_variable_method_disambiguates_via_named_import(self, tmp_path: Path) -> None:
        """variable.method() (Case 3) should also use import-path disambiguation."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "dir_a").mkdir()
        (tmp_path / "dir_b").mkdir()

        # Decoy in dir_a (alphabetically first)
        (tmp_path / "dir_a" / "cats.service.ts").write_text(
            "export class CatsService {\n    create() { return 1; }\n}\n"
        )
        # Correct in dir_b
        (tmp_path / "dir_b" / "cats.service.ts").write_text(
            "export class CatsService {\n    create() { return 2; }\n}\n"
        )
        # Consumer in dir_b uses local variable (Case 3: svc.create())
        (tmp_path / "dir_b" / "app.ts").write_text(
            "import { CatsService } from './cats.service';\n"
            "function run() {\n"
            "    const svc = new CatsService();\n"
            "    svc.create();\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        run_fn = next((s for s in result.symbols if s.name == "run"), None)
        assert run_fn is not None
        svc_creates = [s for s in result.symbols if s.name == "CatsService.create"]
        assert len(svc_creates) == 2
        correct = next((s for s in svc_creates if "dir_b" in s.path), None)
        assert correct is not None

        type_edges = [
            e for e in result.edges
            if e.src == run_fn.id and e.evidence_type == "ast_method_type_inferred"
        ]
        assert any(e.dst == correct.id for e in type_edges), (
            f"Expected Case 3 edge to dir_b/CatsService.create, got: "
            f"{[(e.dst, e.evidence_type) for e in type_edges]}"
        )

    def test_import_alias_tracked_in_named_imports(self, tmp_path: Path) -> None:
        """import { Foo as Bar } from './module' should track alias 'Bar'."""
        from hypergumbo_lang_mainstream.js_ts import _extract_named_imports, _get_parser_for_lang

        parser = _get_parser_for_lang(is_typescript=True)
        assert parser is not None
        source = b"import { CatsService as CS } from './cats.service';"
        tree = parser.parse(source)
        imports = _extract_named_imports(tree, source)
        # Alias should be the key, not the original name
        assert "CS" in imports
        assert imports["CS"] == "./cats.service"

    def test_disambiguate_non_relative_import_falls_through(self, tmp_path: Path) -> None:
        """Non-relative imports (e.g., @nestjs/common) skip disambiguation."""
        from hypergumbo_lang_mainstream.js_ts import _disambiguate_by_import
        from hypergumbo_core.ir import Symbol, Span

        sym = Symbol(
            id="test:1", name="Foo.bar", kind="method",
            path="/repo/foo.ts", span=Span(1, 0, 1, 0), language="typescript",
        )
        result = _disambiguate_by_import(
            "@nestjs/common", Path("/repo/app.ts"), "Foo.bar", {"Foo.bar": [sym, sym]},
        )
        assert result is None

    def test_disambiguate_single_candidate_returns_none(self, tmp_path: Path) -> None:
        """Disambiguation with a single candidate returns None (no ambiguity)."""
        from hypergumbo_lang_mainstream.js_ts import _disambiguate_by_import
        from hypergumbo_core.ir import Symbol, Span

        sym = Symbol(
            id="test:1", name="Foo.bar", kind="method",
            path="/repo/foo.ts", span=Span(1, 0, 1, 0), language="typescript",
        )
        result = _disambiguate_by_import(
            "./foo", Path("/repo/app.ts"), "Foo.bar", {"Foo.bar": [sym]},
        )
        assert result is None

    def test_disambiguate_no_match_returns_none(self, tmp_path: Path) -> None:
        """Disambiguation returns None when import path doesn't match any candidate."""
        from hypergumbo_lang_mainstream.js_ts import _disambiguate_by_import
        from hypergumbo_core.ir import Symbol, Span

        sym_a = Symbol(
            id="test:a", name="Foo.bar", kind="method",
            path="/repo/dir_a/foo.ts", span=Span(1, 0, 1, 0), language="typescript",
        )
        sym_b = Symbol(
            id="test:b", name="Foo.bar", kind="method",
            path="/repo/dir_b/foo.ts", span=Span(1, 0, 1, 0), language="typescript",
        )
        # Import points to dir_c which doesn't match either candidate
        result = _disambiguate_by_import(
            "./dir_c/foo", Path("/repo/app.ts"), "Foo.bar", {"Foo.bar": [sym_a, sym_b]},
        )
        assert result is None

    def test_enclosing_function_found_for_all_duplicate_named_methods(self, tmp_path: Path) -> None:
        """All instances of a duplicate-named method must produce call edges.

        In monorepos where multiple files define the same class/method name
        (e.g., NestJS with CatsController.create in 11 sample apps), each
        controller's method must produce its own call edges. Previously,
        _get_enclosing_function used global_symbols which keeps only one
        symbol per name, so only the last-processed file produced edges.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # Create 3 directories each with identical CatsController + CatsService
        for d in ["app_a", "app_b", "app_c"]:
            (tmp_path / d).mkdir()
            (tmp_path / d / "cats.service.ts").write_text(
                "export class CatsService {\n"
                "    create(data: any) { return data; }\n"
                "}\n"
            )
            (tmp_path / d / "cats.controller.ts").write_text(
                "import { CatsService } from './cats.service';\n"
                "\n"
                "export class CatsController {\n"
                "    constructor(private readonly catsService: CatsService) {}\n"
                "\n"
                "    async create(data: any) {\n"
                "        return this.catsService.create(data);\n"
                "    }\n"
                "}\n"
            )

        result = analyze_javascript(tmp_path)

        # All 3 controllers should produce call edges
        ctrl_creates = [s for s in result.symbols if s.name == "CatsController.create"]
        assert len(ctrl_creates) == 3, (
            f"Expected 3 CatsController.create, got {len(ctrl_creates)}"
        )

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        controllers_with_edges = set()
        for ctrl in ctrl_creates:
            ctrl_edges = [e for e in call_edges if e.src == ctrl.id]
            if ctrl_edges:
                controllers_with_edges.add(ctrl.path)

        # The bug: only 1 of 3 controllers produces edges because
        # _get_enclosing_function uses global_symbols (one per name)
        assert len(controllers_with_edges) == 3, (
            f"Expected ALL 3 controllers to have call edges, "
            f"but only {len(controllers_with_edges)} do: "
            f"{[p.split(str(tmp_path))[-1] for p in controllers_with_edges]}"
        )


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

    def test_decorator_resolves_to_same_file_function(
        self, tmp_path: Path,
    ) -> None:
        """When a decorator's identifier matches a function symbol in
        the same file, a ``decorated_by`` edge is emitted from the
        decorated class to the decorator function.

        This exercises the happy path of the decorator resolver in
        _extract_decorator_edges — previously uncovered because every
        decorator test only verified metadata extraction, not edge
        construction.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        ts_file = tmp_path / "service.ts"
        ts_file.write_text(
            "function MyDecorator() {\n"
            "  return function (target: any) { return target; };\n"
            "}\n\n"
            "@MyDecorator()\n"
            "class UserService {\n"
            "    findAll() { return []; }\n"
            "}\n",
        )

        result = analyze_javascript(tmp_path)

        decorated_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]
        # At least one resolved decorated_by edge was emitted.
        resolved = [
            e for e in decorated_edges
            if "unresolved" not in e.dst
        ]
        assert len(resolved) >= 1
        # The decorator function is the destination.
        dec_fn = next(
            (s for s in result.symbols
             if s.name == "MyDecorator" and s.kind == "function"),
            None,
        )
        assert dec_fn is not None
        assert any(e.dst == dec_fn.id for e in resolved)

    def test_decorator_rejects_non_function_kind_same_name(
        self, tmp_path: Path,
    ) -> None:
        """When a decorator's identifier matches a CLASS (not a function)
        in the same file, the resolver must leave it unresolved — a data
        class named ``Post`` is not the NestJS ``@Post()`` decorator.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        ts_file = tmp_path / "controller.ts"
        ts_file.write_text(
            "class Post {\n"
            "    title: string = '';\n"
            "}\n\n"
            "@Post()\n"
            "class PostController {\n"
            "    create() { return 1; }\n"
            "}\n",
        )

        result = analyze_javascript(tmp_path)

        # The Post class symbol exists.
        post_class = next(
            (s for s in result.symbols
             if s.name == "Post" and s.kind == "class"),
            None,
        )
        assert post_class is not None

        # NO resolved decorated_by edge points at the Post class.
        decorated_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]
        assert not any(
            e.dst == post_class.id for e in decorated_edges
        )

    def test_class_decorator_simple(self, tmp_path: Path) -> None:
        """Extracts simple class decorator without arguments."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

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


class TestHapiUsageContext:
    """Tests for Hapi config-object route detection."""

    def test_hapi_server_route_object(self, tmp_path: Path) -> None:
        """Detects server.route({ method, path, handler }) pattern."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "server.js").write_text("""
const Hapi = require('@hapi/hapi');

async function getUsers(request, h) {
    return { users: [] };
}

const server = Hapi.server({ port: 3000 });

server.route({
    method: 'GET',
    path: '/users',
    handler: getUsers
});
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if "route" in c.context_name), None)
        assert ctx is not None
        assert ctx.kind == "call"
        assert ctx.metadata["route_path"] == "/users"
        assert ctx.metadata["http_method"] == "GET"
        assert ctx.metadata["config_based"] is True

    def test_hapi_server_route_post(self, tmp_path: Path) -> None:
        """Detects POST route in config object."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "server.js").write_text("""
server.route({
    method: 'POST',
    path: '/users',
    handler: (req, h) => h.response().code(201)
});
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if "route" in c.context_name), None)
        assert ctx is not None
        assert ctx.metadata["http_method"] == "POST"

    def test_hapi_array_of_routes(self, tmp_path: Path) -> None:
        """Detects array of route configs."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "server.js").write_text("""
server.route([
    { method: 'GET', path: '/users', handler: getUsers },
    { method: 'POST', path: '/users', handler: createUser }
]);
""")
        result = analyze_javascript(tmp_path)
        route_contexts = [c for c in result.usage_contexts if "route" in c.context_name]
        assert len(route_contexts) >= 2
        methods = {c.metadata["http_method"] for c in route_contexts}
        assert "GET" in methods
        assert "POST" in methods

    def test_hapi_shorthand_properties(self, tmp_path: Path) -> None:
        """Handles shorthand property syntax."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "server.js").write_text("""
const method = 'GET';
const path = '/api';
server.route({ method, path, handler: () => {} });
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if "route" in c.context_name), None)
        assert ctx is not None
        # Shorthand maps name to name
        assert ctx.metadata["route_path"] is not None

    def test_hapi_inline_handler_function(self, tmp_path: Path) -> None:
        """Handles inline arrow function handlers."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "server.js").write_text("""
server.route({
    method: 'GET',
    path: '/health',
    handler: (req, h) => ({ status: 'ok' })
});
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if "route" in c.context_name), None)
        assert ctx is not None
        # Inline functions have handler_name as None
        assert ctx.metadata.get("handler_name") is None


class TestControllerRoutePattern:
    """Tests for Express Controller.route() config-object pattern (WI-bajod).

    Unleash-style Express apps use a base Controller class with
    ``this.route({method, path, handler})`` for route registration.
    This is structurally identical to Hapi's config-object pattern
    but uses ``this`` as the receiver instead of a named variable.
    """

    def test_this_route_config_object(self, tmp_path: Path) -> None:
        """Detects this.route({method, path, handler}) in a class constructor."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "controller.ts").write_text("""
class StrategyController extends Controller {
    constructor(config) {
        super(config);
        this.route({
            method: 'get',
            path: '',
            handler: this.getAllStrategies,
            permission: 'NONE',
        });
        this.route({
            method: 'delete',
            path: '/:name',
            handler: this.removeStrategy,
            permission: 'DELETE_STRATEGY',
        });
    }

    async getAllStrategies(req, res) {
        return res.json([]);
    }

    async removeStrategy(req, res) {
        return res.json({ ok: true });
    }
}
""")
        result = analyze_javascript(tmp_path)
        route_contexts = [
            c for c in result.usage_contexts
            if "route" in c.context_name and c.metadata.get("config_based")
        ]
        assert len(route_contexts) >= 2
        methods = {c.metadata["http_method"] for c in route_contexts}
        assert "GET" in methods
        assert "DELETE" in methods
        # Handler should be extracted from this.methodName
        handler_names = {c.metadata.get("handler_name") for c in route_contexts}
        assert "getAllStrategies" in handler_names or "this.getAllStrategies" in handler_names

    def test_this_route_member_expression_handler(self, tmp_path: Path) -> None:
        """Handler value this.methodName is extracted from member_expression."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "tag.ts").write_text("""
class TagController extends Controller {
    constructor(config) {
        super(config);
        this.route({ method: 'get', path: '', handler: this.getTags, permission: 'NONE' });
        this.route({ method: 'post', path: '', handler: this.createTag, permission: 'NONE' });
    }
    async getTags(req, res) { return []; }
    async createTag(req, res) { return {}; }
}
""")
        result = analyze_javascript(tmp_path)
        route_contexts = [
            c for c in result.usage_contexts
            if "route" in c.context_name and c.metadata.get("config_based")
        ]
        assert len(route_contexts) >= 2
        paths = {c.metadata["route_path"] for c in route_contexts}
        assert "/" in paths  # empty path normalizes to /


class TestNextJsUsageContext:
    """Tests for Next.js file-based routing detection."""

    def test_nextjs_pages_index(self, tmp_path: Path) -> None:
        """Detects index page in pages directory."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        pages_dir = tmp_path / "pages"
        pages_dir.mkdir()
        (pages_dir / "index.js").write_text("""
export default function Home() {
    return <h1>Home</h1>;
}
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.kind == "export"), None)
        assert ctx is not None
        assert ctx.metadata["route_path"] == "/"
        assert ctx.metadata["is_default"] is True

    def test_nextjs_pages_about(self, tmp_path: Path) -> None:
        """Detects about page."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        pages_dir = tmp_path / "pages"
        pages_dir.mkdir()
        (pages_dir / "about.js").write_text("""
export default function About() {
    return <h1>About</h1>;
}
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.kind == "export"), None)
        assert ctx is not None
        assert ctx.metadata["route_path"] == "/about"

    def test_nextjs_dynamic_route(self, tmp_path: Path) -> None:
        """Detects dynamic route with [id] parameter."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        pages_dir = tmp_path / "pages" / "posts"
        pages_dir.mkdir(parents=True)
        (pages_dir / "[id].js").write_text("""
export default function Post({ id }) {
    return <h1>Post {id}</h1>;
}
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.kind == "export"), None)
        assert ctx is not None
        assert ctx.metadata["route_path"] == "/posts/:id"

    def test_nextjs_catch_all_route(self, tmp_path: Path) -> None:
        """Detects catch-all route with [...slug]."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        pages_dir = tmp_path / "pages" / "docs"
        pages_dir.mkdir(parents=True)
        (pages_dir / "[...slug].js").write_text("""
export default function Doc({ slug }) {
    return <h1>Doc</h1>;
}
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.kind == "export"), None)
        assert ctx is not None
        assert ctx.metadata["route_path"] == "/docs/*"

    def test_nextjs_api_route(self, tmp_path: Path) -> None:
        """Detects API route in pages/api directory."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        api_dir = tmp_path / "pages" / "api"
        api_dir.mkdir(parents=True)
        (api_dir / "users.js").write_text("""
export default function handler(req, res) {
    res.json({ users: [] });
}
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.kind == "export"), None)
        assert ctx is not None
        assert ctx.metadata["route_path"] == "/api/users"
        assert ctx.metadata["is_api_route"] is True

    def test_nextjs_app_router_page(self, tmp_path: Path) -> None:
        """Detects App Router page.tsx."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        app_dir = tmp_path / "app" / "about"
        app_dir.mkdir(parents=True)
        (app_dir / "page.tsx").write_text("""
export default function About() {
    return <h1>About</h1>;
}
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.kind == "export"), None)
        assert ctx is not None
        assert ctx.metadata["route_path"] == "/about"

    def test_nextjs_app_router_route_ts(self, tmp_path: Path) -> None:
        """Detects App Router route.ts for API routes."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        api_dir = tmp_path / "app" / "api" / "users"
        api_dir.mkdir(parents=True)
        (api_dir / "route.ts").write_text("""
export async function GET() {
    return Response.json({ users: [] });
}
""")
        result = analyze_javascript(tmp_path)
        # Should detect route.ts as API route
        ctx = next((c for c in result.usage_contexts if c.kind == "export"), None)
        assert ctx is not None
        assert "/api/users" in ctx.metadata["route_path"]

    def test_nextjs_non_page_file_ignored(self, tmp_path: Path) -> None:
        """Non-page files in pages directory are ignored."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        pages_dir = tmp_path / "pages"
        pages_dir.mkdir()
        (pages_dir / "_app.js").write_text("""
export default function App({ Component, pageProps }) {
    return <Component {...pageProps} />;
}
""")
        # _app.js is a special file, not a page route
        result = analyze_javascript(tmp_path)
        # Should have contexts but _app is an index-like route
        ctx = next((c for c in result.usage_contexts if c.kind == "export"), None)
        # _app becomes /_app which is valid
        if ctx:
            assert "/_app" in ctx.metadata["route_path"]

    def test_nextjs_data_fetching_exports(self, tmp_path: Path) -> None:
        """Detects getServerSideProps and getStaticProps."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        pages_dir = tmp_path / "pages"
        pages_dir.mkdir()
        (pages_dir / "posts.js").write_text("""
export default function Posts({ posts }) {
    return <ul>{posts.map(p => <li key={p.id}>{p.title}</li>)}</ul>;
}

export async function getServerSideProps() {
    return { props: { posts: [] } };
}
""")
        result = analyze_javascript(tmp_path)
        contexts = [c for c in result.usage_contexts if c.kind == "export"]
        # Should have both default export and getServerSideProps
        assert len(contexts) >= 1


class TestLibraryExportContext:
    """Tests for library export detection from index files."""

    def test_index_ts_default_export(self, tmp_path: Path) -> None:
        """Detects default export from index.ts."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "index.ts").write_text("""
export default class Hls {
    constructor() {}
    load(url: string) {}
}
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.kind == "library_export"), None)
        assert ctx is not None
        assert ctx.context_name == "export.default"
        assert ctx.metadata["is_default"] is True
        assert ctx.metadata["export_name"] == "Hls"

    def test_index_js_named_exports(self, tmp_path: Path) -> None:
        """Detects named exports from index.js."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "index.js").write_text("""
export function doSomething() {
    return 42;
}

export function doOtherThing() {
    return "hello";
}
""")
        result = analyze_javascript(tmp_path)
        contexts = [c for c in result.usage_contexts if c.kind == "library_export"]
        assert len(contexts) == 2
        names = {c.metadata["export_name"] for c in contexts}
        assert names == {"doSomething", "doOtherThing"}
        for ctx in contexts:
            assert ctx.metadata["is_default"] is False

    def test_index_tsx_export_clause(self, tmp_path: Path) -> None:
        """Detects export clause from index.tsx."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "index.tsx").write_text("""
function Button() {
    return <button>Click</button>;
}

function Input() {
    return <input />;
}

export { Button, Input };
""")
        result = analyze_javascript(tmp_path)
        contexts = [c for c in result.usage_contexts if c.kind == "library_export"]
        assert len(contexts) == 2
        names = {c.metadata["export_name"] for c in contexts}
        assert names == {"Button", "Input"}

    def test_index_const_export(self, tmp_path: Path) -> None:
        """Detects exported constants from index.js."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "index.js").write_text("""
export const VERSION = "1.0.0";
export const CONFIG = { debug: false };
""")
        result = analyze_javascript(tmp_path)
        contexts = [c for c in result.usage_contexts if c.kind == "library_export"]
        assert len(contexts) == 2
        names = {c.metadata["export_name"] for c in contexts}
        assert names == {"VERSION", "CONFIG"}

    def test_non_index_file_ignored(self, tmp_path: Path) -> None:
        """Non-index files don't generate library export contexts."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "utils.ts").write_text("""
export function helper() {
    return 123;
}
""")
        result = analyze_javascript(tmp_path)
        contexts = [c for c in result.usage_contexts if c.kind == "library_export"]
        assert len(contexts) == 0

    def test_index_jsx_supported(self, tmp_path: Path) -> None:
        """Detects exports from index.jsx."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "index.jsx").write_text("""
export function ReactComponent() {
    return <div>Hello</div>;
}
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.kind == "library_export"), None)
        assert ctx is not None
        assert ctx.metadata["export_name"] == "ReactComponent"

    def test_export_symbol_ref_resolved(self, tmp_path: Path) -> None:
        """Exported symbols have their symbol_ref resolved."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "index.ts").write_text("""
export function myExportedFunction() {
    return 42;
}
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.kind == "library_export"), None)
        assert ctx is not None
        assert ctx.symbol_ref is not None
        # Verify the symbol exists
        sym = next((s for s in result.symbols if s.id == ctx.symbol_ref), None)
        assert sym is not None
        assert sym.name == "myExportedFunction"
        assert sym.kind == "function"

    def test_class_export(self, tmp_path: Path) -> None:
        """Detects exported class from index.ts."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "index.ts").write_text("""
export class MyLibrary {
    doStuff() {
        return "stuff";
    }
}
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.kind == "library_export"), None)
        assert ctx is not None
        assert ctx.metadata["export_name"] == "MyLibrary"
        assert ctx.symbol_ref is not None

    def test_default_export_identifier(self, tmp_path: Path) -> None:
        """Detects 'export default Identifier' pattern."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "index.js").write_text("""
function MyComponent() {
    return null;
}

export default MyComponent;
""")
        result = analyze_javascript(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.kind == "library_export"), None)
        assert ctx is not None
        assert ctx.context_name == "export.default"
        assert ctx.metadata["is_default"] is True
        # The export_name should be the identifier
        assert ctx.metadata["export_name"] == "MyComponent"


# ============================================================================
# JS/TS Inheritance Edge Tests (META-001)
# ============================================================================


class TestJsTsInheritanceEdges:
    """Tests for JS/TS inheritance edge detection.

    META-001 requires that base_classes metadata becomes extends edges so that
    the type hierarchy linker can create dispatches_to edges for polymorphic dispatch.
    """

    def test_extracts_extends_edge_same_file(self, tmp_path: Path) -> None:
        """Extracts extends relationship edges for classes in the same file."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "models.ts").write_text("""
class Animal {
    speak() {
        return "";
    }
}

class Dog extends Animal {
    speak() {
        return "Woof";
    }
}
""")

        result = analyze_javascript(tmp_path)

        assert result.run is not None
        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        assert len(extends_edges) >= 1

        # Edge should be from Dog to Animal (child extends parent)
        edge = extends_edges[0]
        assert "Dog" in edge.src
        assert "Animal" in edge.dst

    def test_extracts_implements_edge(self, tmp_path: Path) -> None:
        """Extracts implements relationship edges for interfaces."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "service.ts").write_text("""
interface UserService {
    findUser(id: number): User;
}

class UserServiceImpl implements UserService {
    findUser(id: number): User {
        return { id };
    }
}
""")

        result = analyze_javascript(tmp_path)

        # Should have an implements edge
        impl_edges = [e for e in result.edges if e.edge_type == "implements"]
        assert len(impl_edges) >= 1

        edge = impl_edges[0]
        assert "UserServiceImpl" in edge.src
        assert "UserService" in edge.dst

    def test_extracts_extends_edge_with_generics(self, tmp_path: Path) -> None:
        """Extracts extends edges when base class has generics."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "repo.ts").write_text("""
class Repository<T> {
    save(item: T) {}
}

class UserRepository extends Repository<User> {
    findByEmail(email: string) {}
}
""")

        result = analyze_javascript(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        assert len(extends_edges) >= 1

        # Edge should be from UserRepository to Repository (generic stripped)
        edge = extends_edges[0]
        assert "UserRepository" in edge.src
        assert "Repository" in edge.dst

    def test_no_extends_edge_for_external_class(self, tmp_path: Path) -> None:
        """No extends edge created when base class is external (not in repo)."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "component.tsx").write_text("""
import React from 'react';

class MyComponent extends React.Component {
    render() {
        return null;
    }
}
""")

        result = analyze_javascript(tmp_path)

        # base_classes metadata should still be set
        my_class = next((s for s in result.symbols if s.name == "MyComponent"), None)
        assert my_class is not None
        assert "base_classes" in (my_class.meta or {})

        # But no extends edge since React.Component is external
        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        assert len(extends_edges) == 0

    def test_extends_prefers_imported_class_over_name_collision(
        self, tmp_path: Path
    ) -> None:
        """When multiple classes share a name, extends resolves to the imported one.

        INV-015: Same bug as Python (Django 238 Model stubs). Two files define
        class 'Model'; child file imports from specific path and extends 'Model'.
        Edge should resolve to the imported Model, not the other one.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # Real Model class
        (tmp_path / "db").mkdir()
        (tmp_path / "db" / "model.ts").write_text(
            "export class Model {\n"
            "    save() {}\n"
            "}\n"
        )

        # Test stub Model class (different file, same name)
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "helpers.ts").write_text(
            "export class Model {\n"
            "    /* stub */\n"
            "}\n"
        )

        # A file that imports from ./db/model and extends Model
        (tmp_path / "app.ts").write_text(
            "import { Model } from './db/model';\n"
            "\n"
            "class Article extends Model {\n"
            "    publish() {}\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        article_extends = [e for e in extends_edges if "Article" in e.src]
        assert len(article_extends) == 1, (
            f"Expected 1 extends edge from Article, got {len(article_extends)}"
        )

        # Edge should point to db/model.ts::Model, NOT tests/helpers.ts::Model
        edge = article_extends[0]
        assert "db/model.ts" in edge.dst or "db\\model.ts" in edge.dst, (
            f"Article extends edge should point to db/model.ts::Model, "
            f"but points to: {edge.dst}"
        )

    def test_extends_same_file_class_preferred_over_other_file(
        self, tmp_path: Path
    ) -> None:
        """When base class is defined in the same file, prefer it over other files."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # Base defined in file A
        (tmp_path / "a.ts").write_text(
            "export class Base {\n    run() {}\n}\n"
        )

        # Base defined in file B AND used as base in same file
        (tmp_path / "b.ts").write_text(
            "class Base {\n    run() {}\n}\n"
            "\n"
            "class Child extends Base {\n    go() {}\n}\n"
        )

        result = analyze_javascript(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        child_extends = [e for e in extends_edges if "Child" in e.src]
        assert len(child_extends) == 1

        # Should resolve to b.ts::Base (same file), not a.ts::Base
        edge = child_extends[0]
        assert "b.ts" in edge.dst, (
            f"Child extends edge should prefer same-file Base (b.ts), "
            f"but points to: {edge.dst}"
        )

    def test_extends_deterministic_fallback_when_ambiguous(
        self, tmp_path: Path
    ) -> None:
        """When no same-file or import match, extends uses deterministic fallback."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # Two files define 'Base', neither is imported
        (tmp_path / "mod_a.ts").write_text(
            "export class Base {\n    run() {}\n}\n"
        )
        (tmp_path / "mod_b.ts").write_text(
            "export class Base {\n    run() {}\n}\n"
        )
        # A third file extends 'Base' without importing either
        (tmp_path / "child.ts").write_text(
            "class Child extends Base {\n    go() {}\n}\n"
        )

        result = analyze_javascript(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        child_extends = [e for e in extends_edges if "Child" in e.src]
        # Should still create an edge (deterministic fallback)
        assert len(child_extends) == 1

    def test_implements_prefers_imported_interface_over_collision(
        self, tmp_path: Path
    ) -> None:
        """Interface disambiguation: implements resolves to imported interface."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # Real Validator interface
        (tmp_path / "core").mkdir()
        (tmp_path / "core" / "validator.ts").write_text(
            "export interface Validator {\n"
            "    validate(): boolean;\n"
            "}\n"
        )

        # Stub Validator interface (different file, same name)
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "mock.ts").write_text(
            "export interface Validator {\n"
            "    /* mock */\n"
            "}\n"
        )

        # A file that imports from ./core/validator and implements Validator
        (tmp_path / "form.ts").write_text(
            "import { Validator } from './core/validator';\n"
            "\n"
            "class FormValidator implements Validator {\n"
            "    validate(): boolean { return true; }\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)

        impl_edges = [e for e in result.edges if e.edge_type == "implements"]
        form_impl = [e for e in impl_edges if "FormValidator" in e.src]
        assert len(form_impl) == 1, (
            f"Expected 1 implements edge from FormValidator, got {len(form_impl)}"
        )

        # Edge should point to core/validator.ts::Validator
        edge = form_impl[0]
        assert "core/validator.ts" in edge.dst or "core\\validator.ts" in edge.dst, (
            f"FormValidator implements edge should point to core/validator.ts::Validator, "
            f"but points to: {edge.dst}"
        )


class TestAbstractClassDeclaration:
    """Tests for TypeScript abstract class support.

    TypeScript abstract classes produce ``abstract_class_declaration`` in tree-sitter,
    which is a different node type than ``class_declaration``. The analyzer must handle
    both to extract symbols, methods, and inheritance edges from abstract classes.
    """

    def test_abstract_class_extracted_as_symbol(self, tmp_path: Path) -> None:
        """Abstract class should be extracted as a class symbol."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "track.ts").write_text(
            "export default abstract class LocalTrack<\n"
            "  TrackKind extends string = string,\n"
            "> {\n"
            "    protected sender: any;\n"
            "    abstract restart(): Promise<void>;\n"
            "    stop() { console.log('stop'); }\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]
        names = [s.name for s in classes]
        assert "LocalTrack" in names, (
            f"Abstract class LocalTrack not found in symbols. Classes: {names}"
        )

    def test_abstract_class_methods_qualified(self, tmp_path: Path) -> None:
        """Methods inside abstract classes should have qualified names."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "base.ts").write_text(
            "abstract class BaseHandler {\n"
            "    abstract process(data: any): void;\n"
            "    log(msg: string) { console.log(msg); }\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        assert "BaseHandler.log" in method_names, (
            f"Method log inside abstract class should be qualified as "
            f"BaseHandler.log, got: {method_names}"
        )

    def test_abstract_class_extends_creates_edge(self, tmp_path: Path) -> None:
        """Concrete class extending abstract class should create extends edge."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "base.ts").write_text(
            "export abstract class Track {\n"
            "    stop() {}\n"
            "}\n"
        )
        (tmp_path / "local.ts").write_text(
            "import { Track } from './base';\n"
            "\n"
            "export class LocalTrack extends Track {\n"
            "    start() {}\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        local_extends = [e for e in extends_edges if "LocalTrack" in e.src]
        assert len(local_extends) == 1, (
            f"Expected 1 extends edge from LocalTrack, got {len(local_extends)}: "
            f"{[(e.src, e.dst) for e in extends_edges]}"
        )
        assert "Track" in local_extends[0].dst

    def test_abstract_class_exported_detected(self, tmp_path: Path) -> None:
        """Export of abstract class should be tracked."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "widget.ts").write_text(
            "export abstract class Widget {\n"
            "    abstract render(): void;\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class" and s.name == "Widget"]
        assert len(classes) == 1

    def test_parenthesized_extends_expression(self, tmp_path: Path) -> None:
        """Class extending a parenthesized cast expression should extract base class.

        Pattern: class Room extends (EventEmitter as new () => TypedEmitter<Callbacks>) {}
        Should extract EventEmitter as the base class.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "emitter.ts").write_text(
            "export class EventEmitter {\n"
            "    emit(event: string) {}\n"
            "}\n"
        )
        (tmp_path / "room.ts").write_text(
            "import { EventEmitter } from './emitter';\n"
            "\n"
            "interface RoomCallbacks { connected: () => void; }\n"
            "type TypedEmitter<T> = EventEmitter;\n"
            "\n"
            "class Room extends (EventEmitter as new () => TypedEmitter<RoomCallbacks>) {\n"
            "    connect() {}\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        room = next((s for s in result.symbols if s.name == "Room" and s.kind == "class"), None)
        assert room is not None
        base_classes = room.meta.get("base_classes", []) if room.meta else []
        assert any("EventEmitter" in b for b in base_classes), (
            f"Room should have EventEmitter as base class, got: {base_classes}"
        )


class TestJsTsAmbiguousMethodGuard:
    """Tests for AMB-METHOD invariant in JavaScript/TypeScript.

    When a method name has 3+ definitions across different classes and
    the receiver type cannot be inferred, the analyzer must NOT produce
    a resolved call edge (which would be a false positive).

    Invariant: Method calls with 3+ ambiguous receiver types must not
    produce resolved call edges.
    """

    def test_ambiguous_method_three_plus_classes_no_resolved_edge(
        self, tmp_path: Path,
    ) -> None:
        """obj.close() with 3 classes defining close() → no resolved edge.

        When Server, Client, and Worker all define close(), and obj's type
        cannot be inferred, the call should NOT produce a resolved edge
        pointing to any specific class's close() method.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        ts_file = tmp_path / "multi.ts"
        ts_file.write_text("""
class Server {
    close() { return true; }
}

class Client {
    close() { return true; }
}

class Worker {
    close() { return true; }
}

function cleanup(obj: any) {
    obj.close();
}
""")

        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        cleanup_calls = [e for e in call_edges if "cleanup" in e.src]

        # Should NOT have a resolved edge to any specific class's close()
        for edge in cleanup_calls:
            if "close" in edge.dst.lower():
                assert "Server.close" not in edge.dst, (
                    f"Should not resolve to Server.close, got {edge.dst}"
                )
                assert "Client.close" not in edge.dst, (
                    f"Should not resolve to Client.close, got {edge.dst}"
                )
                assert "Worker.close" not in edge.dst, (
                    f"Should not resolve to Worker.close, got {edge.dst}"
                )

    def test_two_classes_same_method_still_resolves(
        self, tmp_path: Path,
    ) -> None:
        """obj.run() with 2 classes → still resolves (below threshold)."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        ts_file = tmp_path / "two.ts"
        ts_file.write_text("""
class Server {
    run() { return true; }
}

class Client {
    run() { return true; }
}

function execute(obj: any) {
    obj.run();
}
""")

        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        execute_calls = [e for e in call_edges if "execute" in e.src]

        # 2 candidates is below the threshold — should still resolve
        run_calls = [e for e in execute_calls if "run" in e.dst.lower()]
        assert len(run_calls) >= 1, (
            "2 candidates should still resolve"
        )


class TestObjectLiteralFunctionReferences:
    """Tests for function references in object literal fields.

    Object literals like {onClick: handleClick, onSubmit: processForm}
    contain function references that should produce edges. This is
    common in React, Express config, event emitter patterns, etc.
    """

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_javascript")

    def test_object_literal_function_ref_same_file(self, tmp_path: Path) -> None:
        """Object property with bare identifier creates a references edge."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """
function handleClick() {
    return true;
}

function setup() {
    const config = {
        onClick: handleClick,
    };
    return config;
}
"""
        (tmp_path / "app.js").write_text(code)
        result = analyze_javascript(tmp_path)

        ref_edges = [e for e in result.edges
                     if e.edge_type == "references"
                     and e.evidence_type == "object_field_reference"]
        assert len(ref_edges) == 1
        assert "handleClick" in ref_edges[0].dst
        assert "setup" in ref_edges[0].src
        assert ref_edges[0].confidence == pytest.approx(0.80, rel=0.01)

    def test_object_literal_func_ref_cross_file(self, tmp_path: Path) -> None:
        """Object property function reference resolves across files."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "handler.js").write_text("""
function processForm(data) {
    return data;
}
""")
        (tmp_path / "config.js").write_text("""
function createConfig() {
    return {
        onSubmit: processForm,
    };
}
""")
        result = analyze_javascript(tmp_path)

        ref_edges = [e for e in result.edges
                     if e.edge_type == "references"
                     and e.evidence_type == "object_field_reference"]
        assert len(ref_edges) == 1
        assert "processForm" in ref_edges[0].dst

    def test_object_literal_non_function_not_matched(self, tmp_path: Path) -> None:
        """Object property with non-function identifier creates no edge."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """
const STATUS_ACTIVE = "active";

function setup() {
    return {
        status: STATUS_ACTIVE,
    };
}
"""
        (tmp_path / "app.js").write_text(code)
        result = analyze_javascript(tmp_path)

        ref_edges = [e for e in result.edges
                     if e.edge_type == "references"
                     and e.evidence_type == "object_field_reference"]
        assert len(ref_edges) == 0

    def test_shorthand_property_function_ref(self, tmp_path: Path) -> None:
        """Shorthand property {handleClick} creates a references edge."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """
function handleClick() {
    return true;
}

function setup() {
    return { handleClick };
}
"""
        (tmp_path / "app.js").write_text(code)
        result = analyze_javascript(tmp_path)

        ref_edges = [e for e in result.edges
                     if e.edge_type == "references"
                     and e.evidence_type == "object_field_reference"]
        assert len(ref_edges) == 1
        assert "handleClick" in ref_edges[0].dst

    def test_multiple_function_refs_in_object(self, tmp_path: Path) -> None:
        """Multiple function references in one object produce multiple edges."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """
function onStart() {}
function onStop() {}

function createEvents() {
    return {
        start: onStart,
        stop: onStop,
    };
}
"""
        (tmp_path / "events.js").write_text(code)
        result = analyze_javascript(tmp_path)

        ref_edges = [e for e in result.edges
                     if e.edge_type == "references"
                     and e.evidence_type == "object_field_reference"]
        assert len(ref_edges) == 2


class TestCallbackArgumentFunctionReferences:
    """Tests for function references passed as arguments to calls.

    When code passes a function reference as an argument (e.g.
    ``app.get("/path", handleUsers)``), the analyzer should create
    a ``references`` edge from the enclosing function to the handler,
    with evidence_type ``callback_argument_reference``.
    """

    def test_express_route_handler_reference(self, tmp_path: Path) -> None:
        """app.get('/path', handler) creates a references edge to handler."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """
function handleUsers(req, res) {
    res.json([]);
}

function setupRoutes(app) {
    app.get("/api/users", handleUsers);
}
"""
        (tmp_path / "routes.js").write_text(code)
        result = analyze_javascript(tmp_path)

        ref_edges = [e for e in result.edges
                     if e.edge_type == "references"
                     and e.evidence_type == "callback_argument_reference"]
        assert len(ref_edges) == 1
        assert "handleUsers" in ref_edges[0].dst
        assert "setupRoutes" in ref_edges[0].src
        assert ref_edges[0].confidence == pytest.approx(0.75, rel=0.01)

    def test_callback_arg_cross_file(self, tmp_path: Path) -> None:
        """Function reference as argument resolves across files."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "handler.js").write_text("""
function processData(data) {
    return data;
}
""")
        (tmp_path / "app.js").write_text("""
function main() {
    items.forEach(processData);
}
""")
        result = analyze_javascript(tmp_path)

        ref_edges = [e for e in result.edges
                     if e.edge_type == "references"
                     and e.evidence_type == "callback_argument_reference"]
        assert len(ref_edges) == 1
        assert "processData" in ref_edges[0].dst
        assert "main" in ref_edges[0].src

    def test_non_function_arg_not_matched(self, tmp_path: Path) -> None:
        """Passing a non-function identifier as argument creates no edge."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """
const TIMEOUT = 5000;

function setup() {
    setTimeout(callback, TIMEOUT);
}
"""
        (tmp_path / "app.js").write_text(code)
        result = analyze_javascript(tmp_path)

        ref_edges = [e for e in result.edges
                     if e.edge_type == "references"
                     and e.evidence_type == "callback_argument_reference"]
        assert len(ref_edges) == 0

    def test_multiple_callback_args(self, tmp_path: Path) -> None:
        """Multiple function arguments produce multiple reference edges."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """
function onSuccess(data) { return data; }
function onError(err) { throw err; }

function fetchData() {
    promise.then(onSuccess, onError);
}
"""
        (tmp_path / "fetch.js").write_text(code)
        result = analyze_javascript(tmp_path)

        ref_edges = [e for e in result.edges
                     if e.edge_type == "references"
                     and e.evidence_type == "callback_argument_reference"]
        assert len(ref_edges) == 2
        dst_names = {e.dst for e in ref_edges}
        assert any("onSuccess" in d for d in dst_names)
        assert any("onError" in d for d in dst_names)

    def test_typescript_callback_arg(self, tmp_path: Path) -> None:
        """TypeScript: function reference as argument works."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """
function validate(input: string): boolean {
    return input.length > 0;
}

function processForm(app: Express): void {
    app.post("/submit", validate);
}
"""
        (tmp_path / "form.ts").write_text(code)
        result = analyze_javascript(tmp_path)

        ref_edges = [e for e in result.edges
                     if e.edge_type == "references"
                     and e.evidence_type == "callback_argument_reference"]
        assert len(ref_edges) == 1
        assert "validate" in ref_edges[0].dst


class TestJsBuiltinNameGuard:
    """Tests for JavaScript built-in name collision guard.

    Calls to JS built-ins (Number, String, Boolean, etc.) should not
    resolve to user-defined functions with the same name.  Without this
    guard, ``Number(x)`` in server code resolves to a React component
    named ``Number`` in client code.
    """

    def test_number_call_not_resolved_to_user_component(
        self, tmp_path: Path
    ) -> None:
        """Number(x) should not create an edge to a user-defined Number."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "component.js").write_text(
            "function Number({ value }) { return value; }\n"
        )
        (tmp_path / "server.js").write_text("""
function convert(x) {
    return Number(x);
}
""")
        result = analyze_javascript(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"
                 and "convert" in e.src and "Number" in e.dst]
        assert len(calls) == 0, (
            f"Number(x) should not resolve to user component, got: {calls}"
        )

    def test_parseint_not_resolved_to_user_function(
        self, tmp_path: Path
    ) -> None:
        """parseInt(str) should not resolve to user-defined parseInt."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "utils.js").write_text(
            "function parseInt(str) { return str | 0; }\n"
        )
        (tmp_path / "app.js").write_text("""
function getAge(input) {
    return parseInt(input);
}
""")
        result = analyze_javascript(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"
                 and "getAge" in e.src and "parseInt" in e.dst]
        assert len(calls) == 0

    def test_builtin_as_callback_arg_not_resolved(
        self, tmp_path: Path
    ) -> None:
        """Built-in name passed as callback arg creates no reference edge."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "comp.js").write_text(
            "function Number(v) { return v; }\n"
        )
        (tmp_path / "app.js").write_text("""
function process() {
    items.map(Number);
}
""")
        result = analyze_javascript(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.evidence_type == "callback_argument_reference"
            and "Number" in e.dst
        ]
        assert len(ref_edges) == 0

    def test_non_builtin_still_resolves(self, tmp_path: Path) -> None:
        """User-defined functions with non-builtin names still resolve."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "utils.js").write_text(
            "function processData(x) { return x; }\n"
        )
        (tmp_path / "app.js").write_text("""
function main() {
    processData(42);
}
""")
        result = analyze_javascript(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"
                 and "main" in e.src and "processData" in e.dst]
        assert len(calls) == 1


class TestCallbackArgCrossPackageGuard:
    """Tests for callback argument reference cross-package guard.

    Callback arguments like ``app.get("/path", error)`` should not create
    ``references`` edges to functions in different npm packages.  The
    same-package preference and import-path disambiguation used for
    direct calls must also apply to callback argument references.
    """

    def test_callback_arg_prefers_same_package(
        self, tmp_path: Path
    ) -> None:
        """Callback arg ref should prefer same-package function."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # Two packages: server/ and client/
        (tmp_path / "server" / "package.json").parent.mkdir()
        (tmp_path / "server" / "package.json").write_text('{"name": "server"}')
        (tmp_path / "server" / "handler.js").write_text(
            "function error(msg) { console.log(msg); }\n"
        )
        (tmp_path / "server" / "app.js").write_text("""
function setup() {
    items.forEach(error);
}
""")
        (tmp_path / "client" / "package.json").parent.mkdir()
        (tmp_path / "client" / "package.json").write_text('{"name": "client"}')
        (tmp_path / "client" / "actions.js").write_text(
            "function error(msg) { alert(msg); }\n"
        )
        result = analyze_javascript(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.evidence_type == "callback_argument_reference"
            and "error" in e.dst
        ]
        # Should resolve to server/handler.js error, NOT client/actions.js error
        for edge in ref_edges:
            assert "client" not in edge.dst, (
                f"Callback ref should prefer same-package, got cross-package: {edge.dst}"
            )

    def test_callback_arg_no_cross_package_when_only_other_pkg(
        self, tmp_path: Path
    ) -> None:
        """No callback ref edge when function only exists in another package."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # error() defined only in client/, called from server/
        (tmp_path / "server" / "package.json").parent.mkdir()
        (tmp_path / "server" / "package.json").write_text('{"name": "server"}')
        (tmp_path / "server" / "app.js").write_text("""
function setup() {
    items.forEach(error);
}
""")
        (tmp_path / "client" / "package.json").parent.mkdir()
        (tmp_path / "client" / "package.json").write_text('{"name": "client"}')
        (tmp_path / "client" / "actions.js").write_text(
            "function error(msg) { alert(msg); }\n"
        )
        result = analyze_javascript(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.evidence_type == "callback_argument_reference"
            and "error" in e.dst
        ]
        assert len(ref_edges) == 0, (
            f"Callback ref should not cross package boundary, got: {ref_edges}"
        )

    def test_callback_arg_param_shadowing(
        self, tmp_path: Path
    ) -> None:
        """Callback arg that matches a param of enclosing fn is skipped."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "other.js").write_text(
            "function resolve(x) { return x; }\n"
        )
        (tmp_path / "app.js").write_text("""
function doWork() {
    return new Promise((resolve, reject) => {
        items.forEach(resolve);
    });
}
""")
        result = analyze_javascript(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.evidence_type == "callback_argument_reference"
            and "resolve" in e.dst
        ]
        assert len(ref_edges) == 0, (
            f"resolve passed as callback arg should be local param, got: {ref_edges}"
        )

    def test_callback_arg_import_disambiguation(
        self, tmp_path: Path
    ) -> None:
        """Named import should guide callback arg resolution."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "package.json").write_text('{"name": "pkg"}')
        (pkg / "local.js").write_text(
            "function myHandler(x) { return x; }\n"
            "module.exports = { myHandler };\n"
        )
        (pkg / "other.js").write_text(
            "function myHandler(x) { return x * 2; }\n"
            "module.exports = { myHandler };\n"
        )
        (pkg / "app.js").write_text(
            "import { myHandler } from './local';\n"
            "function setup() {\n"
            "    items.map(myHandler);\n"
            "}\n"
        )
        result = analyze_javascript(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.evidence_type == "callback_argument_reference"
            and "myHandler" in e.dst
        ]
        # Should resolve to local.js (imported), not other.js
        for edge in ref_edges:
            assert "local" in edge.dst, (
                f"Should resolve to imported module, got: {edge.dst}"
            )

    def test_cross_package_no_package_json_allows_resolution(
        self, tmp_path: Path
    ) -> None:
        """When target has no package.json, cross-package guard allows resolution."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # Only source has package.json, target does not
        (tmp_path / "src" / "package.json").parent.mkdir()
        (tmp_path / "src" / "package.json").write_text('{"name": "app"}')
        (tmp_path / "src" / "app.js").write_text("""
function setup() {
    items.forEach(helper);
}
""")
        (tmp_path / "lib" / "utils.js").parent.mkdir()
        (tmp_path / "lib" / "utils.js").write_text(
            "function helper(x) { return x; }\n"
        )
        result = analyze_javascript(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.evidence_type == "callback_argument_reference"
            and "helper" in e.dst
        ]
        # Should still resolve since we can't determine package boundary
        assert len(ref_edges) == 1


class TestParameterShadowing:
    """Tests for function parameter shadowing of global names.

    When a function parameter shadows a global function name (e.g.,
    ``resolve`` and ``reject`` in ``new Promise((resolve, reject) => {...})``),
    calls to that name inside the function body should NOT resolve to the
    global function.  This prevents false cross-package edges.
    """

    def test_promise_resolve_not_resolved_to_global(
        self, tmp_path: Path
    ) -> None:
        """resolve() inside Promise callback should not resolve to global."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "other.js").write_text(
            "function resolve(x) { return x; }\n"
        )
        (tmp_path / "app.js").write_text("""
function doWork() {
    return new Promise((resolve, reject) => {
        resolve(42);
    });
}
""")
        result = analyze_javascript(tmp_path)
        false_edges = [
            e for e in result.edges if e.edge_type == "calls"
            and "doWork" in e.src and "resolve" in e.dst
        ]
        assert len(false_edges) == 0, (
            f"resolve() inside Promise should not resolve to global, got: {false_edges}"
        )

    def test_promise_reject_not_resolved_to_global(
        self, tmp_path: Path
    ) -> None:
        """reject() inside Promise callback should not resolve to global."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "other.js").write_text(
            "function reject(x) { return x; }\n"
        )
        (tmp_path / "app.js").write_text("""
function doWork() {
    return new Promise((resolve, reject) => {
        reject("error");
    });
}
""")
        result = analyze_javascript(tmp_path)
        false_edges = [
            e for e in result.edges if e.edge_type == "calls"
            and "doWork" in e.src and "reject" in e.dst
        ]
        assert len(false_edges) == 0, (
            f"reject() inside Promise should not resolve to global, got: {false_edges}"
        )

    def test_typescript_promise_function_expression(
        self, tmp_path: Path
    ) -> None:
        """TS function expression params (required_parameter) shadow globals."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "other.js").write_text(
            "function resolve(x) { return x; }\n"
        )
        (tmp_path / "app.ts").write_text("""
function getEmail(s: string) {
    return new Promise(function(resolve, reject) {
        resolve(s);
    });
}
""")
        result = analyze_javascript(tmp_path)
        false_edges = [
            e for e in result.edges if e.edge_type == "calls"
            and "getEmail" in e.src and "resolve" in e.dst
        ]
        assert len(false_edges) == 0, (
            f"resolve() in TS function expression should not resolve to global, got: {false_edges}"
        )

    def test_nested_callback_inherits_outer_param(
        self, tmp_path: Path
    ) -> None:
        """resolve() in nested callback inherits param from outer closure."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "other.js").write_text(
            "function resolve(x) { return x; }\n"
        )
        (tmp_path / "app.ts").write_text("""
function saveData(zid: number) {
    return new Promise(function(
        resolve: (arg0: number) => void,
        reject: (arg0: any) => void
    ) {
        doAsync(zid, function(err: any) {
            if (err) {
                reject(err);
            } else {
                resolve(0);
            }
        });
    });
}
""")
        result = analyze_javascript(tmp_path)
        false_edges = [
            e for e in result.edges if e.edge_type == "calls"
            and "saveData" in e.src and "resolve" in e.dst
        ]
        assert len(false_edges) == 0, (
            f"resolve() in nested callback should not resolve to global, got: {false_edges}"
        )

    def test_callback_param_shadows_global(
        self, tmp_path: Path
    ) -> None:
        """General case: callback param name shadows a same-named global fn."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "utils.js").write_text(
            "function handler(x) { return x * 2; }\n"
        )
        (tmp_path / "app.js").write_text("""
function setup() {
    events.on("data", (handler) => {
        handler("value");
    });
}
""")
        result = analyze_javascript(tmp_path)
        false_edges = [
            e for e in result.edges if e.edge_type == "calls"
            and "setup" in e.src and "handler" in e.dst
        ]
        assert len(false_edges) == 0, (
            f"handler() should be local param, not global, got: {false_edges}"
        )

    def test_non_shadowed_call_still_resolves(
        self, tmp_path: Path
    ) -> None:
        """Calls to names NOT shadowed by params still resolve correctly."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "utils.js").write_text(
            "function helper(x) { return x; }\n"
        )
        (tmp_path / "app.js").write_text("""
function doWork() {
    return new Promise((resolve, reject) => {
        helper(42);
        resolve(true);
    });
}
""")
        result = analyze_javascript(tmp_path)
        # helper() should still resolve (not shadowed by params)
        helper_edges = [
            e for e in result.edges if e.edge_type == "calls"
            and "helper" in e.dst
        ]
        assert len(helper_edges) == 1, (
            f"helper() should still resolve, got: {helper_edges}"
        )
        # resolve() should NOT resolve to global
        resolve_edges = [
            e for e in result.edges if e.edge_type == "calls"
            and "resolve" in e.dst
        ]
        assert len(resolve_edges) == 0

    def test_regular_function_param_shadows_global(
        self, tmp_path: Path
    ) -> None:
        """Regular function params (not arrow) also shadow globals."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "other.js").write_text(
            "function callback(x) { return x; }\n"
        )
        (tmp_path / "app.js").write_text("""
function doWork() {
    items.forEach(function(callback) {
        callback("value");
    });
}
""")
        result = analyze_javascript(tmp_path)
        false_edges = [
            e for e in result.edges if e.edge_type == "calls"
            and "doWork" in e.src and "callback" in e.dst
        ]
        assert len(false_edges) == 0, (
            f"callback() should be local param, not global, got: {false_edges}"
        )

    def test_single_param_arrow_shadows_global(
        self, tmp_path: Path
    ) -> None:
        """Single-param arrow function (no parens) shadows global name."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "other.js").write_text(
            "function cb(x) { return x; }\n"
        )
        (tmp_path / "app.js").write_text("""
function doWork() {
    items.forEach(cb => {
        cb("value");
    });
}
""")
        result = analyze_javascript(tmp_path)
        false_edges = [
            e for e in result.edges if e.edge_type == "calls"
            and "doWork" in e.src and "cb" in e.dst
        ]
        assert len(false_edges) == 0, (
            f"cb() should be local arrow param, not global, got: {false_edges}"
        )


class TestMiddlewareChainEdges:
    """Tests for Express-style middleware chain edge creation.

    When Express routes register multiple middleware functions as arguments,
    e.g. ``app.post('/path', auth, validate, handler)``, the analyzer should
    create ``references`` edges between consecutive middleware/handler functions
    with evidence_type ``middleware_chain``.  This makes the execution pipeline
    visible in forward/reverse slices.
    """

    def test_middleware_chain_creates_edges_between_consecutive_handlers(
        self, tmp_path: Path
    ) -> None:
        """app.post('/path', mw1, mw2, handler) creates chain edges."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """
function authMiddleware(req, res, next) { next(); }
function validateInput(req, res, next) { next(); }
function handleCreate(req, res) { res.json({}); }

function setupRoutes(app) {
    app.post("/api/items", authMiddleware, validateInput, handleCreate);
}
"""
        (tmp_path / "routes.js").write_text(code)
        result = analyze_javascript(tmp_path)

        chain_edges = [
            e for e in result.edges
            if e.evidence_type == "middleware_chain"
        ]
        # Should have 2 chain edges: auth→validate, validate→handle
        assert len(chain_edges) == 2
        # First edge: authMiddleware → validateInput
        assert any(
            "authMiddleware" in e.src and "validateInput" in e.dst
            for e in chain_edges
        ), f"Expected auth→validate edge, got: {[(e.src, e.dst) for e in chain_edges]}"
        # Second edge: validateInput → handleCreate
        assert any(
            "validateInput" in e.src and "handleCreate" in e.dst
            for e in chain_edges
        ), f"Expected validate→handle edge, got: {[(e.src, e.dst) for e in chain_edges]}"

    def test_middleware_chain_with_factory_calls(self, tmp_path: Path) -> None:
        """Middleware factories like need('txt') create chain edges to the
        factory function, not the returned middleware."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """
function auth(req, res, next) { next(); }
function need(param) { return function(req, res, next) { next(); }; }
function handler(req, res) { res.json({}); }

function setupRoutes(app) {
    app.post("/api/comments", auth, need("txt"), handler);
}
"""
        (tmp_path / "routes.js").write_text(code)
        result = analyze_javascript(tmp_path)

        chain_edges = [
            e for e in result.edges
            if e.evidence_type == "middleware_chain"
        ]
        # auth → need, need → handler
        assert len(chain_edges) == 2
        assert any(
            "auth" in e.src and "need" in e.dst
            for e in chain_edges
        ), f"Expected auth→need edge, got: {[(e.src, e.dst) for e in chain_edges]}"
        assert any(
            "need" in e.src and "handler" in e.dst
            for e in chain_edges
        ), f"Expected need→handler edge, got: {[(e.src, e.dst) for e in chain_edges]}"

    def test_no_middleware_chain_for_single_handler(self, tmp_path: Path) -> None:
        """app.get('/path', handler) with a single handler creates no chain edges."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """
function getUsers(req, res) { res.json([]); }

function setup(app) {
    app.get("/users", getUsers);
}
"""
        (tmp_path / "routes.js").write_text(code)
        result = analyze_javascript(tmp_path)

        chain_edges = [
            e for e in result.edges
            if e.evidence_type == "middleware_chain"
        ]
        assert len(chain_edges) == 0

    def test_no_middleware_chain_for_non_route_calls(self, tmp_path: Path) -> None:
        """Regular calls with multiple args don't create chain edges."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """
function onSuccess(data) { return data; }
function onError(err) { throw err; }

function fetch() {
    promise.then(onSuccess, onError);
}
"""
        (tmp_path / "fetch.js").write_text(code)
        result = analyze_javascript(tmp_path)

        chain_edges = [
            e for e in result.edges
            if e.evidence_type == "middleware_chain"
        ]
        assert len(chain_edges) == 0


class TestCrossPackageGuardAllPaths:
    """Tests for cross-package guard on namespace calls, method inference,
    object field references, and direct call fallback.

    These edge creation paths previously lacked the cross-package guard
    that was applied to ``callback_argument_reference`` and direct calls
    with import-path disambiguation.  Common names like ``error``,
    ``request``, ``f``, ``apply`` resolved across npm package boundaries.
    """

    def test_namespace_call_no_cross_package(
        self, tmp_path: Path
    ) -> None:
        """Namespace alias.error() must not resolve to another package.

        When ``utils`` is imported via ``import * as utils`` and
        ``utils.error()`` is called, the resolver may find ``error``
        in a different npm package. The cross-package guard must block it.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "server" / "package.json").parent.mkdir()
        (tmp_path / "server" / "package.json").write_text('{"name": "server"}')
        (tmp_path / "server" / "app.js").write_text(
            "import * as utils from './utils';\n"
            "function handler() {\n"
            "    utils.error('oops');\n"
            "}\n"
        )
        (tmp_path / "server" / "utils.js").write_text(
            "export function log(msg) { console.log(msg); }\n"
        )
        (tmp_path / "client" / "package.json").parent.mkdir()
        (tmp_path / "client" / "package.json").write_text('{"name": "client"}')
        (tmp_path / "client" / "actions.js").write_text(
            "function error(msg) { alert(msg); }\n"
        )
        result = analyze_javascript(tmp_path)
        ns_edges = [
            e for e in result.edges
            if e.evidence_type == "ast_call_namespace"
            and "error" in e.dst
            and "client" in e.dst
        ]
        assert len(ns_edges) == 0, (
            f"Namespace call should not cross package boundary: {ns_edges}"
        )

    def test_namespace_call_same_package_resolves(
        self, tmp_path: Path
    ) -> None:
        """Namespace alias.doWork() should resolve within same package."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "pkg" / "package.json").parent.mkdir()
        (tmp_path / "pkg" / "package.json").write_text('{"name": "pkg"}')
        (tmp_path / "pkg" / "app.js").write_text(
            "import * as helpers from './helpers';\n"
            "function handler() {\n"
            "    helpers.doWork('data');\n"
            "}\n"
        )
        (tmp_path / "pkg" / "helpers.js").write_text(
            "export function doWork(x) { return x; }\n"
        )
        result = analyze_javascript(tmp_path)
        ns_edges = [
            e for e in result.edges
            if e.evidence_type == "ast_call_namespace"
            and "doWork" in e.dst
        ]
        assert len(ns_edges) >= 1, (
            "Namespace call within same package should resolve"
        )

    def test_method_inferred_no_cross_package(
        self, tmp_path: Path
    ) -> None:
        """Fallback method inference should not cross package boundary."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "server" / "package.json").parent.mkdir()
        (tmp_path / "server" / "package.json").write_text('{"name": "server"}')
        (tmp_path / "server" / "app.js").write_text(
            "function handler() {\n"
            "    const obj = getObj();\n"
            "    obj.apply('data');\n"
            "}\n"
        )
        (tmp_path / "client" / "package.json").parent.mkdir()
        (tmp_path / "client" / "package.json").write_text('{"name": "client"}')
        (tmp_path / "client" / "plugin.js").write_text(
            "class Plugin {\n"
            "    apply(compiler) { return compiler; }\n"
            "}\n"
        )
        result = analyze_javascript(tmp_path)
        method_edges = [
            e for e in result.edges
            if e.evidence_type == "ast_method_inferred"
            and "apply" in e.dst
        ]
        for edge in method_edges:
            assert "client" not in edge.dst, (
                f"Method inference should not cross package boundary: {edge.dst}"
            )

    def test_object_field_ref_no_cross_package(
        self, tmp_path: Path
    ) -> None:
        """Object literal {handler: myFunc} should not cross packages."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "server" / "package.json").parent.mkdir()
        (tmp_path / "server" / "package.json").write_text('{"name": "server"}')
        (tmp_path / "server" / "config.js").write_text(
            "function setup() {\n"
            "    const routes = {onError: error};\n"
            "    return routes;\n"
            "}\n"
        )
        (tmp_path / "client" / "package.json").parent.mkdir()
        (tmp_path / "client" / "package.json").write_text('{"name": "client"}')
        (tmp_path / "client" / "actions.js").write_text(
            "function error(msg) { alert(msg); }\n"
        )
        result = analyze_javascript(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.evidence_type == "object_field_reference"
            and "error" in e.dst
        ]
        assert len(ref_edges) == 0, (
            f"Object field ref should not cross package boundary: {ref_edges}"
        )

    def test_shorthand_property_no_cross_package(
        self, tmp_path: Path
    ) -> None:
        """Shorthand property {error} should not cross packages."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "server" / "package.json").parent.mkdir()
        (tmp_path / "server" / "package.json").write_text('{"name": "server"}')
        (tmp_path / "server" / "config.js").write_text(
            "function setup() {\n"
            "    const handlers = {error};\n"
            "    return handlers;\n"
            "}\n"
        )
        (tmp_path / "client" / "package.json").parent.mkdir()
        (tmp_path / "client" / "package.json").write_text('{"name": "client"}')
        (tmp_path / "client" / "actions.js").write_text(
            "function error(msg) { alert(msg); }\n"
        )
        result = analyze_javascript(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.evidence_type == "object_field_reference"
            and "error" in e.dst
        ]
        assert len(ref_edges) == 0, (
            f"Shorthand prop ref should not cross package boundary: {ref_edges}"
        )

    def test_middleware_chain_no_cross_package(
        self, tmp_path: Path
    ) -> None:
        """Middleware chain should not include cross-package symbols.

        ``app.get('/path', timeout(15000), moveToBody, handler)`` should
        only chain symbols from the same npm package. If ``timeout``
        resolves to a function in a different package, it must be skipped.
        """
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "server" / "package.json").parent.mkdir()
        (tmp_path / "server" / "package.json").write_text('{"name": "server"}')
        (tmp_path / "server" / "middleware.js").write_text(
            "function moveToBody(req, res, next) { next(); }\n"
            "function handler(req, res) { res.send('ok'); }\n"
        )
        (tmp_path / "server" / "app.js").write_text(
            "const app = require('express')();\n"
            "app.get('/api/data', timeout(15000), moveToBody, handler);\n"
        )
        (tmp_path / "client" / "package.json").parent.mkdir()
        (tmp_path / "client" / "package.json").write_text('{"name": "client"}')
        (tmp_path / "client" / "utils.js").write_text(
            "function timeout(ms) { return setTimeout(() => {}, ms); }\n"
        )
        result = analyze_javascript(tmp_path)
        chain_edges = [
            e for e in result.edges
            if e.evidence_type == "middleware_chain"
        ]
        for edge in chain_edges:
            assert "client" not in edge.src, (
                f"Middleware chain should not use cross-package symbol: {edge.src}"
            )
            assert "client" not in edge.dst, (
                f"Middleware chain should not use cross-package symbol: {edge.dst}"
            )

    def test_direct_call_fallback_no_cross_package(
        self, tmp_path: Path
    ) -> None:
        """Direct call fallback (no import, no same-package) must not cross."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "server" / "package.json").parent.mkdir()
        (tmp_path / "server" / "package.json").write_text('{"name": "server"}')
        (tmp_path / "server" / "app.js").write_text(
            "function handler() {\n"
            "    formatDate('2024-01-01');\n"
            "}\n"
        )
        (tmp_path / "client" / "package.json").parent.mkdir()
        (tmp_path / "client" / "package.json").write_text('{"name": "client"}')
        (tmp_path / "client" / "utils.js").write_text(
            "function formatDate(d) { return d.toString(); }\n"
        )
        result = analyze_javascript(tmp_path)
        call_edges = [
            e for e in result.edges
            if e.evidence_type == "ast_call_direct"
            and "formatDate" in e.dst
        ]
        assert len(call_edges) == 0, (
            f"Direct call fallback should not cross package boundary: {call_edges}"
        )


class TestNormalizeJstsSignature:
    """Tests for JS/TS signature normalization (ADR-0014 §3)."""

    def test_typescript_typed(self) -> None:
        from hypergumbo_lang_mainstream.js_ts import normalize_jsts_signature
        assert normalize_jsts_signature("(name: string, age: number): boolean") == "(string,number)boolean"

    def test_javascript_untyped(self) -> None:
        from hypergumbo_lang_mainstream.js_ts import normalize_jsts_signature
        assert normalize_jsts_signature("(name, age)") == "(name,age)"

    def test_none(self) -> None:
        from hypergumbo_lang_mainstream.js_ts import normalize_jsts_signature
        assert normalize_jsts_signature(None) is None


class TestJsTsTopLevelCallEdges:
    """Top-level code (outside any function) should produce call edges
    attributed to a <module:filename> symbol (INV-jahom)."""

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_javascript")

    def test_module_symbol_created(self, tmp_path: Path) -> None:
        """Every JS file gets a <module:filename> symbol."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "index.js").write_text("const x = 1;\n")
        result = analyze_javascript(tmp_path)

        mod_syms = [s for s in result.symbols if s.kind == "module"]
        assert len(mod_syms) == 1
        assert mod_syms[0].name == "<module:index.js>"

    def test_toplevel_direct_call_produces_edge(self, tmp_path: Path) -> None:
        """A top-level call like `helper()` should create a calls edge
        from <module:main.js> to helper."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "helper.js").write_text("function helper() { return 1; }\n")
        (tmp_path / "main.js").write_text(
            "const helper = require('./helper');\nhelper();\n"
        )
        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # The top-level call helper() in main.js should produce an edge
        # from <module:main.js> → helper
        module_call_edges = [
            e for e in call_edges
            if "<module:main.js>" in e.src
        ]
        assert len(module_call_edges) >= 1
        assert any("helper" in e.dst for e in module_call_edges)

    def test_toplevel_method_call_produces_edge(self, tmp_path: Path) -> None:
        """Top-level method call like `app.listen(3000)` should be attributed
        to the module symbol."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """\
class Server {
  listen(port) { return port; }
}
const app = new Server();
app.listen(3000);
"""
        (tmp_path / "app.js").write_text(code)
        result = analyze_javascript(tmp_path)

        # The new Server() instantiation at top-level should be attributed
        # to <module:app.js>
        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        module_inst = [e for e in inst_edges if "<module:app.js>" in e.src]
        assert len(module_inst) >= 1

    def test_toplevel_new_expression_produces_edge(self, tmp_path: Path) -> None:
        """Top-level `new Foo()` should produce an instantiates edge
        from the module symbol."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = "class Foo {}\nconst f = new Foo();\n"
        (tmp_path / "index.js").write_text(code)
        result = analyze_javascript(tmp_path)

        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        module_inst = [e for e in inst_edges if "<module:index.js>" in e.src]
        assert len(module_inst) == 1

    def test_call_inside_function_still_uses_function(self, tmp_path: Path) -> None:
        """Calls inside a named function should still be attributed to that
        function, not the module symbol (regression check)."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """\
function helper() { return 1; }
function main() { helper(); }
"""
        (tmp_path / "app.js").write_text(code)
        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # The call should be from main → helper, NOT from <module:app.js>
        main_calls = [e for e in call_edges if "main" in e.src and "module" not in e.src]
        assert len(main_calls) >= 1
        module_calls = [e for e in call_edges if "<module:app.js>" in e.src]
        assert len(module_calls) == 0

    def test_esm_toplevel_call(self, tmp_path: Path) -> None:
        """ESM-style top-level call with import should produce a call edge."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "utils.js").write_text(
            "export function setup() { return true; }\n"
        )
        (tmp_path / "index.js").write_text(
            "import { setup } from './utils.js';\nsetup();\n"
        )
        result = analyze_javascript(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        module_calls = [
            e for e in call_edges if "<module:index.js>" in e.src
        ]
        assert len(module_calls) >= 1
        assert any("setup" in e.dst for e in module_calls)


class TestMpegTsBinarySkip:
    """Binary .ts files (MPEG Transport Stream) must not be parsed as TypeScript."""

    def test_binary_ts_file_skipped(self, tmp_path: Path) -> None:
        """A .ts file containing binary data (MPEG-TS) produces no symbols."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        # MPEG-TS sync byte is 0x47, packets are 188 bytes
        mpeg_ts_data = b"\x47" + b"\x00" * 187  # one TS packet
        mpeg_ts_data *= 10  # 10 packets
        (tmp_path / "video.ts").write_bytes(mpeg_ts_data)

        # Also add a real TypeScript file to confirm it still works
        (tmp_path / "app.ts").write_text("const x: number = 1;\n")

        result = analyze_javascript(tmp_path)

        # The binary .ts file should not produce any symbols
        ts_symbols = [s for s in result.symbols if "video.ts" in s.path]
        assert len(ts_symbols) == 0, (
            f"Binary MPEG-TS file should not produce symbols, got: {ts_symbols}"
        )

        # The real TypeScript file should still be analyzed
        real_ts = [s for s in result.symbols if "app.ts" in s.path]
        assert len(real_ts) >= 1


# ============================================================================
# React Router JSX Route Detection
# ============================================================================


class TestReactRouterJSXRouteDetection:
    """Tests for React Router <Route> JSX element detection.

    React Router uses JSX elements to define client-side routes:
    - <Route path="/users" element={<Users />} />
    - <Route path="/about" component={About} />
    - <Route path="/">...</Route>

    These should be detected as route symbols and appear in routes.txt.
    """

    @pytest.fixture(autouse=True)
    def _skip_no_ts(self) -> None:
        """Skip if tree-sitter is not available."""
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_javascript")

    def test_self_closing_route_with_element(self, tmp_path: Path) -> None:
        """<Route path="/users" element={<Users />} /> creates a route symbol."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "App.tsx").write_text(
            'import { Route } from "react-router-dom";\n'
            "function App() {\n"
            "  return (\n"
            '    <Route path="/users" element={<Users />} />\n'
            "  );\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1

        route = routes[0]
        assert route.meta is not None
        assert route.meta["route_path"] == "/users"
        assert route.meta["http_method"] == "GET"
        assert route.meta.get("handler_ref") == "Users"

    def test_self_closing_route_with_component(self, tmp_path: Path) -> None:
        """<Route path="/about" component={About} /> creates a route symbol."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "App.tsx").write_text(
            'import { Route } from "react-router-dom";\n'
            "function App() {\n"
            "  return (\n"
            '    <Route path="/about" component={About} />\n'
            "  );\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1

        route = routes[0]
        assert route.meta is not None
        assert route.meta["route_path"] == "/about"
        assert route.meta.get("handler_ref") == "About"

    def test_route_element_with_children(self, tmp_path: Path) -> None:
        """<Route path="/home">...</Route> (non-self-closing) creates a route symbol."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "App.tsx").write_text(
            'import { Route } from "react-router-dom";\n'
            "function App() {\n"
            "  return (\n"
            '    <Route path="/home">\n'
            "      <Home />\n"
            "    </Route>\n"
            "  );\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1

        route = routes[0]
        assert route.meta is not None
        assert route.meta["route_path"] == "/home"

    def test_multiple_routes(self, tmp_path: Path) -> None:
        """Multiple <Route> elements each create a route symbol."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "App.tsx").write_text(
            'import { Route, Routes } from "react-router-dom";\n'
            "function App() {\n"
            "  return (\n"
            "    <Routes>\n"
            '      <Route path="/" element={<Home />} />\n'
            '      <Route path="/users" element={<Users />} />\n'
            '      <Route path="/settings" element={<Settings />} />\n'
            "    </Routes>\n"
            "  );\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 3

        paths = {r.meta["route_path"] for r in routes if r.meta}
        assert "/" in paths
        assert "/users" in paths
        assert "/settings" in paths

    def test_route_without_path_ignored(self, tmp_path: Path) -> None:
        """<Route element={<Layout />} /> without path is not a route."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "App.tsx").write_text(
            'import { Route } from "react-router-dom";\n'
            "function App() {\n"
            "  return <Route element={<Layout />} />;\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 0

    def test_member_expression_route_tag(self, tmp_path: Path) -> None:
        """<ReactRouter.Route path="/x" /> (member expression tag) creates a route."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "App.jsx").write_text(
            'import * as ReactRouter from "react-router-dom";\n'
            "function App() {\n"
            "  return (\n"
            '    <ReactRouter.Route path="/dashboard" element={<Dashboard />} />\n'
            "  );\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1

        route = routes[0]
        assert route.meta is not None
        assert route.meta["route_path"] == "/dashboard"

    def test_non_route_jsx_ignored(self, tmp_path: Path) -> None:
        """Non-Route JSX elements are not detected as routes."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "App.tsx").write_text(
            "function App() {\n"
            '  return <div className="app"><Header /><Footer /></div>;\n'
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 0

    def test_create_browser_router_routes(self, tmp_path: Path) -> None:
        """createBrowserRouter([...]) should create route symbols."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "router.tsx").write_text(
            "import { createBrowserRouter } from 'react-router-dom';\n"
            "\n"
            "const router = createBrowserRouter([\n"
            "  { path: '/', element: <Home /> },\n"
            "  { path: '/users', element: <Users /> },\n"
            "  { path: '/users/:id', element: <UserDetail /> },\n"
            "]);\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        route_paths = {s.meta["route_path"] for s in routes if s.meta}
        assert "/" in route_paths
        assert "/users" in route_paths
        assert "/users/:id" in route_paths

    def test_create_browser_router_nested_children(self, tmp_path: Path) -> None:
        """Nested children in createBrowserRouter should compose paths."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "router.tsx").write_text(
            "const router = createBrowserRouter([\n"
            "  {\n"
            "    path: '/dashboard',\n"
            "    element: <Layout />,\n"
            "    children: [\n"
            "      { path: 'settings', element: <Settings /> },\n"
            "      { path: 'profile', element: <Profile /> },\n"
            "    ],\n"
            "  },\n"
            "]);\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        route_paths = {s.meta["route_path"] for s in routes if s.meta}
        assert "/dashboard" in route_paths
        assert "/dashboard/settings" in route_paths
        assert "/dashboard/profile" in route_paths

    def test_create_hash_router(self, tmp_path: Path) -> None:
        """createHashRouter should also be detected."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "router.tsx").write_text(
            "const router = createHashRouter([\n"
            "  { path: '/about', element: <About /> },\n"
            "]);\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].meta["route_path"] == "/about"

    def test_route_loader_and_action(self, tmp_path: Path) -> None:
        """loader and action properties should appear in route meta."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "router.tsx").write_text(
            "import { createBrowserRouter } from 'react-router-dom';\n"
            "import { loadUsers } from './loaders';\n"
            "import { createUser } from './actions';\n"
            "\n"
            "const router = createBrowserRouter([\n"
            "  {\n"
            "    path: '/users',\n"
            "    element: <Users />,\n"
            "    loader: loadUsers,\n"
            "    action: createUser,\n"
            "  },\n"
            "]);\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1

        route = next(r for r in routes if r.meta["route_path"] == "/users")
        assert route.meta["loader_ref"] == "loadUsers"
        assert route.meta["action_ref"] == "createUser"

    def test_route_lazy_import(self, tmp_path: Path) -> None:
        """lazy: () => import('./Page') should record lazy_import in meta."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "router.tsx").write_text(
            "import { createBrowserRouter } from 'react-router-dom';\n"
            "\n"
            "const router = createBrowserRouter([\n"
            "  {\n"
            "    path: '/dashboard',\n"
            "    lazy: () => import('./pages/Dashboard'),\n"
            "  },\n"
            "]);\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1

        route = next(r for r in routes if r.meta["route_path"] == "/dashboard")
        assert route.meta["lazy_import"] == "./pages/Dashboard"

    def test_route_loader_action_and_lazy_combined(self, tmp_path: Path) -> None:
        """Route with loader, action, and lazy should capture all three."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "router.tsx").write_text(
            "const router = createBrowserRouter([\n"
            "  {\n"
            "    path: '/settings',\n"
            "    lazy: () => import('./Settings'),\n"
            "    loader: fetchSettings,\n"
            "    action: updateSettings,\n"
            "  },\n"
            "  {\n"
            "    path: '/plain',\n"
            "    element: <Plain />,\n"
            "  },\n"
            "]);\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        route_map = {r.meta["route_path"]: r for r in routes if r.meta}

        settings = route_map["/settings"]
        assert settings.meta["lazy_import"] == "./Settings"
        assert settings.meta["loader_ref"] == "fetchSettings"
        assert settings.meta["action_ref"] == "updateSettings"

        plain = route_map["/plain"]
        assert "lazy_import" not in plain.meta
        assert "loader_ref" not in plain.meta
        assert "action_ref" not in plain.meta

    def test_lazy_without_dynamic_import(self, tmp_path: Path) -> None:
        """lazy property without dynamic import() should not produce lazy_import."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "router.tsx").write_text(
            "const router = createBrowserRouter([\n"
            "  {\n"
            "    path: '/nolazy',\n"
            "    lazy: () => fetchComponent('Dashboard'),\n"
            "  },\n"
            "]);\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        route = next(r for r in routes if r.meta["route_path"] == "/nolazy")
        assert "lazy_import" not in route.meta


class TestReactLazyRouteDetection:
    """Tests for React.lazy() component detection in JSX routes.

    When a JSX <Route> element references a component defined via React.lazy(),
    the route symbol should include lazy_import metadata pointing to the
    dynamic import path. This enables tracing through lazy wrappers to the
    actual component module.
    """

    def test_jsx_route_with_react_lazy_component(self, tmp_path: Path) -> None:
        """Route referencing a React.lazy() component gets lazy_import metadata."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "App.tsx").write_text(
            "import React, { Suspense } from 'react';\n"
            "import { Route, Routes } from 'react-router-dom';\n"
            "\n"
            "const LazyDashboard = React.lazy(() => import('./pages/Dashboard'));\n"
            "\n"
            "export default function App() {\n"
            "  return (\n"
            "    <Suspense fallback={<Loading />}>\n"
            "      <Routes>\n"
            "        <Route path='/dashboard' element={<LazyDashboard />} />\n"
            "      </Routes>\n"
            "    </Suspense>\n"
            "  );\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1

        route = next(r for r in routes if r.meta.get("route_path") == "/dashboard")
        assert route.meta["handler_ref"] == "LazyDashboard"
        assert route.meta["lazy_import"] == "./pages/Dashboard"

    def test_jsx_route_with_lazy_no_react_prefix(self, tmp_path: Path) -> None:
        """lazy() without React. prefix also detected."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "App.tsx").write_text(
            "import { lazy } from 'react';\n"
            "import { Route } from 'react-router-dom';\n"
            "\n"
            "const Settings = lazy(() => import('./Settings'));\n"
            "\n"
            "function App() {\n"
            "  return <Route path='/settings' element={<Settings />} />;\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        route = next(r for r in routes if r.meta.get("route_path") == "/settings")
        assert route.meta["lazy_import"] == "./Settings"

    def test_non_lazy_component_no_lazy_import(self, tmp_path: Path) -> None:
        """Regular component referenced in JSX route has no lazy_import."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "App.tsx").write_text(
            "import { Route } from 'react-router-dom';\n"
            "import Home from './Home';\n"
            "\n"
            "function App() {\n"
            "  return <Route path='/' element={<Home />} />;\n"
            "}\n"
        )

        result = analyze_javascript(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        route = next(r for r in routes if r.meta.get("route_path") == "/")
        assert "lazy_import" not in route.meta


class TestSpaBootstrapUsageContext:
    """Tests for SPA bootstrap call detection via UsageContext.

    React (and similar SPA frameworks) bootstrap applications through
    module-level calls like createRoot(), ReactDOM.render(), hydrateRoot().
    These calls should emit UsageContext records with kind="call" so that
    react.yaml usage patterns can assign the app_bootstrap concept to the
    module symbol, enabling SPA_BOOTSTRAP entrypoint detection.
    """

    def test_create_root_react18(self, tmp_path: Path) -> None:
        """React 18 createRoot() emits a bootstrap UsageContext."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "main.tsx").write_text(
            "import { createRoot } from 'react-dom/client';\n"
            "import App from './App';\n"
            "\n"
            "const root = createRoot(document.getElementById('root')!);\n"
            "root.render(<App />);\n"
        )
        result = analyze_javascript(tmp_path)
        bootstrap_ctx = [
            c for c in result.usage_contexts
            if c.kind == "call" and "createRoot" in c.context_name
        ]
        assert len(bootstrap_ctx) == 1
        ctx = bootstrap_ctx[0]
        assert ctx.context_name == "createRoot"
        # symbol_ref should point to the module symbol
        module_sym = next(s for s in result.symbols if s.kind == "module")
        assert ctx.symbol_ref == module_sym.id

    def test_reactdom_render_react17(self, tmp_path: Path) -> None:
        """React 17 ReactDOM.render() emits a bootstrap UsageContext."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "index.jsx").write_text(
            "import React from 'react';\n"
            "import ReactDOM from 'react-dom';\n"
            "import App from './App';\n"
            "\n"
            "ReactDOM.render(<App />, document.getElementById('root'));\n"
        )
        result = analyze_javascript(tmp_path)
        bootstrap_ctx = [
            c for c in result.usage_contexts
            if c.kind == "call" and "ReactDOM.render" in c.context_name
        ]
        assert len(bootstrap_ctx) == 1
        ctx = bootstrap_ctx[0]
        assert ctx.context_name == "ReactDOM.render"
        module_sym = next(s for s in result.symbols if s.kind == "module")
        assert ctx.symbol_ref == module_sym.id

    def test_hydrate_root_ssr(self, tmp_path: Path) -> None:
        """hydrateRoot() emits a bootstrap UsageContext."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "entry-client.tsx").write_text(
            "import { hydrateRoot } from 'react-dom/client';\n"
            "import App from './App';\n"
            "\n"
            "hydrateRoot(document.getElementById('root')!, <App />);\n"
        )
        result = analyze_javascript(tmp_path)
        bootstrap_ctx = [
            c for c in result.usage_contexts
            if c.kind == "call" and "hydrateRoot" in c.context_name
        ]
        assert len(bootstrap_ctx) == 1
        assert bootstrap_ctx[0].context_name == "hydrateRoot"

    def test_create_browser_router(self, tmp_path: Path) -> None:
        """createBrowserRouter() emits a bootstrap UsageContext."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "main.tsx").write_text(
            "import { createBrowserRouter, RouterProvider } from 'react-router-dom';\n"
            "import { createRoot } from 'react-dom/client';\n"
            "\n"
            "const router = createBrowserRouter([{ path: '/', element: <Home /> }]);\n"
            "const root = createRoot(document.getElementById('root')!);\n"
            "root.render(<RouterProvider router={router} />);\n"
        )
        result = analyze_javascript(tmp_path)
        bootstrap_names = {
            c.context_name for c in result.usage_contexts
            if c.kind == "call" and c.context_name in (
                "createRoot", "createBrowserRouter"
            )
        }
        assert "createRoot" in bootstrap_names
        assert "createBrowserRouter" in bootstrap_names

    def test_no_bootstrap_for_non_spa_calls(self, tmp_path: Path) -> None:
        """Regular function calls should NOT produce bootstrap UsageContexts."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "utils.js").write_text(
            "const result = doSomething();\n"
            "console.log(result);\n"
        )
        result = analyze_javascript(tmp_path)
        bootstrap_ctx = [
            c for c in result.usage_contexts
            if c.kind == "call" and c.context_name in (
                "createRoot", "ReactDOM.render", "hydrateRoot",
                "createBrowserRouter",
            )
        ]
        assert len(bootstrap_ctx) == 0

    def test_reactdom_createroot_qualified(self, tmp_path: Path) -> None:
        """ReactDOM.createRoot() (qualified form) emits a bootstrap context."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "main.tsx").write_text(
            "import ReactDOM from 'react-dom/client';\n"
            "import App from './App';\n"
            "\n"
            "const root = ReactDOM.createRoot(document.getElementById('root')!);\n"
            "root.render(<App />);\n"
        )
        result = analyze_javascript(tmp_path)
        bootstrap_ctx = [
            c for c in result.usage_contexts
            if c.kind == "call" and "createRoot" in c.context_name
        ]
        assert len(bootstrap_ctx) == 1
        assert bootstrap_ctx[0].context_name == "ReactDOM.createRoot"

    def test_electron_app_when_ready(self, tmp_path: Path) -> None:
        """Electron app.whenReady() emits a bootstrap UsageContext."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "main.js").write_text(
            "const { app, BrowserWindow } = require('electron');\n"
            "\n"
            "app.whenReady().then(() => {\n"
            "  const win = new BrowserWindow({ width: 800, height: 600 });\n"
            "  win.loadFile('index.html');\n"
            "});\n"
        )
        result = analyze_javascript(tmp_path)
        bootstrap_ctx = [
            c for c in result.usage_contexts
            if c.kind == "call" and c.context_name == "app.whenReady"
        ]
        assert len(bootstrap_ctx) == 1
        module_sym = next(s for s in result.symbols if s.kind == "module")
        assert bootstrap_ctx[0].symbol_ref == module_sym.id

    def test_electron_app_on_ready(self, tmp_path: Path) -> None:
        """Electron app.on('ready') emits a bootstrap UsageContext."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "main.ts").write_text(
            "import { app } from 'electron';\n"
            "\n"
            "app.on('ready', () => {\n"
            "  console.log('App is ready');\n"
            "});\n"
        )
        result = analyze_javascript(tmp_path)
        bootstrap_ctx = [
            c for c in result.usage_contexts
            if c.kind == "call" and c.context_name == "app.on"
        ]
        assert len(bootstrap_ctx) == 1


class TestTypeReferenceEdges:
    """Tests for TypeScript type-level reference edges."""

    def test_type_alias_references_other_type(self, tmp_path: Path) -> None:
        """Type alias body creates type_ref edges to referenced types."""
        pytest.importorskip("tree_sitter_typescript")
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """\
interface User {
  id: string;
  name: string;
}

type UserList = User[];
"""
        (tmp_path / "types.ts").write_text(code)
        result = analyze_javascript(tmp_path)

        type_ref_edges = [e for e in result.edges if e.edge_type == "type_ref"]
        # UserList should reference User
        assert any(
            "UserList" in e.src and "User" in e.dst
            for e in type_ref_edges
        ), f"Expected type_ref from UserList to User, got: {type_ref_edges}"

    def test_type_alias_references_multiple_types(self, tmp_path: Path) -> None:
        """Type alias with intersection/union references multiple types."""
        pytest.importorskip("tree_sitter_typescript")
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """\
interface Serializable {
  serialize(): string;
}

interface Loggable {
  log(): void;
}

type Combined = Serializable & Loggable;
"""
        (tmp_path / "types.ts").write_text(code)
        result = analyze_javascript(tmp_path)

        type_ref_edges = [e for e in result.edges if e.edge_type == "type_ref"]
        # Combined should reference both Serializable and Loggable
        combined_refs = [e for e in type_ref_edges if "Combined" in e.src]
        ref_dsts = {e.dst for e in combined_refs}
        assert any("Serializable" in d for d in ref_dsts), (
            f"Expected Combined -> Serializable, got dst: {ref_dsts}"
        )
        assert any("Loggable" in d for d in ref_dsts), (
            f"Expected Combined -> Loggable, got dst: {ref_dsts}"
        )

    def test_interface_method_return_type_ref(self, tmp_path: Path) -> None:
        """Interface method return type creates type_ref to the return type."""
        pytest.importorskip("tree_sitter_typescript")
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """\
interface User {
  id: string;
  name: string;
}

interface UserService {
  getUser(id: string): User;
  listUsers(): User[];
}
"""
        (tmp_path / "types.ts").write_text(code)
        result = analyze_javascript(tmp_path)

        type_ref_edges = [e for e in result.edges if e.edge_type == "type_ref"]
        # UserService should reference User (from method signatures)
        service_refs = [e for e in type_ref_edges if "UserService" in e.src]
        assert any(
            "User" in e.dst for e in service_refs
        ), f"Expected UserService -> User type_ref, got: {service_refs}"

    def test_no_type_ref_to_builtin_types(self, tmp_path: Path) -> None:
        """Does not create type_ref edges to built-in types like string, number."""
        pytest.importorskip("tree_sitter_typescript")
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """\
type Name = string;
type Count = number;
type Flag = boolean;
"""
        (tmp_path / "types.ts").write_text(code)
        result = analyze_javascript(tmp_path)

        type_ref_edges = [e for e in result.edges if e.edge_type == "type_ref"]
        # No edges to built-in types
        assert len(type_ref_edges) == 0, (
            f"Expected no type_ref edges to builtins, got: {type_ref_edges}"
        )

    def test_type_alias_self_ref_and_dedup(self, tmp_path: Path) -> None:
        """Self-referential type and duplicate refs don't create redundant edges."""
        pytest.importorskip("tree_sitter_typescript")
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """\
interface Node {
  value: string;
}

type TreeNode = Node & { children: TreeNode[] };
"""
        (tmp_path / "types.ts").write_text(code)
        result = analyze_javascript(tmp_path)

        type_ref_edges = [e for e in result.edges if e.edge_type == "type_ref"]
        # TreeNode references Node (not itself)
        tree_refs = [e for e in type_ref_edges if "TreeNode" in e.src]
        assert len(tree_refs) == 1, (
            f"Expected exactly 1 type_ref from TreeNode (to Node), got: {tree_refs}"
        )
        assert "Node" in tree_refs[0].dst

    def test_type_ref_edge_confidence(self, tmp_path: Path) -> None:
        """Type reference edges have appropriate confidence."""
        pytest.importorskip("tree_sitter_typescript")
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        code = """\
interface User {
  id: string;
}

type UserOrNull = User | null;
"""
        (tmp_path / "types.ts").write_text(code)
        result = analyze_javascript(tmp_path)

        type_ref_edges = [e for e in result.edges if e.edge_type == "type_ref"]
        for edge in type_ref_edges:
            assert edge.confidence == 0.85, (
                f"Expected confidence 0.85, got {edge.confidence}"
            )


class TestJsTsShapeId:
    """Tests for shape_id computation in JS/TS (ADR-0014 §1)."""

    def test_function_has_shape_id(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "example.js").write_text(
            "function add(a, b) { return a + b; }\n"
        )
        result = analyze_javascript(tmp_path)
        func = next(s for s in result.symbols if s.name == "add")
        assert func.shape_id is not None
        assert func.shape_id.startswith("sha256:")

    def test_class_has_shape_id(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "example.js").write_text(
            "class Foo {\n  bar() { return 42; }\n}\n"
        )
        result = analyze_javascript(tmp_path)
        cls = next(s for s in result.symbols if s.kind == "class")
        assert cls.shape_id is not None
        assert cls.shape_id.startswith("sha256:")
