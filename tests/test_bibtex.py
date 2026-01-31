"""Tests for the BibTeX bibliography analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import bibtex as bibtex_module
from hypergumbo_lang_extended1.bibtex import (
    BibtexAnalysisResult,
    analyze_bibtex,
    find_bibtex_files,
    is_bibtex_tree_sitter_available,
)


def make_bibtex_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a BibTeX file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindBibtexFiles:
    """Tests for find_bibtex_files function."""

    def test_finds_bib_files(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bib", "@article{test, author={A}}")
        make_bibtex_file(tmp_path, "docs/bibliography.bib", "@book{test2, author={B}}")
        files = find_bibtex_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"refs.bib", "bibliography.bib"}

    def test_finds_bibtex_extension(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bibtex", "@article{test, author={A}}")
        files = find_bibtex_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "refs.bibtex"

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_bibtex_files(tmp_path)
        assert files == []


class TestIsBibtexTreeSitterAvailable:
    """Tests for is_bibtex_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_bibtex_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(bibtex_module, "is_bibtex_tree_sitter_available", return_value=False):
            assert bibtex_module.is_bibtex_tree_sitter_available() is False


class TestAnalyzeBibtex:
    """Tests for analyze_bibtex function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bib", "@article{test, author={A}}")
        with patch.object(bibtex_module, "is_bibtex_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="BibTeX analysis skipped"):
                result = bibtex_module.analyze_bibtex(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_bibtex(tmp_path)
        assert result.symbols == []
        assert result.run is None

    def test_extracts_article(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bib", """@article{smith2020,
  author = {John Smith},
  title = {A Study},
  journal = {Journal of Testing},
  year = {2020}
}""")
        result = analyze_bibtex(tmp_path)
        assert not result.skipped
        entry = next((s for s in result.symbols if s.kind == "entry"), None)
        assert entry is not None
        assert entry.name == "smith2020"
        assert entry.meta.get("entry_type") == "article"
        assert entry.meta.get("author") == "John Smith"
        assert entry.meta.get("year") == "2020"

    def test_extracts_book(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bib", """@book{jones2019,
  author = {Bob Jones},
  title = {Deep Learning},
  publisher = {Tech Press},
  year = {2019}
}""")
        result = analyze_bibtex(tmp_path)
        entry = next((s for s in result.symbols if s.kind == "entry"), None)
        assert entry is not None
        assert entry.name == "jones2019"
        assert entry.meta.get("entry_type") == "book"

    def test_extracts_inproceedings(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bib", """@inproceedings{wilson2021,
  author = {Alice Wilson},
  title = {Neural Networks},
  booktitle = {Proceedings of ICML},
  year = {2021}
}""")
        result = analyze_bibtex(tmp_path)
        entry = next((s for s in result.symbols if s.kind == "entry"), None)
        assert entry is not None
        assert entry.meta.get("entry_type") == "inproceedings"

    def test_extracts_multiple_entries(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bib", """@article{smith2020,
  author = {John Smith},
  title = {Study A},
  year = {2020}
}

@book{jones2019,
  author = {Bob Jones},
  title = {Book B},
  year = {2019}
}

@inproceedings{wilson2021,
  author = {Alice Wilson},
  title = {Paper C},
  year = {2021}
}""")
        result = analyze_bibtex(tmp_path)
        entries = [s for s in result.symbols if s.kind == "entry"]
        assert len(entries) == 3
        keys = {e.name for e in entries}
        assert keys == {"smith2020", "jones2019", "wilson2021"}

    def test_field_count(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bib", """@article{test,
  author = {A},
  title = {T},
  journal = {J},
  year = {2020},
  volume = {1}
}""")
        result = analyze_bibtex(tmp_path)
        entry = next((s for s in result.symbols if s.kind == "entry"), None)
        assert entry is not None
        assert entry.meta.get("field_count") == 5

    def test_long_title_truncation(self, tmp_path: Path) -> None:
        long_title = "A Very Long Title That Exceeds Fifty Characters And Should Be Truncated"
        make_bibtex_file(tmp_path, "refs.bib", f"""@article{{test,
  author = {{A}},
  title = {{{long_title}}},
  year = {{2020}}
}}""")
        result = analyze_bibtex(tmp_path)
        entry = next((s for s in result.symbols if s.kind == "entry"), None)
        assert entry is not None
        assert len(entry.meta.get("title", "")) <= 50

    def test_signature_format(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bib", """@article{test2020,
  author = {A},
  year = {2020}
}""")
        result = analyze_bibtex(tmp_path)
        entry = next((s for s in result.symbols if s.kind == "entry"), None)
        assert entry is not None
        assert "@article{test2020}" in entry.signature

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bib", "@article{test, author={A}}")
        result = analyze_bibtex(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "bibtex.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0
        assert result.run.files_analyzed == 1

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs1.bib", "@article{a, author={A}}")
        make_bibtex_file(tmp_path, "refs2.bib", "@book{b, author={B}}")
        result = analyze_bibtex(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 2

    def test_pass_id(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bib", "@article{test, author={A}}")
        result = analyze_bibtex(tmp_path)
        entry = next((s for s in result.symbols if s.kind == "entry"), None)
        assert entry is not None
        assert entry.origin == "bibtex.tree_sitter"

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bib", "@article{test, author={A}}")
        result = analyze_bibtex(tmp_path)
        entry = next((s for s in result.symbols if s.kind == "entry"), None)
        assert entry is not None
        assert entry.id == entry.stable_id
        assert "bibtex:" in entry.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bib", "@article{test, author={A}}")
        result = analyze_bibtex(tmp_path)
        entry = next((s for s in result.symbols if s.kind == "entry"), None)
        assert entry is not None
        assert entry.span is not None
        assert entry.span.start_line >= 1

    def test_unknown_author(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bib", "@article{test, title={T}, year={2020}}")
        result = analyze_bibtex(tmp_path)
        entry = next((s for s in result.symbols if s.kind == "entry"), None)
        assert entry is not None
        assert entry.meta.get("author") == "Unknown"

    def test_edges_empty(self, tmp_path: Path) -> None:
        make_bibtex_file(tmp_path, "refs.bib", "@article{test, author={A}}")
        result = analyze_bibtex(tmp_path)
        assert result.edges == []
