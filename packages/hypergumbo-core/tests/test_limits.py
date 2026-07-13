# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for limits tracking."""

import pytest

from hypergumbo_core.limits import KNOWN_LIMITATIONS, Limits, FailedFile


class TestLimits:
    """Tests for Limits dataclass."""

    def test_empty_limits(self) -> None:
        """Empty limits produces minimal output with known limitations."""
        limits = Limits()
        d = limits.to_dict()

        # Known limitations always included (static analysis gaps)
        assert len(d["not_captured"]) > 0
        assert any("dynamic" in item.lower() for item in d["not_captured"])
        # INV-virik: empty diagnostic reporting lists are OMITTED, not present-as-[]
        # (their absence reads as "nothing dropped/failed").
        assert "truncated_files" not in d
        assert "skipped_languages" not in d
        assert "failed_files" not in d
        assert "hypergumbo" in d["analyzer_version"]

    def test_add_failed_file(self) -> None:
        """Can add failed files."""
        limits = Limits()
        limits.add_failed_file(
            path="broken.py",
            reason="SyntaxError: invalid syntax",
            analyzer="python",
        )

        d = limits.to_dict()

        assert len(d["failed_files"]) == 1
        assert d["failed_files"][0]["path"] == "broken.py"
        assert "SyntaxError" in d["failed_files"][0]["reason"]
        assert d["failed_files"][0]["analyzer"] == "python"

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
        limits1.add_failed_file("a.py", "error1", "python")

        limits2 = Limits()
        limits2.add_failed_file("b.js", "error2", "js")
        limits2.add_skipped_language("go")

        merged = limits1.merge(limits2)
        d = merged.to_dict()

        assert len(d["failed_files"]) == 2
        assert "go" in d["skipped_languages"]

    def test_analysis_depth_field_removed(self) -> None:
        """analysis_depth was a dead hardcoded "syntax_only" constant — never
        reassigned in any production path, factually false for the semantic maps
        hypergumbo produces, and read by zero consumers. Removed per the D9b
        delete-zero-entropy-field precedent (WI-muzus / its duplicate WI-zusok).
        The key must no longer be emitted and the dataclass must reject the
        kwarg, so a stale writer can't silently reintroduce it."""
        assert "analysis_depth" not in Limits().to_dict()
        with pytest.raises(TypeError):
            Limits(analysis_depth="syntax_only")  # type: ignore[call-arg]

    def test_not_captured_is_static_disclaimer_not_per_repo(self) -> None:
        """WI-togop: not_captured is a fixed universal disclaimer — identical
        regardless of this repo's per-analysis state (skipped languages, failed
        files). Two divergent Limits must serialize the SAME not_captured list,
        locking the static-by-design contract the docs describe."""
        loaded = Limits()
        loaded.add_skipped_language("go")
        loaded.add_failed_file("x.py", "boom", "python")
        bare = Limits()

        assert loaded.to_dict()["not_captured"] == bare.to_dict()["not_captured"]
        assert loaded.to_dict()["not_captured"] == KNOWN_LIMITATIONS

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

    def test_test_files_excluded_present_false_when_not_excluded(self) -> None:
        """WI-miron: test_files_excluded is always observable — present and
        False when tests were not excluded, so absence is never confused with
        'tests included'."""
        limits = Limits()  # Default is False
        d = limits.to_dict()

        assert d["test_files_excluded"] is False

    def test_partial_results_reason_omitted_when_empty(self) -> None:
        """WI-tamop: partial_results_reason follows the spec §960/§994
        conditional-presence contract — present only when the analysis is
        incomplete (a reason is set), absent otherwise."""
        d = Limits().to_dict()
        assert "partial_results_reason" not in d

    def test_partial_results_reason_present_when_set(self) -> None:
        """A set reason is emitted verbatim."""
        limits = Limits()
        limits.partial_results_reason = "one or more passes crashed; results are partial"
        d = limits.to_dict()
        assert d["partial_results_reason"] == "one or more passes crashed; results are partial"


class TestFailedFile:
    """Tests for FailedFile dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dict."""
        ff = FailedFile(
            path="broken.py",
            reason="SyntaxError",
            analyzer="python",
        )
        d = ff.to_dict()

        assert d["path"] == "broken.py"
        assert d["reason"] == "SyntaxError"
        assert d["analyzer"] == "python"
