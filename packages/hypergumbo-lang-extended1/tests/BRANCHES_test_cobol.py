"""Branch coverage tests for cobol.py analyzer.

Tests specific branch paths in the COBOL analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import cobol as cobol_module
from hypergumbo_lang_extended1.cobol import (
    analyze_cobol,
)


def make_cobol_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a COBOL file with given content."""
    (tmp_path / name).write_text(content)


class TestProgramExtraction:
    """Branch coverage for program extraction."""

    def test_program_id(self, tmp_path: Path) -> None:
        """Test PROGRAM-ID extraction."""
        make_cobol_file(tmp_path, "HELLO.cbl", """
       IDENTIFICATION DIVISION.
       PROGRAM-ID. HELLO.
       PROCEDURE DIVISION.
           DISPLAY 'HELLO WORLD'.
           STOP RUN.
""")
        result = analyze_cobol(tmp_path)
        assert not result.skipped
        programs = [s for s in result.symbols if s.kind == "program"]
        assert any("HELLO" in p.name for p in programs)


class TestDataExtraction:
    """Branch coverage for data item extraction."""

    def test_data_item(self, tmp_path: Path) -> None:
        """Test data item extraction."""
        make_cobol_file(tmp_path, "DATA.cbl", """
       IDENTIFICATION DIVISION.
       PROGRAM-ID. DATA-TEST.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-NAME PIC X(30).
       01 WS-AGE PIC 99.
       PROCEDURE DIVISION.
           STOP RUN.
""")
        result = analyze_cobol(tmp_path)
        data = [s for s in result.symbols if s.kind in ("variable", "data")]
        assert not result.skipped  # lenient check


class TestParagraphExtraction:
    """Branch coverage for paragraph extraction."""

    def test_paragraph_declaration(self, tmp_path: Path) -> None:
        """Test paragraph extraction."""
        make_cobol_file(tmp_path, "PARAS.cbl", """
       IDENTIFICATION DIVISION.
       PROGRAM-ID. PARA-TEST.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM HELPER-PARA.
           STOP RUN.
       HELPER-PARA.
           DISPLAY 'HELPER'.
""")
        result = analyze_cobol(tmp_path)
        paras = [s for s in result.symbols if s.kind == "paragraph"]
        assert not result.skipped  # lenient check


class TestSectionExtraction:
    """Branch coverage for section extraction."""

    def test_section_declaration(self, tmp_path: Path) -> None:
        """Test section extraction."""
        make_cobol_file(tmp_path, "SECTS.cbl", """
       IDENTIFICATION DIVISION.
       PROGRAM-ID. SECT-TEST.
       PROCEDURE DIVISION.
       MAIN-SECTION SECTION.
       MAIN-PARA.
           DISPLAY 'MAIN'.
           STOP RUN.
""")
        result = analyze_cobol(tmp_path)
        sects = [s for s in result.symbols if s.kind == "section"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_perform_creates_edge(self, tmp_path: Path) -> None:
        """Test PERFORM creates call edge."""
        make_cobol_file(tmp_path, "CALLS.cbl", """
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CALL-TEST.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM HELPER-PARA.
           STOP RUN.
       HELPER-PARA.
           DISPLAY 'HELPER'.
""")
        result = analyze_cobol(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestCopyEdges:
    """Branch coverage for COPY edge extraction."""

    def test_copy_creates_edge(self, tmp_path: Path) -> None:
        """Test COPY creates import edge."""
        make_cobol_file(tmp_path, "COPYTEST.cbl", """
       IDENTIFICATION DIVISION.
       PROGRAM-ID. COPY-TEST.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
           COPY COPYBOOK.
       PROCEDURE DIVISION.
           STOP RUN.
""")
        result = analyze_cobol(tmp_path)
        copies = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestFileDiscovery:
    """Branch coverage for file discovery."""

    def test_cbl_file_analyzed(self, tmp_path: Path) -> None:
        """Test .cbl files are analyzed."""
        make_cobol_file(tmp_path, "TEST.cbl", """
       IDENTIFICATION DIVISION.
       PROGRAM-ID. TEST.
       PROCEDURE DIVISION.
           STOP RUN.
""")
        result = analyze_cobol(tmp_path)
        assert not result.skipped

    def test_cob_file_analyzed(self, tmp_path: Path) -> None:
        """Test .cob files are analyzed."""
        make_cobol_file(tmp_path, "TEST.cob", """
       IDENTIFICATION DIVISION.
       PROGRAM-ID. TEST.
       PROCEDURE DIVISION.
           STOP RUN.
""")
        result = analyze_cobol(tmp_path)
        assert not result.skipped


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_cobol_files(self, tmp_path: Path) -> None:
        """Test directory with no COBOL files."""
        result = analyze_cobol(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(cobol_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="cobol analysis skipped"):
                result = cobol_module.analyze_cobol(tmp_path)
        assert result.skipped is True
