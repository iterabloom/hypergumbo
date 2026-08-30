# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/smart-test``'s TDD-mode test-file selection.

When a change contains no sliceable source (nothing under ``packages/*/src/``),
smart-test falls back to "TDD mode": run the test files that changed, on the
theory that a test written before its implementation still needs to execute.
That fallback matched only ``packages/*/tests/`` and ignored root-level
``tests/``, so a PR touching **only** root tests selected nothing, the manifest
was written with ``Selected tests: 0``, and ``ci.yml`` skipped pytest entirely.
A new top-level test could therefore merge having never run in CI — green
locally, vacuous in the gate. The reverse-slice cannot rescue those either,
because it walks ``packages/*/src/**``.

This file pins the selection pattern by **extracting the real regex out of the
script** and running it through ``grep -E``, the same way the script does,
rather than restating it here. A copy would be free to drift from what ships;
an extract cannot. It is the same technique used in ``tests/test_auto_pr.py``.

``scripts/smart-test`` had no tests before this file. Its name is deliberate:
the WI-jozan mapper resolves ``scripts/smart-test`` to ``tests/test_smart_test*``,
so future changes to the script select these tests instead of nothing.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SMART_TEST = REPO_ROOT / "scripts" / "smart-test"


def _changed_test_files_pattern() -> str:
    """Pull the live ``CHANGED_TEST_FILES`` regex out of the script."""
    for line in SMART_TEST.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("CHANGED_TEST_FILES=") and "grep -E" in stripped:
            match = re.search(r"grep -E '([^']+)'", stripped)
            assert match, f"could not parse the regex out of: {stripped}"
            return match.group(1)
    raise AssertionError("CHANGED_TEST_FILES assignment not found in smart-test")


def _selects(path: str) -> bool:
    """True when the script's own pattern would select ``path``."""
    result = subprocess.run(
        ["grep", "-E", _changed_test_files_pattern()],
        input=path, capture_output=True, text=True,
    )
    return result.returncode == 0


class TestTddModeSelection:
    """Which changed files count as "a test to run" when nothing is sliceable."""

    def test_root_level_test_is_selected(self) -> None:
        """The regression this pattern was widened to fix."""
        assert _selects("tests/test_auto_pr.py")
        assert _selects("tests/test_forge_github_harness.py")

    def test_package_test_is_still_selected(self) -> None:
        """Widening must not cost the original behaviour."""
        assert _selects("packages/hypergumbo-core/tests/test_finalize.py")

    def test_branches_test_prefix_is_still_selected(self) -> None:
        """The repo's second test-file convention."""
        assert _selects(
            "packages/hypergumbo-core/tests/BRANCHES_test_schema.py"
        )

    def test_source_files_are_not_selected(self) -> None:
        """TDD mode is for tests; sources go through the reverse-slice."""
        assert not _selects("packages/hypergumbo-core/src/hypergumbo_core/ir.py")
        assert not _selects("scripts/auto-pr")
        assert not _selects("CHANGELOG.md")

    def test_non_test_modules_under_tests_are_not_selected(self) -> None:
        """Helpers and conftest are not themselves runnable test files.

        ``tests/_forge_github_harness.py`` is imported by real test files;
        selecting it directly would hand pytest a module it does not collect.
        """
        assert not _selects("tests/_forge_github_harness.py")
        assert not _selects("tests/conftest.py")

    def test_pattern_is_anchored_at_the_repo_root(self) -> None:
        """A nested path that merely contains ``tests/`` must not match."""
        assert not _selects("vendor/foo/tests/test_bar.py")
        assert not _selects("docs/tests/test_bar.py")


def _extract_grep_pattern(var_name: str) -> str:
    """Pull the live ``grep -E`` regex off a ``VAR=$(...)`` assignment."""
    for line in SMART_TEST.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{var_name}=") and "grep -E" in stripped:
            match = re.search(r"grep -E '([^']+)'", stripped)
            assert match, f"could not parse the regex out of: {stripped}"
            return match.group(1)
    raise AssertionError(f"{var_name} assignment not found in smart-test")


def _pattern_selects(pattern: str, path: str) -> bool:
    result = subprocess.run(
        ["grep", "-E", pattern], input=path, capture_output=True, text=True
    )
    return result.returncode == 0


class TestTopLevelSurfacePattern:
    """INV-lizor: the top-level executable surface the gate must never
    silently skip — scripts/ (any depth), .githooks/, .agent/hooks/ — with
    the unmapped remainder routed to the root-suite fallback."""

    def test_covers_the_verified_blind_spots(self) -> None:
        pattern = _extract_grep_pattern("CHANGED_TOP_LEVEL_SOURCES")
        assert _pattern_selects(pattern, "scripts/lib/forgejo-api.sh")
        assert _pattern_selects(pattern, ".githooks/reference-transaction")
        assert _pattern_selects(pattern, ".agent/hooks/cursor/session-start.sh")
        assert _pattern_selects(pattern, ".agent/hooks/_shared/sub/helper.sh")
        assert _pattern_selects(pattern, "scripts/smart-test")

    def test_does_not_cover_non_executable_surfaces(self) -> None:
        pattern = _extract_grep_pattern("CHANGED_TOP_LEVEL_SOURCES")
        assert not _pattern_selects(pattern, "CHANGELOG.md")
        assert not _pattern_selects(pattern, "docs/hypergumbo-spec.md")
        assert not _pattern_selects(
            pattern, "packages/hypergumbo-core/src/hypergumbo_core/ir.py"
        )
        assert not _pattern_selects(
            pattern, ".agent/tracker-workspace/.ops/.WI-x.ops"
        )

    def test_dead_variable_comment_is_gone(self) -> None:
        """The pre-fix script defined CHANGED_TOP_LEVEL_SOURCES, read it
        nowhere, and carried a comment describing a check that did not
        exist. The variable must now be consumed."""
        text = SMART_TEST.read_text()
        assert "included via a separate check" not in text
        assert text.count("CHANGED_TOP_LEVEL_SOURCES") >= 2, (
            "CHANGED_TOP_LEVEL_SOURCES is defined but never read — the "
            "dead-variable defect INV-lizor documented"
        )

    def test_root_suite_fallback_is_wired(self) -> None:
        """Unmapped top-level changes must over-select (root suite), never
        silently skip."""
        text = SMART_TEST.read_text()
        assert "UNMAPPED_TOP_LEVEL" in text
        assert "tests/test_*.py" in text

    def test_skip_message_no_longer_lies(self) -> None:
        """The 0-test manifest branch is only reachable for genuinely
        non-executable changes now; its wording must not claim 'no Python
        source files changed' (it printed that even when source DID
        change)."""
        text = SMART_TEST.read_text()
        assert "No Python source files changed - skipping tests" not in text


class TestUntrackedEnumeration:
    """INV-kinin route: change enumeration was three git-diff calls, none of
    which report untracked files — a NEW test file was invisible until
    committed, and its absence read as 'selected: everything relevant'."""

    def test_ls_files_others_is_used(self) -> None:
        text = SMART_TEST.read_text()
        assert "ls-files --others --exclude-standard" in text

    def test_scope_covers_test_and_source_surfaces(self) -> None:
        pattern = _extract_grep_pattern("UNTRACKED_CHANGES")
        assert _pattern_selects(pattern, "tests/test_brand_new.py")
        assert _pattern_selects(
            pattern, "packages/hypergumbo-core/tests/test_new.py"
        )
        assert _pattern_selects(
            pattern, "packages/hypergumbo-core/tests/fixtures/new.proto"
        )
        assert _pattern_selects(pattern, "scripts/new-tool")
        assert _pattern_selects(pattern, ".agent/hooks/claude-code/new.sh")

    def test_scope_excludes_tracker_ops_and_notebook_noise(self) -> None:
        """Pending tracker .ops files are untracked by design almost
        continuously; sweeping them in would fire the top-level fallback on
        every run."""
        pattern = _extract_grep_pattern("UNTRACKED_CHANGES")
        assert not _pattern_selects(
            pattern, ".agent/tracker-workspace/.ops/.WI-x.ops"
        )
        assert not _pattern_selects(pattern, ".agent/.training-data.jsonl")
        assert not _pattern_selects(pattern, "notes.md")


class TestFixturePattern:
    """INV-kinin dominant route: a fixture edit changes a gate's inputs
    without running the gate. Fixture changes select the owning suite."""

    def test_fixture_paths_are_selected(self) -> None:
        pattern = _extract_grep_pattern("CHANGED_FIXTURE_FILES")
        assert _pattern_selects(pattern, "tests/fixtures/repo/a.py")
        assert _pattern_selects(
            pattern, "packages/hypergumbo-core/tests/fixtures/b/c.proto"
        )

    def test_non_fixture_paths_are_not(self) -> None:
        pattern = _extract_grep_pattern("CHANGED_FIXTURE_FILES")
        assert not _pattern_selects(
            pattern, "packages/hypergumbo-core/src/hypergumbo_core/fixtures.py"
        )
        assert not _pattern_selects(pattern, "docs/fixtures/example.json")


def _doc_gate_greps() -> list[str]:
    """Pull the live doc-gate grep spellings out of the script.

    Extracted rather than restated for the reason the module docstring gives:
    a copy is free to drift from what ships. If the union is ever narrowed to
    one spelling, this returns one and the coverage assertion below fails.
    """
    text = SMART_TEST.read_text()
    block = text.split("DOC_GATE_TESTS=", 1)
    assert len(block) == 2, "DOC_GATE_TESTS assignment not found in smart-test"
    body = block[1].split("} | sort -u)", 1)[0]
    found = re.findall(r"grep -lF (?:'([^']*)'|\"([^\"]*)\")", body)
    return [a or b for a, b in found]


def _root_tests_matching(needle: str) -> set[str]:
    """Root tests containing ``needle``, by the script's own fixed-string rule."""
    return {
        p.name
        for p in sorted((REPO_ROOT / "tests").glob("test_*.py"))
        if needle in p.read_text(encoding="utf-8")
    }


class TestDocGateSelection:
    """INV-kafak: the gates that GOVERN documents must run on document changes.

    Measured, not inferred. ``smart-test --manifest`` on a tree whose only
    edits were under ``docs/`` wrote ``Selected tests: 0``, and measurement
    0009 merged green while failing ``check-measurement-frame`` — the gate
    ADR-0048 §A3 exists for. On the one change class those gates check, the
    selector ran every test except them."""

    def test_the_docs_pattern_selects_a_document_and_nothing_else(self) -> None:
        pattern = _extract_grep_pattern("CHANGED_DOC_FILES")
        assert _pattern_selects(pattern, "docs/measurements/0010-shapes.md")
        assert _pattern_selects(pattern, "docs/adr/0049-deferred.md")
        assert not _pattern_selects(pattern, "CHANGELOG.md")
        assert not _pattern_selects(
            pattern, "packages/hypergumbo-core/src/hypergumbo_core/ir.py"
        )
        assert not _pattern_selects(pattern, "notdocs/thing.md")

    def test_the_gate_set_is_derived_from_the_tree(self) -> None:
        """A hardcoded roster would cover today's gates and leave the next one
        green and unrun — the decay that put nine of fifteen languages in
        ``F2_LANGS``. The selection must glob the root suite."""
        text = SMART_TEST.read_text()
        assert "DOC_GATE_TESTS" in text
        head = text.split("DOC_GATE_TESTS=", 1)[1][:600]
        assert "tests/test_*.py" in head

    def test_the_regression_that_merged_is_now_selected(self) -> None:
        """The specific gate a docs-only PR skipped while breaking it."""
        selected: set[str] = set()
        for needle in _doc_gate_greps():
            selected |= _root_tests_matching(needle)
        assert "test_check_measurement_frame.py" in selected
        assert "test_adr_readme_index_sync.py" in selected
        assert "test_adr_supersession_symmetry.py" in selected

    def test_the_union_is_a_superset_of_every_arm(self) -> None:
        """The three arms are not equally load-bearing and the script says so.

        `docs/` alone currently reaches all thirteen gates; the two
        quoted-bareword arms are defensive, for a `Path("docs") / "adr"`
        spelling no root test uses today. This asserted strict inequality
        first and FAILED, which is how that was established rather than
        assumed — the comment in the script was corrected to match. What is
        pinned here is the property that actually matters: no arm reaches
        outside the union, and the union is never smaller than the path arm."""
        greps = _doc_gate_greps()
        assert len(greps) >= 3, greps
        union: set[str] = set()
        for needle in greps:
            union |= _root_tests_matching(needle)
        for needle in greps:
            assert _root_tests_matching(needle) <= union, needle
        assert _root_tests_matching("docs/") <= union
        assert len(union) >= 13, sorted(union)

    def test_the_source_count_no_longer_writes_a_stray_zero(self) -> None:
        """``grep -c .`` PRINTS 0 and EXITS 1 on empty input, so ``|| echo 0``
        fired in addition to grep's own count and made SOURCE_COUNT the
        two-line string ``0\\n0`` — a bare ``0`` in the manifest header between
        two ``#`` comments. Latent until the doc-gate union made this writer
        reachable with zero changed sources."""
        text = SMART_TEST.read_text()
        assert 'SOURCE_COUNT=$(echo "$CHANGED_SOURCE_FILES" | grep -c . || true)' in text
        assert 'SOURCE_COUNT=$(echo "$CHANGED_SOURCE_FILES" | grep -c . || echo 0)' not in text


def _catalogue_gate_greps() -> list[str]:
    """Pull the live catalogue-gate grep spellings out of the script."""
    text = SMART_TEST.read_text()
    block = text.split("CATALOGUE_GATE_TESTS=", 1)
    assert len(block) == 2, "CATALOGUE_GATE_TESTS assignment not found in smart-test"
    body = block[1].split("} | sort -u)", 1)[0]
    return [a or b for a, b in re.findall(r"grep -rlF (?:'([^']*)'|\"([^\"]*)\")", body)]


def _tests_matching(needle: str) -> set[str]:
    """Every package or root test containing ``needle``, by the script's rule."""
    found = set()
    for base in sorted(REPO_ROOT.glob("packages/*/tests")):
        found |= {p.name for p in base.glob("test_*.py")
                  if needle in p.read_text(encoding="utf-8", errors="ignore")}
    found |= {p.name for p in (REPO_ROOT / "tests").glob("test_*.py")
              if needle in p.read_text(encoding="utf-8", errors="ignore")}
    return found


class TestCatalogueGateSelection:
    """INV-muvis: a catalogue YAML change must run the tests that read it.

    MEASURED, on the change that prompted it. Moving stdin readers between
    boundaries in five ``io_primitives`` files produced
    ``Targeted run (2 test files, 0 changed sources)`` — and both of those were
    the tests that same commit added. CI runs the COMMITTED manifest, so the
    io-boundary and taint suites the change could break would not have run.

    This is INV-kafak's defect in a second change class, which is why the fix
    is the same derived-from-the-tree shape rather than a list of file names.
    """

    def test_the_catalogue_pattern_selects_a_catalogue_and_nothing_else(self) -> None:
        pattern = _extract_grep_pattern("CHANGED_CATALOGUE_FILES")
        assert _pattern_selects(
            pattern,
            "packages/hypergumbo-core/src/hypergumbo_core/io_primitives/go.yaml",
        )
        assert _pattern_selects(
            pattern,
            "packages/hypergumbo-core/src/hypergumbo_core/io_primitives_overlays/"
            "go-web-frameworks.yaml",
        )
        assert not _pattern_selects(
            pattern,
            "packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py",
        )
        assert not _pattern_selects(pattern, "docs/measurements/0010-shapes.md")

    def test_the_gate_set_is_derived_from_the_tree(self) -> None:
        """No hardcoded roster: every selected name must exist as a test file."""
        selected: set[str] = set()
        for needle in _catalogue_gate_greps():
            selected |= _tests_matching(needle)
        assert selected, "the catalogue gate selects nothing at all"
        names = {p.name for p in REPO_ROOT.glob("packages/*/tests/test_*.py")}
        names |= {p.name for p in (REPO_ROOT / "tests").glob("test_*.py")}
        assert selected <= names

    def test_the_change_that_prompted_this_is_now_selected(self) -> None:
        """The regression, named. These read the catalogue and ran on neither arm."""
        selected: set[str] = set()
        for needle in _catalogue_gate_greps():
            selected |= _tests_matching(needle)
        assert "test_unconditional_stdin_reads.py" in selected
        assert "test_inv_nular_false_sources.py" in selected
        assert "test_deferred_crossing_boundary.py" in selected

    def test_every_arm_contributes_or_the_comment_is_wrong(self) -> None:
        """The script claims three spellings each catch something the others miss.

        A redundant arm is not harmful, but a comment asserting it is
        load-bearing when it is not is — that exact claim was refuted once
        already on the doc gate. So this measures rather than trusts: each arm
        must select at least one file, and the union must be a strict superset
        of at least one arm, or the comment above it needs rewriting.
        """
        per_arm = {n: _tests_matching(n) for n in _catalogue_gate_greps()}
        for needle, hits in per_arm.items():
            assert hits, f"the {needle!r} arm selects nothing"
        union: set[str] = set()
        for hits in per_arm.values():
            union |= hits
        assert any(union > hits for hits in per_arm.values()), (
            "no arm is a proper subset of the union — the union is one arm "
            "restated, and the script's comment should say so"
        )
