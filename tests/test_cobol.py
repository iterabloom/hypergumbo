"""Tests for COBOL analyzer."""
from pathlib import Path

import pytest

from hypergumbo.analyze.cobol import analyze_cobol


class TestAnalyzeCOBOL:
    """Tests for analyze_cobol function."""

    def test_detects_program(self, tmp_path: Path) -> None:
        """Should detect COBOL programs."""
        (tmp_path / "hello.cob").write_text("""
       IDENTIFICATION DIVISION.
       PROGRAM-ID. HELLO-WORLD.
       PROCEDURE DIVISION.
           DISPLAY "HELLO".
           STOP RUN.
""")

        result = analyze_cobol(tmp_path)

        assert not result.skipped
        programs = [s for s in result.symbols if s.kind == "program"]
        assert len(programs) == 1
        assert programs[0].name == "HELLO-WORLD"

    def test_detects_paragraphs(self, tmp_path: Path) -> None:
        """Should detect COBOL paragraphs."""
        (tmp_path / "calc.cbl").write_text("""
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CALC-PROG.
       PROCEDURE DIVISION.
       MAIN-PARA.
           DISPLAY "MAIN".
       INIT-PARA.
           DISPLAY "INIT".
       CALC-PARA.
           DISPLAY "CALC".
""")

        result = analyze_cobol(tmp_path)

        paragraphs = [s for s in result.symbols if s.kind == "paragraph"]
        names = [p.name for p in paragraphs]
        assert "MAIN-PARA" in names
        assert "INIT-PARA" in names
        assert "CALC-PARA" in names

    def test_detects_data_items(self, tmp_path: Path) -> None:
        """Should detect COBOL data items."""
        (tmp_path / "data.cob").write_text("""
       IDENTIFICATION DIVISION.
       PROGRAM-ID. DATA-PROG.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-NAME PIC X(20).
       01 WS-AGE PIC 9(3).
       PROCEDURE DIVISION.
           STOP RUN.
""")

        result = analyze_cobol(tmp_path)

        data_items = [s for s in result.symbols if s.kind == "data"]
        names = [d.name for d in data_items]
        assert "WS-NAME" in names
        assert "WS-AGE" in names
        # Check level number is captured
        ws_name = next(d for d in data_items if d.name == "WS-NAME")
        assert ws_name.meta.get("level") == "01"

    def test_detects_sections(self, tmp_path: Path) -> None:
        """Should detect COBOL sections."""
        (tmp_path / "sections.cob").write_text("""
       IDENTIFICATION DIVISION.
       PROGRAM-ID. SECTION-TEST.
       PROCEDURE DIVISION.
       MAIN-SECTION SECTION.
       MAIN-PARA.
           DISPLAY "MAIN".
       INIT-SECTION SECTION.
       INIT-PARA.
           DISPLAY "INIT".
""")

        result = analyze_cobol(tmp_path)

        sections = [s for s in result.symbols if s.kind == "section"]
        names = [s.name for s in sections]
        assert "MAIN-SECTION" in names
        assert "INIT-SECTION" in names

    def test_detects_perform_edges(self, tmp_path: Path) -> None:
        """Should detect PERFORM calls between paragraphs."""
        (tmp_path / "perform.cob").write_text("""
       IDENTIFICATION DIVISION.
       PROGRAM-ID. PERFORM-TEST.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM INIT-PARA.
           PERFORM CALC-PARA.
           STOP RUN.
       INIT-PARA.
           DISPLAY "INIT".
       CALC-PARA.
           DISPLAY "CALC".
""")

        result = analyze_cobol(tmp_path)

        perform_edges = [e for e in result.edges if e.meta.get("call_type") == "perform"]
        assert len(perform_edges) >= 2
        targets = [e.dst for e in perform_edges]
        assert "INIT-PARA" in targets
        assert "CALC-PARA" in targets

    def test_detects_call_edges(self, tmp_path: Path) -> None:
        """Should detect CALL statements to external programs."""
        (tmp_path / "caller.cob").write_text("""
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CALLER-PROG.
       PROCEDURE DIVISION.
       MAIN-PARA.
           CALL "SUB-PROG" USING WS-DATA.
           CALL "UTIL-PROG".
           STOP RUN.
""")

        result = analyze_cobol(tmp_path)

        call_edges = [e for e in result.edges if e.meta.get("call_type") == "call"]
        assert len(call_edges) >= 2
        targets = [e.dst for e in call_edges]
        assert "SUB-PROG" in targets
        assert "UTIL-PROG" in targets

    def test_handles_cpy_copybooks(self, tmp_path: Path) -> None:
        """Should analyze COBOL copybook files when they have full program structure."""
        # Note: Standalone copybook fragments without IDENTIFICATION DIVISION
        # cannot be parsed by tree-sitter. Copybooks with full structure work.
        (tmp_path / "customer.cpy").write_text("""
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CUSTOMER-CPY.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 CUSTOMER-RECORD.
          05 CUST-ID PIC 9(8).
          05 CUST-NAME PIC X(30).
          05 CUST-BALANCE PIC S9(9)V99.
""")

        result = analyze_cobol(tmp_path)

        # Should find data items in copybook
        data_items = [s for s in result.symbols if s.kind == "data"]
        names = [d.name for d in data_items]
        assert "CUSTOMER-RECORD" in names or "CUST-ID" in names

    def test_handles_multiple_files(self, tmp_path: Path) -> None:
        """Should analyze multiple COBOL files."""
        (tmp_path / "main.cob").write_text("""
       IDENTIFICATION DIVISION.
       PROGRAM-ID. MAIN-PROG.
       PROCEDURE DIVISION.
           STOP RUN.
""")
        (tmp_path / "util.cbl").write_text("""
       IDENTIFICATION DIVISION.
       PROGRAM-ID. UTIL-PROG.
       PROCEDURE DIVISION.
           STOP RUN.
""")

        result = analyze_cobol(tmp_path)

        programs = [s for s in result.symbols if s.kind == "program"]
        names = [p.name for p in programs]
        assert "MAIN-PROG" in names
        assert "UTIL-PROG" in names

    def test_empty_repo_returns_empty_result(self, tmp_path: Path) -> None:
        """Should return empty result for repo with no COBOL files."""
        (tmp_path / "readme.txt").write_text("Not COBOL")

        result = analyze_cobol(tmp_path)

        assert result.symbols == []
        assert result.edges == []


class TestAnalyzeCOBOLFallback:
    """Tests for fallback when COBOL tree-sitter is not available."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Should return skipped result when tree-sitter not available."""
        # This test runs regardless of availability to test the warning path
        (tmp_path / "test.cob").write_text("IDENTIFICATION DIVISION.")

        # Temporarily make it unavailable by mocking
        import hypergumbo.analyze.cobol as cobol_mod

        original_func = cobol_mod.is_cobol_tree_sitter_available

        def mock_unavailable():
            return False

        cobol_mod.is_cobol_tree_sitter_available = mock_unavailable

        try:
            with pytest.warns(UserWarning, match="tree-sitter-language-pack"):
                result = analyze_cobol(tmp_path)
            assert result.skipped
            assert "not available" in result.skipped_reason
        finally:
            cobol_mod.is_cobol_tree_sitter_available = original_func
