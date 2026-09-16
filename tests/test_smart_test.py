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
import pathlib
import subprocess
import sys
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


def _catalogue_predicate_selects(path: str) -> bool:
    """Run smart-test's OWN catalogue line, both greps, over one path.

    ``_extract_grep_pattern`` reads the first ``grep -E`` off the assignment,
    which stopped being the whole predicate when the key widened from "is
    YAML" to "is not Python" — the exclusion lives in a second grep down the
    pipe. Testing only the first half would have certified a predicate that
    selects every Python file under a package subdirectory.
    """
    line = None
    for raw in SMART_TEST.read_text().splitlines():
        stripped = raw.strip()
        if stripped.startswith("CHANGED_CATALOGUE_FILES=") and "grep -E" in stripped:
            line = stripped
            break
    assert line, "CHANGED_CATALOGUE_FILES assignment not found in smart-test"
    body = line.split("$(", 1)[1].rsplit(")", 1)[0]
    pipeline = body.split("|", 1)[1]
    result = subprocess.run(
        ["bash", "-c", f"echo \"$1\" | {pipeline}", "_", path],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


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


class TestConftestPattern:
    """INV-kinin's sibling, one filename over.

    A ``conftest.py`` is neither a ``test_``-prefixed test file nor package
    ``src/``, so a change confined to one matched NEITHER ``CHANGED_TEST_FILES``
    nor ``CHANGED_SOURCE_FILES`` and selected NOTHING -- smart-test printed
    "no test-relevant files changed (docs/config only)" and wrote a manifest
    with 0 tests, so CI would skip pytest entirely.

    That is strictly worse than the fixture case INV-kinin already fixed: an
    ``autouse`` fixture declared in a conftest governs EVERY test beneath it.
    Found when a conftest change that REPAIRED 13 failing tests produced a
    0-test manifest -- the repair and the blindness in the same commit.
    """

    def test_package_conftests_are_selected(self) -> None:
        pattern = _extract_grep_pattern("CHANGED_CONFTEST_FILES")
        assert _pattern_selects(
            pattern, "packages/hypergumbo-tracker/tests/conftest.py"
        )
        assert _pattern_selects(pattern, "tests/conftest.py")
        assert _pattern_selects(pattern, "conftest.py")

    def test_non_conftest_paths_are_not(self) -> None:
        pattern = _extract_grep_pattern("CHANGED_CONFTEST_FILES")
        assert not _pattern_selects(
            pattern, "packages/hypergumbo-core/tests/test_conftest_helpers.py"
        )
        assert not _pattern_selects(
            pattern, "packages/hypergumbo-core/src/hypergumbo_core/conftest_util.py"
        )
        assert not _pattern_selects(pattern, "docs/conftest.md")


class TestAConftestChangeSelectsItsOwningSuite:
    """The end the pattern exists for: a real conftest path picks real tests."""

    def test_the_tracker_conftest_selects_tracker_tests(self) -> None:
        """Read off the shipped script, not restated: the block routes a
        ``packages/<pkg>/tests/**/conftest.py`` to that package's suite."""
        text = SMART_TEST.read_text()
        assert "CHANGED_CONFTEST_FILES" in text
        block = text.split("CHANGED_CONFTEST_FILES=", 1)[1]
        # The owning-suite routing must name BOTH homes, or one of them
        # silently selects nothing.
        assert "packages/*/tests/*" in block or "packages/*" in block
        assert "tests/test_*.py" in block

    def test_a_root_conftest_reaches_package_suites_too(self) -> None:
        """A rootdir conftest applies to every test collected beneath it,
        packages included -- selecting only the root suite would under-select
        exactly where the blast radius is widest."""
        text = SMART_TEST.read_text()
        block = text.split("CHANGED_CONFTEST_FILES=", 1)[1].split("\nfi\n", 1)[0]
        assert "packages/*/tests/test_*.py" in block


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


#: A real shipped YAML from the family the catalogue gate was built for.
IO_PRIMITIVES_YAML = (
    "packages/hypergumbo-core/src/hypergumbo_core/io_primitives/go.yaml"
)
#: And from the family it was blind to — 107 files, selecting zero until
#: INV-bigaz widened the key.
FRAMEWORKS_YAML = (
    "packages/hypergumbo-core/src/hypergumbo_core/frameworks/fastapi.yaml"
)


def _catalogue_gate_greps(changed: str = IO_PRIMITIVES_YAML) -> list[str]:
    """The live catalogue-gate grep terms, asked of the helper that derives them.

    These used to be three literals written into the script, and this function
    read them back out of it. They are now derived per family from
    ``YAML_CATALOGS`` (INV-bigaz), so the question "what does the gate grep
    for" has moved from the script's text to a helper's output — and asking
    the helper is what keeps these tests measuring the live mechanism rather
    than a copy of it.
    """
    helper = REPO_ROOT / "scripts" / "catalogue_gate_terms.py"
    proc = subprocess.run(
        [sys.executable, str(helper), str(REPO_ROOT)],
        input=changed,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return [line for line in proc.stdout.splitlines() if line]


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
        assert _catalogue_predicate_selects(
            "packages/hypergumbo-core/src/hypergumbo_core/io_primitives/go.yaml"
        )
        assert _catalogue_predicate_selects(
            "packages/hypergumbo-core/src/hypergumbo_core/io_primitives_overlays/"
            "go-web-frameworks.yaml"
        )
        assert _catalogue_predicate_selects(
            "packages/hypergumbo-core/src/hypergumbo_core/url_folding/SCOPE.md"
        ), "SCOPE.md is parsed at runtime and is not YAML"
        assert not _catalogue_predicate_selects(
            "packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py"
        )
        assert not _catalogue_predicate_selects(
            "packages/hypergumbo-core/src/hypergumbo_core/linkers/route_handler.py"
        ), "a Python file in a package subdirectory is the slice's job, not this one"
        assert not _catalogue_predicate_selects("docs/measurements/0010-shapes.md")

    def test_the_shell_fallback_and_the_helper_agree_on_the_live_tree(self) -> None:
        """The degraded path is the one duplicate of the predicate; pin it.

        smart-test falls back to a shell-derived directory name when python3 or
        the helper is unavailable, because silence is the worst failure mode
        for a selector. Two homes for one fact is how a widening lands in one
        and not the other, so this measures that they still agree about what
        counts as shipped data."""
        tracked = subprocess.run(
            ["git", "ls-files", "packages/"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.splitlines()

        # One representative per distinct SHAPE rather than per file: the two
        # predicates can only disagree on depth-below-src and suffix, and
        # asking both about two thousand paths costs 50 seconds to re-ask the
        # same question. Every shape the tree contains is still exercised, and
        # the grouping is stated here rather than left as an unexplained
        # sample.
        shapes = {}
        for path in tracked:
            parts = path.split("/")
            depth = len(parts) - parts.index("src") if "src" in parts else 0
            shapes.setdefault((depth, pathlib.PurePath(path).suffix), path)
        assert len(shapes) >= 6, sorted(shapes)

        disagreements = [
            (path, by_shell, by_helper)
            for path in shapes.values()
            for by_shell, by_helper in [
                (_catalogue_predicate_selects(path),
                 bool(_catalogue_gate_greps(path)))
            ]
            if by_shell != by_helper
        ]
        assert not disagreements, disagreements

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

    def test_the_loader_arm_is_load_bearing_and_not_decoration(self) -> None:
        """The terms are derived now, so what needs pinning is WHY there are two.

        This replaced an assertion that each of three hand-written arms earned
        its place. With per-family derivation a term selecting nothing is not a
        defect — it means no test names that loader entry point YET, and one
        will. What stays falsifiable is the measurement that justified
        grepping for the LOADER at all: on the live tree the directory name
        alone reaches 58 test files and the union reaches 157, so a gate keyed
        on the catalogue's own name would miss two thirds of what reads it.
        """
        union: set[str] = set()
        for term in _catalogue_gate_greps():
            union |= _tests_matching(term)
        by_name_only = _tests_matching("io_primitives")
        assert by_name_only, "the directory-name arm selects nothing at all"
        assert union > by_name_only, (
            "the loader arm adds nothing — either the registry's loader field "
            "stopped resolving or the comment claiming it is load-bearing is "
            "now wrong"
        )

    def test_the_family_the_old_key_was_blind_to_now_selects(self) -> None:
        """INV-bigaz/INV-dohoj, the residual: 107 framework YAMLs selecting zero.

        The old key named io_primitives and its overlays. A change confined to
        frameworks/ wrote a 0-test manifest and printed "no test-relevant files
        changed (docs/config only)" — the green tick over a hole, with the
        message asserting the opposite of what happened.
        """
        selected: set[str] = set()
        for term in _catalogue_gate_greps(FRAMEWORKS_YAML):
            selected |= _tests_matching(term)
        assert selected, "a frameworks/ change still selects nothing"
        assert _pattern_selects(
            _extract_grep_pattern("CHANGED_CATALOGUE_FILES"), FRAMEWORKS_YAML
        ), "the script's own pattern does not even reach the helper"


class TestFullMeansFull:
    """WI-ginuj: a gate named for totality that is silently partial.

    ``--full`` collected ``packages/*`` and never the repo-root ``tests/``,
    while CI runs the manifest, which does include root tests. PR #890 went
    green locally over 26,702 tests and red in CI on
    ``test_adr_readme_index_sync.py`` — a test the local gate holds and simply
    did not run.

    The item filed three sites, found by grepping ``run_pytest``. There were
    five: the background suite calls ``pytest`` directly and the full-suite
    manifest writer enumerates with ``find``. That is why the fix is one
    definition rather than five edits, and why the first test here is
    structural — it is the one that would have found the two the grep missed.
    """

    def _definition_lines(self) -> list[str]:
        return [
            line
            for line in SMART_TEST.read_text().splitlines()
            if line.startswith("FULL_SUITE_")
        ]

    def test_the_suite_target_is_defined_once(self) -> None:
        defined = self._definition_lines()
        assert len(defined) == 2, defined

    def test_the_definition_includes_the_root_suite(self) -> None:
        for line in self._definition_lines():
            assert "tests" in line.split("=", 1)[1]
            assert "packages/*/tests" in line

    def test_no_site_names_a_packages_only_suite_target(self) -> None:
        """The structural control: the grep the item ran found three of five.

        Any line that RUNS or ENUMERATES the suite must go through the shared
        definition. A new call site that spells the target out is exactly how
        this defect half-survived its own first fix.
        """
        offenders = []
        for number, line in enumerate(SMART_TEST.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("FULL_SUITE_"):
                continue
            if "packages/*/tests" not in stripped:
                continue
            if any(
                verb in stripped
                for verb in ("run_pytest ", "pytest ", "find packages")
            ):
                offenders.append(f"{number}: {stripped}")
        assert not offenders, offenders

    def test_every_full_suite_entry_point_discloses_its_scope(self) -> None:
        """Fix (b): a partial run must not be able to look like a total one."""
        text = SMART_TEST.read_text()
        assert "disclose_full_suite_scope() {" in text
        # The definition line ends in "() {", so only CALLS match this.
        calls = text.count("disclose_full_suite_scope\n")
        assert calls >= 3, f"defined, but called only {calls} time(s)"

    def test_the_disclosure_names_both_roots_and_hides_nothing(
        self, tmp_path: Path
    ) -> None:
        """Run it, rather than read it — and run it under production's flags.

        The harness takes ``set -euo pipefail`` out of the script rather than
        writing it down: a copy goes stale the day a flag is added, and a
        harness missing production's flags is blind to whole defect classes.
        The function is sliced out by text and driven from a file; the first
        version of this test built a ``bash -c`` string with nested double
        quotes and failed on its own quoting rather than on the code.
        """
        text = SMART_TEST.read_text()
        flags = next(
            line for line in text.splitlines() if line.startswith("set -")
        )
        start = text.index("disclose_full_suite_scope() {")
        end = text.index("\n}\n", start) + len("\n}\n")
        probe = tmp_path / "probe.sh"
        probe.write_text(
            f"{flags}\n"
            f'REPO_ROOT="{REPO_ROOT}"\n'
            'FULL_SUITE_PYTEST_TARGETS="packages/*/tests/ tests/"\n'
            f"{text[start:end]}\n"
            "disclose_full_suite_scope\n"
        )
        result = subprocess.run(
            ["bash", str(probe)], capture_output=True, text=True, cwd=REPO_ROOT
        )
        assert result.returncode == 0, result.stderr
        assert "collecting:" in result.stdout
        assert "packages/hypergumbo-core/tests" in result.stdout
        assert " tests" in result.stdout
        assert "NOT collecting" not in result.stdout, (
            "a directory holding tests is outside the full suite: "
            + result.stdout
        )

    def test_the_test_that_caught_this_is_a_root_test(self) -> None:
        """The failure that filed the item, named."""
        assert (REPO_ROOT / "tests" / "test_adr_readme_index_sync.py").is_file()
        assert not list(
            REPO_ROOT.glob("packages/*/tests/test_adr_readme_index_sync.py")
        )

    def test_no_test_basename_is_shared_between_the_two_suites(self) -> None:
        """What made "full" impossible to fix, found by trying it.

        pytest's default `prepend` import mode names a module after its
        BASENAME, so collecting ``tests/test_generate_concepts.py`` and
        ``packages/hypergumbo-core/tests/test_generate_concepts.py`` in one run
        raises ``import file mismatch`` and INTERRUPTS COLLECTION — the run
        stops, it does not merely skip a file. That is the real obstacle
        behind this item's deferral of fix (a); the reason filed there was
        COV_PATHS, and COV_PATHS turns out not to be a problem at all, since
        more tests can only raise coverage over ``packages/*/src``.

        So this is the invariant that keeps `--full` possible: the two suites
        must not share a basename. A collision is cheap to fix when the file
        is added and expensive to find later, because the error names an
        import, not a policy.
        """
        root = {p.name for p in (REPO_ROOT / "tests").glob("test_*.py")}
        packaged = {p.name for p in REPO_ROOT.glob("packages/*/tests/test_*.py")}
        assert root, "no root tests found — instrument fault"
        assert packaged, "no package tests found — instrument fault"
        assert not (root & packaged), sorted(root & packaged)


class TestCitationGateWiring:
    """WI-lujon: the fourth derived union, and the one that keys backwards.

    The other three ask "what directory did the change touch". This one asks
    "who names this path in their text", so it cannot be folded into them and
    it is worth pinning that smart-test actually calls it — a helper with no
    caller is the same defect the convergence ledger had.
    """

    def test_smart_test_calls_the_citation_helper(self) -> None:
        text = SMART_TEST.read_text()
        assert "scripts/citation_gate_tests.py" in text
        assert "CITATION_GATE_TESTS" in text

    def test_the_union_may_add_and_never_replaces(self) -> None:
        """Every union in this script prints into AFFECTED_TESTS, never over."""
        text = SMART_TEST.read_text()
        block = text.split("CITATION_GATE_HELPER=", 1)[1].split("\nif [[ -z", 1)[0]
        assert 'AFFECTED_TESTS=$(printf' in block
        assert '"$AFFECTED_TESTS"' in block

    def test_the_helper_exists_and_is_executable(self) -> None:
        helper = REPO_ROOT / "scripts" / "citation_gate_tests.py"
        assert helper.is_file()
        assert helper.stat().st_mode & 0o111


class TestPlaybookGateSelection:
    """INV-kafak, third change class: a PLAYBOOK edit selected nothing.

    ``.agent/hooks/`` is in the executable-surface union (INV-lizor) and the
    playbook directory beside it is not — yet playbooks are load-bearing input.
    AGENTS.md's "Creating a New Playbook" requires every playbook to be
    registered in the transcript hook's ``PLAYBOOKS`` list, and
    ``test_on_transcript_change.py`` is the gate that checks the registry and the
    files agree. Renaming or deleting a playbook breaks that gate and, before
    this, ran none of it.

    Measured on the change that prompted it: editing
    ``ci-debug-protocol.md`` selected two test files, neither of which reads a
    playbook.
    """

    def test_the_playbook_pattern_selects_a_playbook_and_nothing_else(self) -> None:
        pattern = _extract_grep_pattern("CHANGED_PLAYBOOK_FILES")
        assert _pattern_selects(
            pattern,
            ".agent/agent_playbooks_protocols_sops_skills/ci-debug-protocol.md",
        )
        assert not _pattern_selects(pattern, ".agent/hooks/_shared/on_transcript_change.py")
        assert not _pattern_selects(pattern, "docs/adr/0049-deferred.md")
        assert not _pattern_selects(pattern, "scripts/ci-debug")

    def test_the_gate_set_is_derived_from_the_tree(self) -> None:
        """No hardcoded roster: whatever names the directory is what runs."""
        selected = _root_tests_matching("agent_playbooks_protocols_sops_skills")
        assert selected, "the playbook gate selects nothing at all"
        assert selected <= {p.name for p in (REPO_ROOT / "tests").glob("test_*.py")}

    def test_the_registry_gate_is_among_them(self) -> None:
        """The specific gate a playbook rename would break."""
        assert "test_on_transcript_change.py" in _root_tests_matching(
            "agent_playbooks_protocols_sops_skills",
        )



def _docs_only_skip_block() -> list[str]:
    """Extract the shipped docs/config-only short-circuit, start to closing ``fi``.

    Extracted rather than restated: a copy of the block here would be free to
    drift from the one that ships, and the defect this pins (INV-sotam) IS a
    property of the shipped block's control flow.
    """
    lines = SMART_TEST.read_text().splitlines()
    starts = [
        i for i, line in enumerate(lines)
        if "writing empty targeted manifest" in line
    ]
    assert len(starts) == 1, f"expected one short-circuit, found {len(starts)}"
    start = starts[0]
    for i in range(start, len(lines)):
        # The branch is nested two levels in; its closing `fi` is the first
        # line at eight spaces of indent.
        if lines[i] == "        fi":
            return lines[start:i]
    raise AssertionError("the docs/config-only branch is never closed")


class TestFullBypassesTheDocsOnlySkip:
    """INV-sotam: ``--full`` must never be overridden by the selection's opinion.

    ``--full`` means "ignore the selection and run everything". The docs/config
    -only short-circuit used to ``exit 0`` before the ``FULL_RUN`` branch was
    ever consulted, so on a tree whose only changes were docs or config the
    command printed a reassuring line, exited 0, and ran nothing. Verifying
    merged work is exactly when a tree looks like that, so the failure mode was
    "the gate cannot fire" wearing the costume of "the gate passed".

    These are structural tests, and the reason is worth recording: the script
    takes a non-blocking ``flock`` on ``.ci/.smart-test.lock`` and resolves its
    own repo root from ``$0``, so a test that invoked it would either fail on
    the lock held by the run executing the test, or operate on the live tree.
    The behavioural repro is in the PR description instead.
    """

    def test_full_is_consulted_before_the_skip_exits(self) -> None:
        """The defect itself: the block must not exit without checking --full."""
        block = _docs_only_skip_block()
        assert any("FULL_RUN" in line for line in block), (
            "the docs/config-only short-circuit exits without consulting "
            "FULL_RUN, so `smart-test --full` runs nothing and exits 0"
        )

    def test_full_runs_the_suite_rather_than_only_skipping_the_exit(self) -> None:
        """Consulting the flag is not enough — it has to run the tests."""
        block = _docs_only_skip_block()
        full_arm = [
            line for line in block
            if "run_pytest" in line and "FULL_SUITE_PYTEST_TARGETS" in line
        ]
        assert full_arm, (
            "--full is consulted but never runs the suite from this branch"
        )

    def test_the_skip_reports_zero_tests_ran(self) -> None:
        """A silent exit 0 reads as success; it has to say nothing ran."""
        block = _docs_only_skip_block()
        assert any("0 tests ran" in line for line in block), (
            "the skip message must state that 0 tests ran, so a caller can "
            "tell 'checked, nothing to do' from 'nothing was checked'"
        )

    def test_full_keeps_the_targeted_manifest(self) -> None:
        """The trap in the obvious fix, pinned so nobody walks into it.

        ``run_full_suite`` writes a FULL-SUITE manifest, which CI rejects by
        design as a signal that change detection went wrong. ``--full`` is a
        local convenience and must leave the committed manifest targeted, so
        routing this branch through ``run_full_suite`` would trade a silent
        skip for a red gate on every docs-only PR.

        Comment lines are stripped before the check: this test's claim is that
        the branch does not CALL that function, and the comment explaining why
        has to be free to name it. Grepping the raw block failed on exactly
        that, which is the difference between matching a name and matching a
        call.
        """
        code = [
            line for line in _docs_only_skip_block()
            if not line.lstrip().startswith("#")
        ]
        assert not any("run_full_suite" in line for line in code), (
            "--full must not route through run_full_suite: that writes the "
            "full-suite manifest CI rejects"
        )
