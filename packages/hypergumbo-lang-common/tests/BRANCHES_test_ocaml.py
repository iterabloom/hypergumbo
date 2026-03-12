# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for ocaml.py analyzer.

Tests specific branch paths in the OCaml analyzer that may not be covered
by the main test suite. Focuses on:
- Signature extraction edge cases
- Enclosing function resolution
- Unresolved call handling
- Module alias paths
- Multiple functions per file
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_symbol_id, node_text
from hypergumbo_lang_common.ocaml import analyze_ocaml, find_ocaml_files

def make_ocaml_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an OCaml file with given content."""
    (tmp_path / name).write_text(content)

class TestOCamlHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("ocaml", "src/main.ml", 1, 10, "add", "function")
        assert symbol_id == "ocaml:src/main.ml:1-10:add:function"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = make_file_id("ocaml", "lib/utils.ml")
        assert file_id == "ocaml:lib/utils.ml:1-1:file:file"

class TestOCamlUnresolvedCalls:
    """Branch coverage for unresolved call handling."""

    def test_call_to_external_function(self, tmp_path: Path) -> None:
        """Test call to function not in codebase creates unresolved edge."""
        make_ocaml_file(tmp_path, "main.ml", """
let main () = external_lib_func 42
""")
        result = analyze_ocaml(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have edge to unresolved function
        assert len(call_edges) >= 1
        unresolved = [e for e in call_edges if "?" in e.dst]
        assert len(unresolved) >= 1

    def test_call_to_stdlib_excluded(self, tmp_path: Path) -> None:
        """Test calls to stdlib print functions are excluded."""
        make_ocaml_file(tmp_path, "main.ml", """
let main () =
    print_int 42;
    print_string "hello";
    print_endline "world";
    print_newline ()
""")
        result = analyze_ocaml(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Standard print functions should be excluded from call edges
        print_calls = [e for e in call_edges if "print_" in e.dst]
        assert len(print_calls) == 0

class TestOCamlMultipleFunctions:
    """Branch coverage for files with multiple functions."""

    def test_multiple_functions_single_file(self, tmp_path: Path) -> None:
        """Test extraction of multiple functions from one file."""
        make_ocaml_file(tmp_path, "math.ml", """
let add x y = x + y
let sub x y = x - y
let mul x y = x * y
let div x y = x / y
""")
        result = analyze_ocaml(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "add" in names
        assert "sub" in names
        assert "mul" in names
        assert "div" in names

    def test_function_calling_another_in_same_file(self, tmp_path: Path) -> None:
        """Test call edge between functions in same file."""
        make_ocaml_file(tmp_path, "utils.ml", """
let double x = x * 2

let quadruple x = double (double x)
""")
        result = analyze_ocaml(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # quadruple should call double (twice)
        double_calls = [e for e in call_edges if "double" in e.dst]
        assert len(double_calls) >= 2

class TestOCamlTypeDefinitions:
    """Branch coverage for type extraction."""

    def test_multiple_types(self, tmp_path: Path) -> None:
        """Test multiple type definitions in one file."""
        make_ocaml_file(tmp_path, "types.ml", """
type point = { x: float; y: float }
type color = Red | Green | Blue
type tree = Leaf | Node of tree * int * tree
""")
        result = analyze_ocaml(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        names = [t.name for t in types]
        assert "point" in names
        assert "color" in names
        assert "tree" in names

    def test_parameterized_type(self, tmp_path: Path) -> None:
        """Test parameterized type definition."""
        make_ocaml_file(tmp_path, "generic.ml", """
type 'a option = None | Some of 'a
type ('a, 'b) result = Ok of 'a | Error of 'b
""")
        result = analyze_ocaml(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        names = [t.name for t in types]
        assert "option" in names
        assert "result" in names

class TestOCamlModuleDefinitions:
    """Branch coverage for module extraction."""

    def test_nested_module(self, tmp_path: Path) -> None:
        """Test nested module definition."""
        make_ocaml_file(tmp_path, "nested.ml", """
module Outer = struct
    module Inner = struct
        let value = 42
    end
end
""")
        result = analyze_ocaml(tmp_path)
        modules = [s for s in result.symbols if s.kind == "module"]
        names = [m.name for m in modules]
        assert "Outer" in names
        assert "Inner" in names

    def test_module_alias(self, tmp_path: Path) -> None:
        """Test module alias definition."""
        make_ocaml_file(tmp_path, "aliases.ml", """
module L = List
module S = String

let main () = L.length [1; 2; 3]
""")
        result = analyze_ocaml(tmp_path)
        modules = [s for s in result.symbols if s.kind == "module"]
        names = [m.name for m in modules]
        assert "L" in names
        assert "S" in names

class TestOCamlOpenStatements:
    """Branch coverage for open statement handling."""

    def test_multiple_opens(self, tmp_path: Path) -> None:
        """Test multiple open statements create import edges."""
        make_ocaml_file(tmp_path, "main.ml", """
open List
open String
open Array

let main () = 1
""")
        result = analyze_ocaml(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        dests = [e.dst for e in import_edges]
        assert any("List" in d for d in dests)
        assert any("String" in d for d in dests)
        assert any("Array" in d for d in dests)

    def test_open_qualified_path(self, tmp_path: Path) -> None:
        """Test open with qualified module path."""
        make_ocaml_file(tmp_path, "main.ml", """
open Unix.LargeFile

let main () = 1
""")
        result = analyze_ocaml(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert any("Unix.LargeFile" in e.dst for e in import_edges)

class TestOCamlSignatureEdgeCases:
    """Branch coverage for signature extraction edge cases."""

    def test_multiline_function(self, tmp_path: Path) -> None:
        """Test signature extraction from multiline function."""
        make_ocaml_file(tmp_path, "main.ml", """
let complex_func a b c d e =
    let intermediate = a + b in
    let another = c * d in
    intermediate + another + e
""")
        result = analyze_ocaml(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "complex_func"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(a, b, c, d, e)"

    def test_recursive_function(self, tmp_path: Path) -> None:
        """Test signature extraction from recursive function."""
        make_ocaml_file(tmp_path, "main.ml", """
let rec factorial n =
    if n <= 1 then 1
    else n * factorial (n - 1)
""")
        result = analyze_ocaml(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "factorial"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(n)"

class TestOCamlCrossFileResolution:
    """Branch coverage for cross-file resolution."""

    def test_three_file_chain(self, tmp_path: Path) -> None:
        """Test call resolution across three files."""
        make_ocaml_file(tmp_path, "base.ml", """
let base_func x = x * 2
""")
        make_ocaml_file(tmp_path, "middle.ml", """
let middle_func x = base_func x + 1
""")
        make_ocaml_file(tmp_path, "top.ml", """
let top_func x = middle_func (middle_func x)
""")
        result = analyze_ocaml(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        # Should have calls between files
        middle_to_base = [e for e in call_edges if "base_func" in e.dst]
        assert len(middle_to_base) >= 1

        top_to_middle = [e for e in call_edges if "middle_func" in e.dst]
        assert len(top_to_middle) >= 2

class TestOCamlEmptyAndMinimal:
    """Branch coverage for empty/minimal files."""

    def test_comment_only_file(self, tmp_path: Path) -> None:
        """Test file with only comments."""
        make_ocaml_file(tmp_path, "comments.ml", """
(* This file only has comments *)
(* Another comment *)
""")
        result = analyze_ocaml(tmp_path)
        # Should handle gracefully
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 0

    def test_file_with_only_type(self, tmp_path: Path) -> None:
        """Test file with only type definitions."""
        make_ocaml_file(tmp_path, "types_only.ml", """
type status = Active | Inactive
""")
        result = analyze_ocaml(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        assert len(types) == 1
        # No function calls expected
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 0

class TestOCamlFindFiles:
    """Branch coverage for file discovery."""

    def test_excludes_build_directory(self, tmp_path: Path) -> None:
        """Test _build directory is excluded."""
        build = tmp_path / "_build" / "default"
        build.mkdir(parents=True)
        (build / "generated.ml").write_text("let x = 1")
        (tmp_path / "src.ml").write_text("let y = 2")

        files = list(find_ocaml_files(tmp_path))
        names = [f.name for f in files]
        assert "src.ml" in names
        # Build artifacts should be excluded
        assert "generated.ml" not in names

    def test_finds_in_subdirectories(self, tmp_path: Path) -> None:
        """Test finding .ml files in nested directories."""
        lib = tmp_path / "lib" / "core"
        lib.mkdir(parents=True)
        (lib / "core.ml").write_text("let x = 1")

        files = list(find_ocaml_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "core.ml"

class TestOCamlQualifiedCalls:
    """Branch coverage for qualified call resolution."""

    def test_module_qualified_call(self, tmp_path: Path) -> None:
        """Test call with module qualifier (List.length)."""
        make_ocaml_file(tmp_path, "main.ml", """
let count lst =
    List.length lst
""")
        result = analyze_ocaml(tmp_path)
        # Should handle gracefully - List.length is external
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any(f.name == "count" for f in funcs)

    def test_aliased_module_call(self, tmp_path: Path) -> None:
        """Test call with aliased module qualifier (L.length where L = List)."""
        make_ocaml_file(tmp_path, "main.ml", """
module L = List

let count lst = L.length lst
""")
        result = analyze_ocaml(tmp_path)
        # Module alias should be extracted
        modules = [s for s in result.symbols if s.kind == "module"]
        assert any(m.name == "L" for m in modules)
