"""Tests for Nix analyzer using tree-sitter-nix.

Tests verify that the analyzer correctly extracts:
- Function definitions (lambdas with bindings)
- Let bindings and attribute set bindings
- Flake inputs and outputs
- Import statements
"""

from hypergumbo.analyze.nix import (
    PASS_ID,
    PASS_VERSION,
    NixAnalysisResult,
    analyze_nix_files,
    find_nix_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "nix-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_function(tmp_path):
    """Test detection of named function binding."""
    nix_file = tmp_path / "default.nix"
    nix_file.write_text("""
{
  myFunc = x: y: x + y;
  add = a: b: a + b;
}
""")
    result = analyze_nix_files(tmp_path)

    assert not result.skipped
    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 2
    names = [f.name for f in functions]
    assert "myFunc" in names
    assert "add" in names
    assert functions[0].language == "nix"


def test_analyze_let_binding(tmp_path):
    """Test detection of let bindings."""
    nix_file = tmp_path / "default.nix"
    nix_file.write_text("""
let
  message = "hello";
  count = 42;
in
{ inherit message count; }
""")
    result = analyze_nix_files(tmp_path)

    bindings = [s for s in result.symbols if s.kind == "binding"]
    assert len(bindings) >= 2
    names = [b.name for b in bindings]
    assert "message" in names
    assert "count" in names


def test_analyze_derivation(tmp_path):
    """Test detection of derivation calls."""
    nix_file = tmp_path / "default.nix"
    nix_file.write_text("""
{ pkgs }:
{
  myPackage = pkgs.stdenv.mkDerivation {
    name = "my-package";
    src = ./src;
  };
}
""")
    result = analyze_nix_files(tmp_path)

    derivations = [s for s in result.symbols if s.kind == "derivation"]
    assert len(derivations) >= 1
    assert derivations[0].name == "my-package"


def test_analyze_flake_input(tmp_path):
    """Test detection of flake inputs."""
    nix_file = tmp_path / "flake.nix"
    nix_file.write_text("""
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-23.11";
    flake-utils.url = "github:numtide/flake-utils";
  };
  outputs = { self, nixpkgs, flake-utils }: {};
}
""")
    result = analyze_nix_files(tmp_path)

    inputs = [s for s in result.symbols if s.kind == "input"]
    assert len(inputs) >= 2
    names = [i.name for i in inputs]
    assert "nixpkgs" in names
    assert "flake-utils" in names


def test_analyze_import(tmp_path):
    """Test detection of import expressions."""
    nix_file = tmp_path / "default.nix"
    nix_file.write_text("""
let
  pkgs = import <nixpkgs> {};
  lib = import ./lib.nix;
in
pkgs
""")
    result = analyze_nix_files(tmp_path)

    # Check for imports edge
    imports = [e for e in result.edges if e.edge_type == "imports"]
    assert len(imports) >= 1


def test_find_nix_files(tmp_path):
    """Test that Nix files are discovered correctly."""
    (tmp_path / "default.nix").write_text("{ }")
    (tmp_path / "shell.nix").write_text("{ pkgs }: pkgs.mkShell {}")
    (tmp_path / "flake.nix").write_text("{ outputs = {}; }")
    (tmp_path / "not_nix.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "module.nix").write_text("{ }")

    files = list(find_nix_files(tmp_path))
    # Should find .nix files
    assert len(files) >= 4


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no Nix files."""
    result = analyze_nix_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    nix_file = tmp_path / "default.nix"
    nix_file.write_text("{ }")

    result = analyze_nix_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    nix_file = tmp_path / "broken.nix"
    nix_file.write_text("{ broken syntax {{{{")

    # Should not raise an exception
    result = analyze_nix_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, NixAnalysisResult)


def test_span_information(tmp_path):
    """Test that span information is correct."""
    nix_file = tmp_path / "test.nix"
    nix_file.write_text("""let
  myValue = 42;
in
myValue
""")
    result = analyze_nix_files(tmp_path)

    bindings = [s for s in result.symbols if s.kind == "binding"]
    assert len(bindings) >= 1

    # Check span
    assert bindings[0].span.start_line >= 1
    assert bindings[0].span.end_line >= bindings[0].span.start_line


def test_tree_sitter_not_available():
    """Test graceful degradation when tree-sitter is not available."""
    from hypergumbo.analyze.nix import is_nix_tree_sitter_available

    # The function should return a boolean
    result = is_nix_tree_sitter_available()
    assert isinstance(result, bool)


def test_multiple_nix_files(tmp_path):
    """Test analysis across multiple Nix files."""
    (tmp_path / "default.nix").write_text("""
{ pkgs }:
{
  myApp = pkgs.hello;
}
""")
    (tmp_path / "shell.nix").write_text("""
{ pkgs ? import <nixpkgs> {} }:
pkgs.mkShell {
  packages = [ pkgs.git ];
}
""")

    result = analyze_nix_files(tmp_path)

    # Should have symbols from both files
    assert len(result.symbols) >= 1


def test_complete_flake(tmp_path):
    """Test a complete flake.nix structure."""
    nix_file = tmp_path / "flake.nix"
    nix_file.write_text("""
{
  description = "My flake";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-23.11";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      myFunc = x: x * 2;
    in
    {
      packages.${system}.default = pkgs.hello;
      devShells.${system}.default = pkgs.mkShell { };
    };
}
""")
    result = analyze_nix_files(tmp_path)

    # Should detect inputs, functions, bindings
    kinds = {s.kind for s in result.symbols}
    assert "input" in kinds or "binding" in kinds or "function" in kinds


def test_overlay(tmp_path):
    """Test detection of Nix overlays."""
    nix_file = tmp_path / "overlay.nix"
    nix_file.write_text("""
final: prev: {
  myPackage = prev.hello.override {
    name = "my-hello";
  };
}
""")
    result = analyze_nix_files(tmp_path)

    # Overlay is a function
    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 1


def test_module(tmp_path):
    """Test detection of NixOS module patterns."""
    nix_file = tmp_path / "module.nix"
    nix_file.write_text("""
{ config, lib, pkgs, ... }:
{
  options.services.myService = {
    enable = lib.mkEnableOption "myService";
  };

  config = lib.mkIf config.services.myService.enable {
    systemd.services.myService = { };
  };
}
""")
    result = analyze_nix_files(tmp_path)

    # Module is a function
    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 1


class TestNixSignatureExtraction:
    """Tests for Nix function signature extraction."""

    def test_simple_lambda(self, tmp_path):
        """Extract signature for simple lambda function."""
        nix_file = tmp_path / "test.nix"
        nix_file.write_text("""
{
  double = x: x * 2;
}
""")
        result = analyze_nix_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "double"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x)"

    def test_curried_function(self, tmp_path):
        """Extract signature for curried (multi-arg) function."""
        nix_file = tmp_path / "test.nix"
        nix_file.write_text("""
{
  add = x: y: x + y;
}
""")
        result = analyze_nix_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x, y)"

    def test_formals_pattern(self, tmp_path):
        """Extract signature for attrset pattern (formals)."""
        nix_file = tmp_path / "test.nix"
        nix_file.write_text("""
{
  greet = { name, greeting }: greeting + ", " + name;
}
""")
        result = analyze_nix_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "greet"]
        assert len(funcs) == 1
        assert funcs[0].signature == "{ name, greeting }"

    def test_formals_with_defaults(self, tmp_path):
        """Extract signature for formals with default values."""
        nix_file = tmp_path / "test.nix"
        nix_file.write_text("""
{
  addThree = { a, b, c ? 0 }: a + b + c;
}
""")
        result = analyze_nix_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "addThree"]
        assert len(funcs) == 1
        assert funcs[0].signature == "{ a, b, c }"

    def test_overlay_signature(self, tmp_path):
        """Extract signature for overlay (top-level curried function)."""
        nix_file = tmp_path / "overlay.nix"
        nix_file.write_text("""
final: prev: {
  myPackage = prev.hello;
}
""")
        result = analyze_nix_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "overlay"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(final, prev)"

    def test_module_signature(self, tmp_path):
        """Extract signature for NixOS module (formals with ellipsis)."""
        nix_file = tmp_path / "module.nix"
        nix_file.write_text("""
{ config, lib, pkgs, ... }:
{
  config = {};
}
""")
        result = analyze_nix_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "module"]
        assert len(funcs) == 1
        # Should extract the named formals (... is not captured as a name)
        assert "config" in funcs[0].signature
        assert "lib" in funcs[0].signature
        assert "pkgs" in funcs[0].signature
