"""Branch coverage tests for nix.py analyzer.

Tests specific branch paths in the Nix analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Function definition extraction (named lambdas)
- Let bindings and attribute set bindings
- Flake inputs detection
- Derivation declarations
- Import expressions
- Function call edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.nix import (
    _make_edge_id,
    _make_symbol_id,
    analyze_nix_files,
    find_nix_files,
)


def make_nix_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Nix file with given content."""
    (tmp_path / name).write_text(content)


class TestNixHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("pkgs/utils.nix", 5, 10, "mkDerivation", "function")
        assert symbol_id == "nix:pkgs/utils.nix:5-10:mkDerivation:function"

    def test_make_edge_id_deterministic(self) -> None:
        """Test edge ID is deterministic."""
        edge_id_1 = _make_edge_id("src1", "dst1", "imports")
        edge_id_2 = _make_edge_id("src1", "dst1", "imports")
        assert edge_id_1 == edge_id_2
        assert edge_id_1.startswith("edge:sha256:")


class TestFunctionExtraction:
    """Branch coverage for function definition extraction."""

    def test_simple_function(self, tmp_path: Path) -> None:
        """Test simple function extraction."""
        make_nix_file(tmp_path, "utils.nix", """
{
  add = x: y: x + y;
}
""")
        result = analyze_nix_files(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) >= 1
        assert any(f.name == "add" for f in funcs)

    def test_formals_function(self, tmp_path: Path) -> None:
        """Test function with formals (attrset pattern)."""
        make_nix_file(tmp_path, "pkg.nix", """
{
  buildPkg = { pkgs, lib, ... }: pkgs.stdenv.mkDerivation { };
}
""")
        result = analyze_nix_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any(f.name == "buildPkg" for f in funcs)

    def test_multiple_functions(self, tmp_path: Path) -> None:
        """Test multiple function definitions."""
        make_nix_file(tmp_path, "funcs.nix", """
{
  inc = x: x + 1;
  dec = x: x - 1;
  double = x: x * 2;
}
""")
        result = analyze_nix_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "inc" in names
        assert "dec" in names
        assert "double" in names


class TestBindingExtraction:
    """Branch coverage for binding extraction."""

    def test_simple_binding(self, tmp_path: Path) -> None:
        """Test simple binding extraction."""
        make_nix_file(tmp_path, "config.nix", """
{
  version = "1.0.0";
  name = "my-package";
}
""")
        result = analyze_nix_files(tmp_path)
        bindings = [s for s in result.symbols if s.kind == "binding"]
        names = [b.name for b in bindings]
        assert "version" in names
        assert "name" in names


class TestFlakeInputs:
    """Branch coverage for flake input detection."""

    def test_flake_inputs(self, tmp_path: Path) -> None:
        """Test flake input detection."""
        make_nix_file(tmp_path, "flake.nix", """
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-23.11";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }: { };
}
""")
        result = analyze_nix_files(tmp_path)
        inputs = [s for s in result.symbols if s.kind == "input"]
        names = [i.name for i in inputs]
        assert "nixpkgs" in names
        assert "flake-utils" in names


class TestDerivationExtraction:
    """Branch coverage for derivation detection."""

    def test_mkderivation(self, tmp_path: Path) -> None:
        """Test mkDerivation detection."""
        make_nix_file(tmp_path, "pkg.nix", """
{ pkgs }:
pkgs.stdenv.mkDerivation {
  pname = "hello";
  version = "1.0";
}
""")
        result = analyze_nix_files(tmp_path)
        # The derivation should be detected
        assert not result.skipped


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_import_path(self, tmp_path: Path) -> None:
        """Test import path creates edge."""
        make_nix_file(tmp_path, "main.nix", """
let
  utils = import ./utils.nix;
in
  utils
""")
        result = analyze_nix_files(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1

    def test_import_nixpkgs(self, tmp_path: Path) -> None:
        """Test import <nixpkgs> creates edge."""
        make_nix_file(tmp_path, "shell.nix", """
let
  pkgs = import <nixpkgs> {};
in
  pkgs.mkShell { }
""")
        result = analyze_nix_files(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1


class TestFunctionCallEdges:
    """Branch coverage for function call edge extraction."""

    def test_function_call(self, tmp_path: Path) -> None:
        """Test function call creates edge."""
        make_nix_file(tmp_path, "calls.nix", """
{
  helper = x: x + 1;
  main = x: helper x;
}
""")
        result = analyze_nix_files(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1


class TestFindNixFiles:
    """Branch coverage for file discovery."""

    def test_finds_nix_files(self, tmp_path: Path) -> None:
        """Test .nix files are discovered."""
        (tmp_path / "default.nix").write_text("{ }")

        files = list(find_nix_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".nix"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        pkgs = tmp_path / "pkgs" / "utils"
        pkgs.mkdir(parents=True)
        (pkgs / "default.nix").write_text("{ lib }: { }")

        files = list(find_nix_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "default.nix"


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_empty_attrset(self, tmp_path: Path) -> None:
        """Test empty attrset file."""
        make_nix_file(tmp_path, "empty.nix", "{ }")
        result = analyze_nix_files(tmp_path)
        assert not result.skipped

    def test_minimal_let(self, tmp_path: Path) -> None:
        """Test minimal let expression."""
        make_nix_file(tmp_path, "min.nix", """
let
  x = 1;
in
  x
""")
        result = analyze_nix_files(tmp_path)
        assert not result.skipped

    def test_no_nix_files(self, tmp_path: Path) -> None:
        """Test directory with no Nix files."""
        result = analyze_nix_files(tmp_path)
        assert not result.skipped
        assert len(result.symbols) == 0


class TestTopLevelFunction:
    """Branch coverage for top-level function (module pattern)."""

    def test_top_level_function(self, tmp_path: Path) -> None:
        """Test top-level function extraction."""
        make_nix_file(tmp_path, "mymodule.nix", """
{ config, lib, pkgs, ... }:

{
  options = { };
  config = { };
}
""")
        result = analyze_nix_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        # Should include a function named after the file
        assert len(funcs) >= 1


class TestFunctionSignatures:
    """Branch coverage for function signature extraction."""

    def test_curried_function_signature(self, tmp_path: Path) -> None:
        """Test curried function signature extraction."""
        make_nix_file(tmp_path, "curried.nix", """
{
  add = a: b: a + b;
}
""")
        result = analyze_nix_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) == 1
        assert funcs[0].signature is not None
        assert "a" in funcs[0].signature
        assert "b" in funcs[0].signature

    def test_formals_signature(self, tmp_path: Path) -> None:
        """Test formals (attrset pattern) signature."""
        make_nix_file(tmp_path, "formals.nix", """
{
  build = { src, name, version ? "0.1" }: src;
}
""")
        result = analyze_nix_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "build"]
        assert len(funcs) == 1
        assert funcs[0].signature is not None
        # Formals signature uses { }
        assert "{" in funcs[0].signature
