"""Tests for limits tracking."""

from hypergumbo.limits import Limits, FailedFile


class TestLimits:
    """Tests for Limits dataclass."""

    def test_empty_limits(self) -> None:
        """Empty limits produces minimal output with known limitations."""
        limits = Limits()
        d = limits.to_dict()

        # Known limitations always included (static analysis gaps)
        assert len(d["not_captured"]) > 0
        assert any("dynamic" in item.lower() for item in d["not_captured"])
        assert d["truncated_files"] == []
        assert d["skipped_languages"] == []
        assert d["failed_files"] == []
        assert "hypergumbo" in d["analyzer_version"]

    def test_add_failed_file(self) -> None:
        """Can add failed files."""
        limits = Limits()
        limits.add_failed_file(
            path="broken.py",
            reason="SyntaxError: invalid syntax",
            analyzer="python-ast-v1",
        )

        d = limits.to_dict()

        assert len(d["failed_files"]) == 1
        assert d["failed_files"][0]["path"] == "broken.py"
        assert "SyntaxError" in d["failed_files"][0]["reason"]
        assert d["failed_files"][0]["analyzer"] == "python-ast-v1"

    def test_add_skipped_language(self) -> None:
        """Can add skipped languages."""
        limits = Limits()
        limits.add_skipped_language("go")
        limits.add_skipped_language("rust")

        d = limits.to_dict()

        assert "go" in d["skipped_languages"]
        assert "rust" in d["skipped_languages"]

    def test_add_truncated_file(self) -> None:
        """Can add truncated files."""
        limits = Limits()
        limits.add_truncated_file(
            path="large.py",
            size_bytes=10_000_000,
            reason="exceeds 5MB limit",
        )

        d = limits.to_dict()

        assert len(d["truncated_files"]) == 1
        assert d["truncated_files"][0]["path"] == "large.py"
        assert d["truncated_files"][0]["size_bytes"] == 10_000_000
        assert "5MB" in d["truncated_files"][0]["reason"]

    def test_not_captured_includes_known_limitations(self) -> None:
        """Not captured includes known analyzer limitations."""
        limits = Limits()
        d = limits.to_dict()

        # These are fundamental limitations of static analysis
        assert any("dynamic" in item.lower() for item in d["not_captured"])

    def test_merge_limits(self) -> None:
        """Can merge limits from multiple analyzers."""
        limits1 = Limits()
        limits1.add_failed_file("a.py", "error1", "python-ast-v1")

        limits2 = Limits()
        limits2.add_failed_file("b.js", "error2", "js-ts-v1")
        limits2.add_skipped_language("go")

        merged = limits1.merge(limits2)
        d = merged.to_dict()

        assert len(d["failed_files"]) == 2
        assert "go" in d["skipped_languages"]

    def test_analysis_depth(self) -> None:
        """Tracks analysis depth."""
        limits = Limits(analysis_depth="syntax_only")
        d = limits.to_dict()

        assert d["analysis_depth"] == "syntax_only"

    def test_max_files_per_analyzer(self) -> None:
        """Tracks max_files_per_analyzer in output."""
        limits = Limits()
        limits.max_files_per_analyzer = 100
        d = limits.to_dict()

        assert d["max_files_per_analyzer"] == 100

    def test_test_files_excluded(self) -> None:
        """Tracks test_files_excluded in output."""
        limits = Limits()
        limits.test_files_excluded = True
        d = limits.to_dict()

        assert d["test_files_excluded"] is True

    def test_test_files_excluded_not_in_output_when_false(self) -> None:
        """test_files_excluded not included in output when False."""
        limits = Limits()  # Default is False
        d = limits.to_dict()

        assert "test_files_excluded" not in d


class TestFailedFile:
    """Tests for FailedFile dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dict."""
        ff = FailedFile(
            path="broken.py",
            reason="SyntaxError",
            analyzer="python-ast-v1",
        )
        d = ff.to_dict()

        assert d["path"] == "broken.py"
        assert d["reason"] == "SyntaxError"
        assert d["analyzer"] == "python-ast-v1"
