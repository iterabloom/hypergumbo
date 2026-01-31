"""Tests for the LLVM IR analyzer."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import llvm_ir as llvm_module
from hypergumbo_lang_extended1.llvm_ir import (
    analyze_llvm_ir,
    find_llvm_ir_files,
    is_llvm_tree_sitter_available,
)

if TYPE_CHECKING:
    pass


def make_llvm_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a LLVM IR file in the temp directory."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


class TestIsTreeSitterAvailable:
    """Tests for is_llvm_tree_sitter_available."""

    def test_returns_true_when_available(self) -> None:
        """Should return True when tree-sitter-llvm is installed."""
        assert is_llvm_tree_sitter_available() is True


class TestFindLlvmIrFiles:
    """Tests for find_llvm_ir_files."""

    def test_finds_ll_files(self, tmp_path: Path) -> None:
        """Should find .ll files."""
        make_llvm_file(tmp_path, "test.ll", "; comment")
        make_llvm_file(tmp_path, "other.ll", "; another")

        files = list(find_llvm_ir_files(tmp_path))
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"test.ll", "other.ll"}

    def test_ignores_non_llvm_files(self, tmp_path: Path) -> None:
        """Should ignore non-.ll files."""
        make_llvm_file(tmp_path, "test.ll", "; comment")
        (tmp_path / "test.c").write_text("int main() {}")
        (tmp_path / "test.py").write_text("print('hello')")

        files = list(find_llvm_ir_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "test.ll"


class TestAnalyzeLlvmIr:
    """Tests for analyze_llvm_ir."""

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Should handle empty directory."""
        result = analyze_llvm_ir(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.skipped is False

    def test_detects_function_definition(self, tmp_path: Path) -> None:
        """Should detect function definitions."""
        make_llvm_file(
            tmp_path,
            "test.ll",
            """; Simple function
define i32 @add(i32 %a, i32 %b) {
  %result = add i32 %a, %b
  ret i32 %result
}
""",
        )

        result = analyze_llvm_ir(tmp_path)
        assert not result.skipped

        func = next((s for s in result.symbols if s.name == "add"), None)
        assert func is not None
        assert func.kind == "function"
        assert func.canonical_name == "@add"
        assert "i32 @add" in func.signature

    def test_detects_multiple_functions(self, tmp_path: Path) -> None:
        """Should detect multiple function definitions."""
        make_llvm_file(
            tmp_path,
            "test.ll",
            """define i32 @foo() {
  ret i32 0
}

define void @bar() {
  ret void
}

define double @baz(double %x) {
  ret double %x
}
""",
        )

        result = analyze_llvm_ir(tmp_path)
        names = {s.name for s in result.symbols if s.kind == "function"}
        assert names == {"foo", "bar", "baz"}

    def test_detects_function_declaration(self, tmp_path: Path) -> None:
        """Should detect external function declarations."""
        make_llvm_file(
            tmp_path,
            "test.ll",
            """declare i32 @printf(ptr, ...)
declare void @exit(i32)
""",
        )

        result = analyze_llvm_ir(tmp_path)
        decls = [s for s in result.symbols if s.kind == "declaration"]
        names = {d.name for d in decls}
        assert names == {"printf", "exit"}

    def test_detects_global_variable(self, tmp_path: Path) -> None:
        """Should detect global variable definitions."""
        make_llvm_file(
            tmp_path,
            "test.ll",
            """@global_int = global i32 42, align 4
@constant_str = private unnamed_addr constant [14 x i8] c"Hello, World!\\00", align 1
""",
        )

        result = analyze_llvm_ir(tmp_path)
        vars = [s for s in result.symbols if s.kind == "variable"]
        names = {v.name for v in vars}
        assert "global_int" in names
        assert "constant_str" in names

    def test_detects_function_call(self, tmp_path: Path) -> None:
        """Should detect function call edges."""
        make_llvm_file(
            tmp_path,
            "test.ll",
            """define i32 @add(i32 %a, i32 %b) {
  %result = add i32 %a, %b
  ret i32 %result
}

define i32 @main() {
  %result = call i32 @add(i32 3, i32 5)
  ret i32 %result
}
""",
        )

        result = analyze_llvm_ir(tmp_path)

        # Find the edge
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 1

        edge = call_edges[0]
        assert edge.evidence_type == "ast_call_direct"

        # Verify source is main and target is add
        main_sym = next(s for s in result.symbols if s.name == "main")
        add_sym = next(s for s in result.symbols if s.name == "add")
        assert edge.src == main_sym.id
        assert edge.dst == add_sym.id

    def test_detects_multiple_calls(self, tmp_path: Path) -> None:
        """Should detect multiple function calls."""
        make_llvm_file(
            tmp_path,
            "test.ll",
            """define i32 @helper1() {
  ret i32 1
}

define i32 @helper2() {
  ret i32 2
}

define i32 @main() {
  %r1 = call i32 @helper1()
  %r2 = call i32 @helper2()
  %result = add i32 %r1, %r2
  ret i32 %result
}
""",
        )

        result = analyze_llvm_ir(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 2

        # Verify both calls are detected
        helper1 = next(s for s in result.symbols if s.name == "helper1")
        helper2 = next(s for s in result.symbols if s.name == "helper2")
        target_ids = {e.dst for e in call_edges}
        assert helper1.id in target_ids
        assert helper2.id in target_ids

    def test_cross_file_call_resolution(self, tmp_path: Path) -> None:
        """Should resolve calls across files."""
        make_llvm_file(
            tmp_path,
            "utils.ll",
            """define i32 @utility_func(i32 %x) {
  %result = mul i32 %x, 2
  ret i32 %result
}
""",
        )

        make_llvm_file(
            tmp_path,
            "main.ll",
            """declare i32 @utility_func(i32)

define i32 @main() {
  %result = call i32 @utility_func(i32 21)
  ret i32 %result
}
""",
        )

        result = analyze_llvm_ir(tmp_path)

        # Should have the function from utils.ll
        utility_func = next(
            (s for s in result.symbols if s.name == "utility_func" and s.kind == "function"),
            None,
        )
        assert utility_func is not None

        # Should have call edge
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        assert call_edges[0].dst == utility_func.id

    def test_function_with_attributes(self, tmp_path: Path) -> None:
        """Should handle functions with various attributes."""
        make_llvm_file(
            tmp_path,
            "test.ll",
            """; Function Attrs: noinline nounwind optnone uwtable
define dso_local i32 @attributed_func(i32 noundef %0, i32 noundef %1) #0 {
  %3 = add nsw i32 %0, %1
  ret i32 %3
}

attributes #0 = { noinline nounwind optnone uwtable }
""",
        )

        result = analyze_llvm_ir(tmp_path)
        func = next((s for s in result.symbols if s.name == "attributed_func"), None)
        assert func is not None
        assert func.kind == "function"

    def test_function_pointer_type(self, tmp_path: Path) -> None:
        """Should handle functions with pointer types."""
        make_llvm_file(
            tmp_path,
            "test.ll",
            """define ptr @create_buffer(i64 %size) {
  %buf = alloca i8, i64 %size
  ret ptr %buf
}
""",
        )

        result = analyze_llvm_ir(tmp_path)
        func = next((s for s in result.symbols if s.name == "create_buffer"), None)
        assert func is not None
        assert "ptr" in func.signature

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        """Should include analysis run metadata."""
        make_llvm_file(tmp_path, "test.ll", "define void @test() { ret void }")

        result = analyze_llvm_ir(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "llvm-v1"
        assert result.run.files_analyzed == 1
        assert result.run.duration_ms >= 0

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Should return skipped result when tree-sitter-llvm unavailable."""
        make_llvm_file(tmp_path, "test.ll", "define void @test() { ret void }")

        with patch.object(llvm_module, "is_llvm_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="LLVM IR analysis skipped"):
                result = llvm_module.analyze_llvm_ir(tmp_path)

        assert result.skipped is True
        assert "not available" in result.skip_reason
        assert result.run is not None  # run is always created

    def test_recursive_calls(self, tmp_path: Path) -> None:
        """Should handle recursive function calls."""
        make_llvm_file(
            tmp_path,
            "test.ll",
            """define i32 @factorial(i32 %n) {
entry:
  %cmp = icmp sle i32 %n, 1
  br i1 %cmp, label %base, label %recurse

base:
  ret i32 1

recurse:
  %sub = sub nsw i32 %n, 1
  %rec = call i32 @factorial(i32 %sub)
  %result = mul nsw i32 %n, %rec
  ret i32 %result
}
""",
        )

        result = analyze_llvm_ir(tmp_path)

        # Should have factorial function
        factorial = next((s for s in result.symbols if s.name == "factorial"), None)
        assert factorial is not None

        # Should have recursive call edge
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        assert call_edges[0].src == factorial.id
        assert call_edges[0].dst == factorial.id

    def test_external_call_no_edge(self, tmp_path: Path) -> None:
        """Should not create edge for calls to declarations without definitions."""
        make_llvm_file(
            tmp_path,
            "test.ll",
            """declare i32 @external_lib_func(i32)

define i32 @main() {
  %result = call i32 @external_lib_func(i32 42)
  ret i32 %result
}
""",
        )

        result = analyze_llvm_ir(tmp_path)

        # The declaration is tracked
        ext = next((s for s in result.symbols if s.name == "external_lib_func"), None)
        assert ext is not None
        assert ext.kind == "declaration"

        # Should still have edge (declaration is in registry)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 1

    def test_struct_type_global(self, tmp_path: Path) -> None:
        """Should handle struct type globals."""
        make_llvm_file(
            tmp_path,
            "test.ll",
            """%struct.Point = type { i32, i32 }

@origin = global %struct.Point { i32 0, i32 0 }, align 4
""",
        )

        result = analyze_llvm_ir(tmp_path)
        origin = next((s for s in result.symbols if s.name == "origin"), None)
        assert origin is not None
        assert origin.kind == "variable"

    def test_spans_are_correct(self, tmp_path: Path) -> None:
        """Should have correct span information."""
        make_llvm_file(
            tmp_path,
            "test.ll",
            """; Line 1 - comment
define i32 @my_func() {
  ret i32 0
}
""",
        )

        result = analyze_llvm_ir(tmp_path)
        func = next((s for s in result.symbols if s.name == "my_func"), None)
        assert func is not None
        # Function starts on line 2 (1-indexed)
        assert func.span.start_line == 2
