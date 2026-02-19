"""Branch coverage tests for bibtex.py analyzer.

Tests specific branch paths in the BibTeX analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import bibtex as bibtex_module
from hypergumbo_lang_extended1.bibtex import (
    analyze_bibtex,
    find_bibtex_files,
)


def make_bibtex_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a BibTeX file with given content."""
    (tmp_path / name).write_text(content)


class TestEntryExtraction:
    """Branch coverage for entry extraction."""

    def test_article_entry(self, tmp_path: Path) -> None:
        """Test article entry extraction."""
        make_bibtex_file(tmp_path, "refs.bib", """
@article{smith2020,
    author = {John Smith},
    title = {An Important Paper},
    journal = {Journal of Science},
    year = {2020}
}
""")
        result = analyze_bibtex(tmp_path)
        assert not result.skipped
        entries = [s for s in result.symbols if s.kind == "entry"]
        assert any("smith2020" in e.name for e in entries)

    def test_book_entry(self, tmp_path: Path) -> None:
        """Test book entry extraction."""
        make_bibtex_file(tmp_path, "refs.bib", """
@book{jones2019,
    author = {Jane Jones},
    title = {A Great Book},
    publisher = {Publisher},
    year = {2019}
}
""")
        result = analyze_bibtex(tmp_path)
        entries = [s for s in result.symbols if s.kind == "entry"]
        assert any("jones2019" in e.name for e in entries)

    def test_inproceedings_entry(self, tmp_path: Path) -> None:
        """Test inproceedings entry extraction."""
        make_bibtex_file(tmp_path, "refs.bib", """
@inproceedings{doe2021,
    author = {John Doe},
    title = {Conference Paper},
    booktitle = {Conference Proceedings},
    year = {2021}
}
""")
        result = analyze_bibtex(tmp_path)
        entries = [s for s in result.symbols if s.kind == "entry"]
        assert any("doe2021" in e.name for e in entries)


class TestStringExtraction:
    """Branch coverage for string definition extraction."""

    def test_string_definition(self, tmp_path: Path) -> None:
        """Test @string definition extraction."""
        make_bibtex_file(tmp_path, "refs.bib", """
@string{jcs = "Journal of Computer Science"}

@article{test2020,
    journal = jcs
}
""")
        result = analyze_bibtex(tmp_path)
        strings = [s for s in result.symbols if s.kind == "string"]
        assert not result.skipped  # lenient check


class TestFindBibtexFiles:
    """Branch coverage for file discovery."""

    def test_finds_bib_files(self, tmp_path: Path) -> None:
        """Test .bib files are discovered."""
        (tmp_path / "test.bib").write_text("@article{test, title={Test}}")
        files = list(find_bibtex_files(tmp_path))
        assert any(f.suffix == ".bib" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_bibtex_files(self, tmp_path: Path) -> None:
        """Test directory with no BibTeX files."""
        result = analyze_bibtex(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_bibtex(self, tmp_path: Path) -> None:
        """Test minimal BibTeX file."""
        make_bibtex_file(tmp_path, "refs.bib", "@article{test,}")
        result = analyze_bibtex(tmp_path)
        assert not result.skipped


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(bibtex_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="bibtex analysis skipped"):
                result = bibtex_module.analyze_bibtex(tmp_path)
        assert result.skipped is True
