# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/tracker-path-linter (WI-sihih).

Covers:
  - _extract_path_tokens: backtick, bare, trailing punct, dedupe
  - _looks_like_path: slash gate, URL skip
  - _strip_trailing_punct: various trailing punctuation
  - _resolve_under_repo: existing vs missing, absolute vs relative
  - _fuzzy_suggest: no match, single match, multi-match disambiguation
  - _lint_item: description + discussion, stale + fresh mix
  - LintReport.exit_code, to_dict
  - _format_text: empty, grouped
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def _import_linter():
    script_path = str(
        Path(__file__).parent.parent / "scripts" / "tracker-path-linter"
    )
    loader = importlib.machinery.SourceFileLoader(
        "tracker_path_linter", script_path,
    )
    spec = importlib.util.spec_from_loader(
        "tracker_path_linter", loader,
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tracker_path_linter"] = mod
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def linter_mod():
    return _import_linter()


class TestExtractPathTokens:
    def test_backtick_wrapped(self, linter_mod) -> None:
        tokens = linter_mod._extract_path_tokens(
            "See `packages/foo/bar.py` for details.",
        )
        assert "packages/foo/bar.py" in tokens

    def test_bare_token(self, linter_mod) -> None:
        tokens = linter_mod._extract_path_tokens(
            "Run scripts/auto-pr to create a PR.",
        )
        assert "scripts/auto-pr" in tokens

    def test_agent_path(self, linter_mod) -> None:
        tokens = linter_mod._extract_path_tokens(
            "Check .agent/hooks/_shared/on_transcript_change.py",
        )
        assert any(
            ".agent/hooks/_shared/on_transcript_change.py" in t
            for t in tokens
        )

    def test_trailing_punct_stripped(self, linter_mod) -> None:
        tokens = linter_mod._extract_path_tokens(
            "See `packages/foo/bar.py`. Or `scripts/other`,",
        )
        assert "packages/foo/bar.py" in tokens

    def test_dedupe(self, linter_mod) -> None:
        tokens = linter_mod._extract_path_tokens(
            "`scripts/foo` and scripts/foo are the same.",
        )
        # De-duped to a single entry.
        assert tokens.count("scripts/foo") == 1

    def test_empty_text(self, linter_mod) -> None:
        assert linter_mod._extract_path_tokens("") == []


class TestLooksLikePath:
    def test_with_slash(self, linter_mod) -> None:
        assert linter_mod._looks_like_path("packages/foo/bar.py") is True

    def test_without_slash(self, linter_mod) -> None:
        assert linter_mod._looks_like_path("Symbol") is False

    def test_http_url(self, linter_mod) -> None:
        assert (
            linter_mod._looks_like_path("http://example.com/foo")
            is False
        )

    def test_https_url(self, linter_mod) -> None:
        assert (
            linter_mod._looks_like_path("https://example.com/foo")
            is False
        )


class TestStripTrailingPunct:
    def test_period(self, linter_mod) -> None:
        assert linter_mod._strip_trailing_punct("a.py.") == "a.py"

    def test_comma(self, linter_mod) -> None:
        assert linter_mod._strip_trailing_punct("a.py,") == "a.py"

    def test_quote(self, linter_mod) -> None:
        assert linter_mod._strip_trailing_punct('a.py"') == "a.py"

    def test_none(self, linter_mod) -> None:
        assert linter_mod._strip_trailing_punct("a.py") == "a.py"


class TestResolveUnderRepo:
    def test_existing_relative(
        self, linter_mod, tmp_path: Path,
    ) -> None:
        (tmp_path / "foo.txt").write_text("hi")
        assert linter_mod._resolve_under_repo(tmp_path, "foo.txt") is True

    def test_missing_relative(
        self, linter_mod, tmp_path: Path,
    ) -> None:
        assert (
            linter_mod._resolve_under_repo(tmp_path, "nonexistent.txt")
            is False
        )

    def test_absolute_existing(
        self, linter_mod, tmp_path: Path,
    ) -> None:
        (tmp_path / "foo.txt").write_text("hi")
        abs_path = str(tmp_path / "foo.txt")
        assert linter_mod._resolve_under_repo(tmp_path, abs_path) is True

    def test_absolute_missing(self, linter_mod, tmp_path: Path) -> None:
        assert (
            linter_mod._resolve_under_repo(
                tmp_path, "/nowhere/at/all.txt",
            )
            is False
        )


class TestFuzzySuggest:
    def test_no_match(self, linter_mod) -> None:
        index: dict[str, list[str]] = {}
        assert (
            linter_mod._fuzzy_suggest("packages/foo/bar.py", index)
            is None
        )

    def test_single_match(self, linter_mod) -> None:
        index = {"bar.py": [".agent/hooks/bar.py"]}
        result = linter_mod._fuzzy_suggest(
            "packages/foo/bar.py", index,
        )
        assert result == ".agent/hooks/bar.py"

    def test_multi_match_prefers_longer_suffix(
        self, linter_mod,
    ) -> None:
        index = {
            "bar.py": [
                "unrelated/bar.py",
                "packages/foo/bar.py",
            ],
        }
        result = linter_mod._fuzzy_suggest(
            "packages/foo/bar.py", index,
        )
        # Perfect match wins.
        assert result == "packages/foo/bar.py"

    def test_multi_match_partial_suffix(self, linter_mod) -> None:
        index = {
            "x.py": [
                "a/b/x.py",
                "c/d/x.py",
            ],
        }
        result = linter_mod._fuzzy_suggest("c/d/x.py", index)
        assert result == "c/d/x.py"


class TestLintItem:
    def test_description_only_clean(
        self, linter_mod, tmp_path: Path,
    ) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "real.py").write_text("pass")
        item = {
            "id": "WI-test",
            "description": "See `src/real.py` for details.",
            "discussion": [],
        }
        report = linter_mod.LintReport()
        linter_mod._lint_item(item, tmp_path, {}, report)
        assert report.stale == []
        assert report.paths_checked == 1

    def test_stale_description_reference(
        self, linter_mod, tmp_path: Path,
    ) -> None:
        item = {
            "id": "WI-test",
            "description": "See `packages/ghost/nope.py` for details.",
            "discussion": [],
        }
        report = linter_mod.LintReport()
        linter_mod._lint_item(item, tmp_path, {}, report)
        assert len(report.stale) == 1
        assert report.stale[0].location == "description"
        assert "ghost/nope.py" in report.stale[0].normalized_path

    def test_stale_discussion_reference(
        self, linter_mod, tmp_path: Path,
    ) -> None:
        item = {
            "id": "WI-test",
            "description": "No path here.",
            "discussion": [
                {"message": "Found the bug in `scripts/ghost.py`."},
            ],
        }
        report = linter_mod.LintReport()
        linter_mod._lint_item(item, tmp_path, {}, report)
        assert len(report.stale) == 1
        assert report.stale[0].location == "discussion[0]"

    def test_text_field_alias_in_discussion(
        self, linter_mod, tmp_path: Path,
    ) -> None:
        """Discussion entries may use 'text' instead of 'message'."""
        item = {
            "id": "WI-test",
            "description": "",
            "discussion": [{"text": "See `packages/ghost.py`"}],
        }
        report = linter_mod.LintReport()
        linter_mod._lint_item(item, tmp_path, {}, report)
        assert len(report.stale) == 1

    def test_fuzzy_suggestion_populated(
        self, linter_mod, tmp_path: Path,
    ) -> None:
        (tmp_path / ".agent" / "hooks").mkdir(parents=True)
        (tmp_path / ".agent" / "hooks" / "foo.py").write_text("pass")
        index = {"foo.py": [".agent/hooks/foo.py"]}
        item = {
            "id": "WI-test",
            "description": "See `packages/old/foo.py` for details.",
            "discussion": [],
        }
        report = linter_mod.LintReport()
        linter_mod._lint_item(item, tmp_path, index, report)
        assert len(report.stale) == 1
        assert report.stale[0].suggested_path == ".agent/hooks/foo.py"

    def test_non_path_tokens_ignored(
        self, linter_mod, tmp_path: Path,
    ) -> None:
        item = {
            "id": "WI-test",
            "description": "Call `Symbol.from_dict` (not a file).",
            "discussion": [],
        }
        report = linter_mod.LintReport()
        linter_mod._lint_item(item, tmp_path, {}, report)
        # No slash → skipped by _looks_like_path gate.
        assert report.stale == []


class TestLintReport:
    def test_empty_exit_code_zero(self, linter_mod) -> None:
        report = linter_mod.LintReport()
        assert report.exit_code == 0

    def test_nonempty_exit_code_one(self, linter_mod) -> None:
        report = linter_mod.LintReport()
        report.stale.append(
            linter_mod.StaleRef(
                item_id="WI-test",
                location="description",
                raw_token="packages/x.py",
                normalized_path="packages/x.py",
            ),
        )
        assert report.exit_code == 1

    def test_to_dict(self, linter_mod) -> None:
        report = linter_mod.LintReport()
        report.items_scanned = 3
        report.paths_checked = 7
        report.stale.append(
            linter_mod.StaleRef(
                item_id="WI-test",
                location="description",
                raw_token="packages/x.py",
                normalized_path="packages/x.py",
                suggested_path="lib/x.py",
            ),
        )
        d = report.to_dict()
        assert d["items_scanned"] == 3
        assert d["paths_checked"] == 7
        assert d["exit_code"] == 1
        assert d["stale_references"][0]["suggested_path"] == "lib/x.py"


class TestFormatText:
    def test_clean_report(self, linter_mod) -> None:
        report = linter_mod.LintReport()
        report.items_scanned = 5
        text = linter_mod._format_text(report)
        assert "No stale references" in text

    def test_stale_report_grouped(self, linter_mod) -> None:
        report = linter_mod.LintReport()
        report.items_scanned = 1
        report.paths_checked = 2
        report.stale.append(
            linter_mod.StaleRef(
                item_id="WI-a",
                location="description",
                raw_token="x.py",
                normalized_path="x.py",
                suggested_path="real/x.py",
            ),
        )
        report.stale.append(
            linter_mod.StaleRef(
                item_id="WI-a",
                location="discussion[0]",
                raw_token="y.py",
                normalized_path="y.py",
            ),
        )
        text = linter_mod._format_text(report)
        assert "[WI-a]" in text
        assert "real/x.py" in text
        assert "1 items, 2 paths checked" in text
        assert "2 stale references" in text


class TestBuildBasenameIndex:
    def test_reads_git_ls_files(
        self, linter_mod, tmp_path: Path, monkeypatch,
    ) -> None:
        """Mocks git ls-files output and confirms index shape."""
        import subprocess as _sp

        class _Result:
            stdout = "scripts/foo.py\nscripts/bar.py\npackages/a/foo.py\n"
            returncode = 0

        def _fake_run(*args, **kwargs):
            return _Result()

        monkeypatch.setattr(_sp, "run", _fake_run)
        index = linter_mod._build_basename_index(tmp_path)
        assert "foo.py" in index
        assert "bar.py" in index
        assert len(index["foo.py"]) == 2

    def test_git_unavailable_returns_empty(
        self, linter_mod, tmp_path: Path, monkeypatch,
    ) -> None:
        """CalledProcessError path returns an empty index."""
        import subprocess as _sp

        def _fake_run(*args, **kwargs):
            raise _sp.CalledProcessError(1, args[0])

        monkeypatch.setattr(_sp, "run", _fake_run)
        assert linter_mod._build_basename_index(tmp_path) == {}


class TestLintEntrypoint:
    def test_lint_end_to_end(
        self, linter_mod, tmp_path: Path, monkeypatch,
    ) -> None:
        """Mocks _enumerate_items, _full_item, and _build_basename_index
        to drive a full lint pass without touching the real tracker."""
        fake_items = [
            {
                "id": "WI-a",
                "description": "See `packages/x.py`.",
                "discussion": [],
            },
        ]
        monkeypatch.setattr(
            linter_mod, "_enumerate_items",
            lambda cli, s, p: fake_items,
        )
        monkeypatch.setattr(
            linter_mod, "_full_item",
            lambda cli, iid: fake_items[0],
        )
        monkeypatch.setattr(
            linter_mod, "_build_basename_index",
            lambda r: {},
        )

        report = linter_mod.lint(
            repo_root=tmp_path, tracker_cli="fake",
        )
        assert report.items_scanned == 1
        assert len(report.stale) == 1
