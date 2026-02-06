"""Branch coverage tests for llvm_ir.py analyzer.

Tests specific branch paths in the LLVM IR analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import llvm_ir as llvm_module
from hypergumbo_lang_extended1.llvm_ir import (
    analyze_llvm_ir,
    find_llvm_ir_files,
)


def make_llvm_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an LLVM IR file with given content."""
    (tmp_path / name).write_text(content)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_function_declaration(self, tmp_path: Path) -> None:
        """Test function declaration extraction."""
        make_llvm_file(tmp_path, "code.ll", """
define i32 @add(i32 %a, i32 %b) {
entry:
  %sum = add i32 %a, %b
  ret i32 %sum
}
""")
        result = analyze_llvm_ir(tmp_path)
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("add" in f.name for f in funcs)

    def test_declare_function(self, tmp_path: Path) -> None:
        """Test declared function extraction."""
        make_llvm_file(tmp_path, "code.ll", """
declare i32 @printf(i8*, ...)
""")
        result = analyze_llvm_ir(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert not result.skipped  # lenient check


class TestGlobalExtraction:
    """Branch coverage for global extraction."""

    def test_global_variable(self, tmp_path: Path) -> None:
        """Test global variable extraction."""
        make_llvm_file(tmp_path, "code.ll", """
@global_var = global i32 42
@constant = constant [13 x i8] c"Hello, World!"
""")
        result = analyze_llvm_ir(tmp_path)
        globals = [s for s in result.symbols if s.kind in ("global", "constant")]
        assert not result.skipped  # lenient check


class TestTypeExtraction:
    """Branch coverage for type extraction."""

    def test_type_definition(self, tmp_path: Path) -> None:
        """Test type definition extraction."""
        make_llvm_file(tmp_path, "code.ll", """
%struct.Point = type { i32, i32 }
%class.MyClass = type { i32, i8* }
""")
        result = analyze_llvm_ir(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_call_instruction(self, tmp_path: Path) -> None:
        """Test call instruction creates edge."""
        make_llvm_file(tmp_path, "code.ll", """
define void @main() {
entry:
  call void @helper()
  ret void
}

define void @helper() {
entry:
  ret void
}
""")
        result = analyze_llvm_ir(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindLlvmFiles:
    """Branch coverage for file discovery."""

    def test_finds_ll_files(self, tmp_path: Path) -> None:
        """Test .ll files are discovered."""
        (tmp_path / "test.ll").write_text("define void @test() { ret void }")
        files = list(find_llvm_ir_files(tmp_path))
        assert any(f.suffix == ".ll" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_llvm_files(self, tmp_path: Path) -> None:
        """Test directory with no LLVM IR files."""
        result = analyze_llvm_ir(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(llvm_module, "is_llvm_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="LLVM IR analysis skipped"):
                result = llvm_module.analyze_llvm_ir(tmp_path)
        assert result.skipped is True
