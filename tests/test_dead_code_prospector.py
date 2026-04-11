# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/dead-code-prospector-run.py.

Coverage focus: WI-zafab filter 1 (polyglot-only check at the harness
level). The full prospecting flow is integration-tested via real
hypergumbo invocations during the bakeoff; here we exercise the
language-counting and polyglot-detection helpers in isolation so
regressions are caught without depending on external repos.
"""
from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path


def _load_prospector():
    """Import scripts/dead-code-prospector-run.py as a module despite no .py extension on the canonical name.

    The script DOES have a .py extension, but it lives outside any package
    and importlib.import_module won't find it without help.
    """
    script_path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "dead-code-prospector-run.py"
    )
    loader = importlib.machinery.SourceFileLoader(
        "dead_code_prospector", str(script_path),
    )
    spec = importlib.util.spec_from_loader("dead_code_prospector", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


prospector = _load_prospector()


# ---------------------------------------------------------------------------
# Tests: _count_languages_by_extension
# ---------------------------------------------------------------------------


class TestCountLanguagesByExtension:
    """Verify file-extension language counting."""

    def test_counts_python_files(self, tmp_path: Path) -> None:
        """Python files are counted under the python language label."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("def f(): pass")
        (tmp_path / "src" / "b.py").write_text("def g(): pass")
        counts = prospector._count_languages_by_extension(tmp_path)
        assert counts == {"python": 2}

    def test_counts_multiple_languages(self, tmp_path: Path) -> None:
        """A polyglot repo aggregates files by language."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.go").write_text("package main")
        (tmp_path / "src" / "main.py").write_text("def f(): pass")
        (tmp_path / "src" / "client.ts").write_text("export const x = 1")
        counts = prospector._count_languages_by_extension(tmp_path)
        assert counts == {"go": 1, "python": 1, "typescript": 1}

    def test_skips_node_modules(self, tmp_path: Path) -> None:
        """Vendored dependencies under ``node_modules/`` do NOT count.

        Without the ignore filter, a Python repo with a single test fixture
        ``node_modules/`` would be falsely promoted to polyglot by counting
        thousands of vendored .js files.
        """
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("def f(): pass")
        (tmp_path / "node_modules" / "lib").mkdir(parents=True)
        for i in range(20):
            (tmp_path / "node_modules" / "lib" / f"vendor{i}.js").write_text(
                "export const x = 1",
            )
        counts = prospector._count_languages_by_extension(tmp_path)
        assert counts == {"python": 1}, (
            f"node_modules vendored files must be ignored; got {counts}"
        )

    def test_skips_vendor_directory(self, tmp_path: Path) -> None:
        """Go's vendor/ directory is also ignored."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.go").write_text("package main")
        (tmp_path / "vendor" / "github.com" / "foo").mkdir(parents=True)
        for i in range(20):
            (tmp_path / "vendor" / "github.com" / "foo" / f"v{i}.go").write_text(
                "package foo",
            )
        counts = prospector._count_languages_by_extension(tmp_path)
        assert counts == {"go": 1}

    def test_unknown_extensions_ignored(self, tmp_path: Path) -> None:
        """Files with extensions not in the language map are silently dropped."""
        (tmp_path / "Makefile").write_text("all:")
        (tmp_path / "README.md").write_text("# repo")
        (tmp_path / "config.toml").write_text("[section]")
        (tmp_path / "main.py").write_text("def f(): pass")
        counts = prospector._count_languages_by_extension(tmp_path)
        assert counts == {"python": 1}


# ---------------------------------------------------------------------------
# Tests: _is_polyglot_repo
# ---------------------------------------------------------------------------


class TestIsPolyglotRepo:
    """Verify the polyglot threshold logic."""

    def test_monoglot_python_returns_false(self) -> None:
        """A pure Python repo is monoglot."""
        assert prospector._is_polyglot_repo({"python": 100}) is False

    def test_two_above_threshold_is_polyglot(self) -> None:
        """Two languages above the default threshold (10) → polyglot."""
        assert prospector._is_polyglot_repo({"python": 50, "go": 30}) is True

    def test_one_below_threshold_is_monoglot(self) -> None:
        """One language above the threshold and another below → monoglot.

        A Go repo with 5 stray test fixtures in JS should still be considered
        monoglot Go for prospecting purposes.
        """
        assert prospector._is_polyglot_repo({"go": 200, "javascript": 5}) is False

    def test_custom_threshold_lower(self) -> None:
        """Threshold can be lowered for tests/small fixtures."""
        # With threshold=2, two languages with 3+ files each is polyglot.
        assert prospector._is_polyglot_repo(
            {"go": 3, "python": 3}, threshold=2,
        ) is True
        # With default threshold=10, the same counts are monoglot.
        assert prospector._is_polyglot_repo({"go": 3, "python": 3}) is False

    def test_empty_returns_false(self) -> None:
        """A repo with no detected source files is not polyglot."""
        assert prospector._is_polyglot_repo({}) is False


# ---------------------------------------------------------------------------
# Tests: run_prospecting integration with the polyglot filter
# ---------------------------------------------------------------------------


class TestRunProspectingPolyglotFilter:
    """Verify that monoglot repos are skipped by default and surfaced
    in the summary, and that --include-monoglot overrides the skip.
    """

    def test_monoglot_repo_skipped_by_default(self, tmp_path: Path) -> None:
        """A pure Python repo is skipped from the prospecting run."""
        pool = tmp_path / "pool"
        pool.mkdir()
        repo = pool / "monorepo"
        repo.mkdir()
        # 50 Python files, no other languages
        for i in range(50):
            (repo / f"mod{i}.py").write_text("def f(): pass")
        output_dir = tmp_path / "out"

        summary = prospector.run_prospecting(
            pool, ["monorepo"], output_dir, include_monoglot=False,
        )

        assert summary["repos_analyzed"] == [], (
            f"Monoglot repo must be skipped, got {summary['repos_analyzed']}"
        )
        assert len(summary["repos_skipped_monoglot"]) == 1
        skipped = summary["repos_skipped_monoglot"][0]
        assert skipped["repo"] == "monorepo"
        assert skipped["languages"] == {"python": 50}

    def test_monoglot_repo_analyzed_with_include_flag(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """``include_monoglot=True`` overrides the skip and runs hypergumbo.

        We monkey-patch ``_run_hypergumbo`` to return a fake successful result
        so the test does not depend on the actual hypergumbo CLI being
        installed in the test environment.
        """
        pool = tmp_path / "pool"
        pool.mkdir()
        repo = pool / "monorepo"
        repo.mkdir()
        for i in range(50):
            (repo / f"mod{i}.py").write_text("def f(): pass")
        output_dir = tmp_path / "out"

        fake_result = {
            "summary": {"total_candidates": 0},
            "dead_candidates": [],
        }
        monkeypatch.setattr(
            prospector, "_run_hypergumbo", lambda repo_path: fake_result,
        )

        summary = prospector.run_prospecting(
            pool, ["monorepo"], output_dir, include_monoglot=True,
        )

        assert summary["repos_analyzed"] == ["monorepo"], (
            f"--include-monoglot should bypass the filter; "
            f"got analyzed={summary['repos_analyzed']}, "
            f"skipped={summary['repos_skipped_monoglot']}"
        )
        assert summary["repos_skipped_monoglot"] == []

    def test_polyglot_repo_analyzed_by_default(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """A genuinely polyglot repo (Go + Python) is analyzed without the override."""
        pool = tmp_path / "pool"
        pool.mkdir()
        repo = pool / "polyrepo"
        repo.mkdir()
        for i in range(15):
            (repo / f"go{i}.go").write_text("package main")
        for i in range(15):
            (repo / f"py{i}.py").write_text("def f(): pass")
        output_dir = tmp_path / "out"

        fake_result = {
            "summary": {"total_candidates": 0},
            "dead_candidates": [],
        }
        monkeypatch.setattr(
            prospector, "_run_hypergumbo", lambda repo_path: fake_result,
        )

        summary = prospector.run_prospecting(
            pool, ["polyrepo"], output_dir, include_monoglot=False,
        )

        assert summary["repos_analyzed"] == ["polyrepo"]
        assert summary["repos_skipped_monoglot"] == []
