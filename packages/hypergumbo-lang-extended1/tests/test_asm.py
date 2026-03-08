"""Tests for Assembly language analyzer.

Tests verify that the analyzer correctly extracts:
- Labels (as function symbols)
- Global directives
- Section directives
- Call instructions (call edges)
- Graceful handling when tree-sitter is unavailable
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import asm as asm_module
from hypergumbo_core.analyze.base import find_child_by_type
from hypergumbo_lang_extended1.asm import (
    analyze_asm,
    find_asm_files,
    is_asm_tree_sitter_available,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository for testing."""
    return tmp_path


class TestFindAsmFiles:
    """Tests for find_asm_files function."""

    def test_finds_s_files(self, temp_repo: Path) -> None:
        """Finds .s files."""
        (temp_repo / "boot.s").write_text(".global _start\n_start:\n    ret\n")
        (temp_repo / "README.md").write_text("# Docs")

        files = list(find_asm_files(temp_repo))
        filenames = {f.name for f in files}

        assert "boot.s" in filenames
        assert "README.md" not in filenames

    def test_finds_asm_files(self, temp_repo: Path) -> None:
        """Finds .asm files."""
        (temp_repo / "main.asm").write_text("_start:\n    ret\n")

        files = list(find_asm_files(temp_repo))
        filenames = {f.name for f in files}

        assert "main.asm" in filenames

    def test_finds_S_files(self, temp_repo: Path) -> None:
        """Finds .S (preprocessed assembly) files."""
        (temp_repo / "startup.S").write_text("#include \"defs.h\"\n_start:\n    ret\n")

        files = list(find_asm_files(temp_repo))
        filenames = {f.name for f in files}

        assert "startup.S" in filenames


class TestAsmTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_availability_check_runs(self) -> None:
        """Availability check returns a boolean."""
        result = is_asm_tree_sitter_available()
        assert isinstance(result, bool)


class TestAsmAnalysis:
    """Tests for Assembly analysis with tree-sitter."""

    def test_detects_labels(self, temp_repo: Path) -> None:
        """Detects labels as function symbols."""
        (temp_repo / "funcs.s").write_text("""
.global my_function
.section .text

my_function:
    push rbp
    mov rbp, rsp
    pop rbp
    ret

helper:
    ret
""")

        result = analyze_asm(temp_repo)

        assert not result.skipped
        label_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "my_function" in label_names
        assert "helper" in label_names

    def test_detects_global_directives(self, temp_repo: Path) -> None:
        """Detects .global directives."""
        (temp_repo / "entry.s").write_text("""
.global _start

_start:
    mov rax, 60
    syscall
""")

        result = analyze_asm(temp_repo)

        # _start should be detected as a label/function
        label_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "_start" in label_names

    def test_detects_data_labels(self, temp_repo: Path) -> None:
        """Detects data section labels as variable symbols."""
        (temp_repo / "data.s").write_text("""
.section .data

message:
    .ascii "Hello, World!"

buffer:
    .space 256
""")

        result = analyze_asm(temp_repo)

        # Data labels detected as symbols
        names = {s.name for s in result.symbols}
        assert "message" in names
        assert "buffer" in names

    def test_detects_call_edges(self, temp_repo: Path) -> None:
        """Detects call instructions as call edges."""
        (temp_repo / "calls.s").write_text("""
.section .text

main:
    call helper
    call printf
    ret

helper:
    ret
""")

        result = analyze_asm(temp_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have call edges from main
        main_calls = [e for e in call_edges if "main" in e.src]
        assert len(main_calls) >= 2
        call_targets = {e.dst.split(":")[-2] for e in main_calls}
        assert "helper" in call_targets
        assert "printf" in call_targets

    def test_resolved_call_has_higher_confidence(self, temp_repo: Path) -> None:
        """Resolved calls (to local labels) have higher confidence than external."""
        (temp_repo / "resolve.s").write_text("""
.section .text

caller:
    call local_func
    call external_func
    ret

local_func:
    ret
""")

        result = analyze_asm(temp_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        local_call = next((e for e in call_edges if "local_func" in e.dst and "external" not in e.dst), None)
        ext_call = next((e for e in call_edges if "external_func" in e.dst), None)

        assert local_call is not None
        assert ext_call is not None
        assert local_call.confidence > ext_call.confidence

    def test_empty_repo(self, temp_repo: Path) -> None:
        """Returns empty result for repo with no assembly files."""
        (temp_repo / "main.py").write_text("print('hello')")

        result = analyze_asm(temp_repo)

        assert not result.skipped
        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_analysis_run_metadata(self, temp_repo: Path) -> None:
        """Analysis run is created with correct metadata."""
        (temp_repo / "test.s").write_text("_start:\n    ret\n")

        result = analyze_asm(temp_repo)

        assert result.run is not None
        assert result.run.pass_id == "asm-v1"
        assert result.run.files_analyzed >= 1


class TestFindChildByType:
    """Tests for find_child_by_type utility function."""

    def test_returns_none_when_no_match(self) -> None:
        """Returns None when no child has the requested type."""
        child_a = SimpleNamespace(type="identifier")
        child_b = SimpleNamespace(type="number")
        parent = SimpleNamespace(children=[child_a, child_b])

        result = find_child_by_type(parent, "nonexistent_type")
        assert result is None

    def test_returns_matching_child(self) -> None:
        """Returns the first child matching the requested type."""
        child_a = SimpleNamespace(type="identifier")
        child_b = SimpleNamespace(type="word")
        parent = SimpleNamespace(children=[child_a, child_b])

        result = find_child_by_type(parent, "word")
        assert result is child_b


class TestAsmRegisterFiltering:
    """Tests for filtering CPU register names from call targets."""

    def test_register_call_targets_excluded(self, temp_repo: Path) -> None:
        """Call instructions with register targets should not create edges."""
        (temp_repo / "indirect.s").write_text("""
.section .text

dispatch:
    call r0
    call r5
    call r6
    call rax
    call rbx
    call eax
    call real_function
    ret

real_function:
    ret
""")

        result = analyze_asm(temp_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        call_dsts = {e.dst for e in call_edges}
        # Register names should not appear as call targets
        for reg in ("r0", "r5", "r6", "rax", "rbx", "eax"):
            assert not any(reg in dst for dst in call_dsts), (
                f"Register {reg} should not be a call target"
            )
        # But real functions should still be resolved
        assert any("real_function" in dst for dst in call_dsts)

    def test_register_names_case_insensitive(self, temp_repo: Path) -> None:
        """Register filtering handles both cases (RAX, rax)."""
        (temp_repo / "case.s").write_text("""
.section .text

caller:
    call RAX
    call Rax
    call actual_func
    ret

actual_func:
    ret
""")

        result = analyze_asm(temp_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        call_dsts = {e.dst for e in call_edges}
        # Uppercase register names should also be filtered
        assert not any("RAX" in dst or "Rax" in dst for dst in call_dsts)
        assert any("actual_func" in dst for dst in call_dsts)


class TestAsmAnalysisUnavailable:
    """Tests for handling unavailable tree-sitter."""

    def test_skipped_when_unavailable(self, temp_repo: Path) -> None:
        """Returns skipped result when tree-sitter unavailable."""
        (temp_repo / "test.s").write_text("_start:\n    ret\n")

        with patch.object(asm_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="asm analysis skipped"):
                result = asm_module.analyze_asm(temp_repo)

        assert result.skipped is True
        assert len(result.symbols) == 0
