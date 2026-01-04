"""Tests for OCaml analyzer.

OCaml analysis uses tree-sitter to extract:
- Symbols: function (let bindings), type, module
- Edges: calls, imports (open statements)

Test coverage includes:
- Function detection (let bindings)
- Type detection
- Module detection
- Open statements (imports)
- Function calls (application expressions)
- Two-pass cross-file resolution
"""
from pathlib import Path




def make_ocaml_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create an OCaml file with given content."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


class TestOCamlAnalyzerAvailability:
    """Tests for tree-sitter-ocaml availability detection."""

    def test_is_ocaml_tree_sitter_available(self) -> None:
        """Check if tree-sitter-ocaml is detected."""
        from hypergumbo.analyze.ocaml import is_ocaml_tree_sitter_available

        # Should be True since we installed tree-sitter-ocaml
        assert is_ocaml_tree_sitter_available() is True


class TestOCamlFunctionDetection:
    """Tests for OCaml function symbol extraction."""

    def test_detect_simple_function(self, tmp_path: Path) -> None:
        """Detect simple let binding function."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "main.ml", """
let add x y = x + y
""")

        result = analyze_ocaml(tmp_path)

        assert not result.skipped
        symbols = result.symbols
        func = next((s for s in symbols if s.name == "add"), None)
        assert func is not None
        assert func.kind == "function"
        assert func.language == "ocaml"

    def test_detect_function_with_unit_param(self, tmp_path: Path) -> None:
        """Detect function with unit parameter (main)."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "main.ml", """
let main () =
  print_endline "Hello"
""")

        result = analyze_ocaml(tmp_path)

        symbols = result.symbols
        func = next((s for s in symbols if s.name == "main"), None)
        assert func is not None
        assert func.kind == "function"


class TestOCamlTypeDetection:
    """Tests for OCaml type symbol extraction."""

    def test_detect_record_type(self, tmp_path: Path) -> None:
        """Detect record type definition."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "types.ml", """
type person = { name : string; age : int }
""")

        result = analyze_ocaml(tmp_path)

        symbols = result.symbols
        dtype = next((s for s in symbols if s.name == "person"), None)
        assert dtype is not None
        assert dtype.kind == "type"

    def test_detect_variant_type(self, tmp_path: Path) -> None:
        """Detect variant type definition."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "types.ml", """
type color = Red | Green | Blue
""")

        result = analyze_ocaml(tmp_path)

        symbols = result.symbols
        dtype = next((s for s in symbols if s.name == "color"), None)
        assert dtype is not None
        assert dtype.kind == "type"


class TestOCamlModuleDetection:
    """Tests for OCaml module symbol extraction."""

    def test_detect_module(self, tmp_path: Path) -> None:
        """Detect module definition."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "lib.ml", """
module MyUtils = struct
  let helper x = x + 1
end
""")

        result = analyze_ocaml(tmp_path)

        symbols = result.symbols
        mod = next((s for s in symbols if s.name == "MyUtils"), None)
        assert mod is not None
        assert mod.kind == "module"


class TestOCamlImportEdges:
    """Tests for OCaml import edge extraction."""

    def test_detect_open_statement(self, tmp_path: Path) -> None:
        """Detect open statement (import)."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "main.ml", """
open List

let main () = length [1; 2; 3]
""")

        result = analyze_ocaml(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert any("List" in e.dst for e in import_edges)


class TestOCamlCallEdges:
    """Tests for OCaml function call edge extraction."""

    def test_detect_function_call(self, tmp_path: Path) -> None:
        """Detect function call edges."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "main.ml", """
let greet name = "Hello " ^ name

let main () = greet "World"
""")

        result = analyze_ocaml(tmp_path)

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        # main calls greet
        assert any("greet" in e.dst for e in call_edges)


class TestOCamlCrossFileResolution:
    """Tests for two-pass cross-file call resolution."""

    def test_cross_file_call(self, tmp_path: Path) -> None:
        """Calls to functions in other files are resolved."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "utils.ml", """
let helper x = x + 1
""")

        make_ocaml_file(tmp_path, "main.ml", """
open Utils

let main () = helper 5
""")

        result = analyze_ocaml(tmp_path)

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]

        # Call to helper should be resolved
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1


class TestOCamlEdgeCases:
    """Edge case tests for OCaml analyzer."""

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty OCaml file produces no symbols."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "empty.ml", "")

        result = analyze_ocaml(tmp_path)

        assert not result.skipped
        # Only file symbol should exist
        symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(symbols) == 0

    def test_syntax_error_file(self, tmp_path: Path) -> None:
        """File with syntax error is handled gracefully."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "bad.ml", """
let foo x =
    (* missing body *)
""")

        result = analyze_ocaml(tmp_path)

        # Should not crash
        assert not result.skipped

    def test_no_ocaml_files(self, tmp_path: Path) -> None:
        """Directory with no OCaml files returns empty result."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "main.py", "print('hello')")

        result = analyze_ocaml(tmp_path)

        assert not result.skipped
        symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(symbols) == 0


class TestOCamlSpanAccuracy:
    """Tests for accurate source location tracking."""

    def test_function_span(self, tmp_path: Path) -> None:
        """Function span includes full definition."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "main.ml", """let add x y = x + y
""")

        result = analyze_ocaml(tmp_path)

        symbols = result.symbols
        func = next((s for s in symbols if s.name == "add"), None)
        assert func is not None
        assert func.span.start_line == 1


class TestOCamlAnalyzeFallback:
    """Tests for fallback when tree-sitter-ocaml is unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path, monkeypatch) -> None:
        """Returns skipped result when tree-sitter-ocaml not available."""
        from hypergumbo.analyze import ocaml

        # Mock tree-sitter-ocaml as unavailable
        monkeypatch.setattr(ocaml, "is_ocaml_tree_sitter_available", lambda: False)

        make_ocaml_file(tmp_path, "main.ml", "let main () = 1")

        result = ocaml.analyze_ocaml(tmp_path)

        assert result.skipped
        assert "tree-sitter-ocaml" in result.skip_reason
        # Run should still be created for provenance tracking
        assert result.run is not None
        assert result.run.pass_id == "ocaml-v1"


class TestOCamlSignatureExtraction:
    """Tests for OCaml function signature extraction."""

    def test_simple_function_signature(self, tmp_path: Path) -> None:
        """Extract signature from simple let binding with params."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "main.ml", "let add x y = x + y")
        result = analyze_ocaml(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x, y)"

    def test_single_param(self, tmp_path: Path) -> None:
        """Extract signature from function with single param."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "main.ml", "let double x = x * 2")
        result = analyze_ocaml(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "double"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x)"

    def test_no_params_value(self, tmp_path: Path) -> None:
        """Value binding (no params) should have no signature."""
        from hypergumbo.analyze.ocaml import analyze_ocaml

        make_ocaml_file(tmp_path, "main.ml", "let x = 42")
        result = analyze_ocaml(tmp_path)
        vals = [s for s in result.symbols if s.kind == "function" and s.name == "x"]
        assert len(vals) == 1
        assert vals[0].signature is None  # No params = value, not function
