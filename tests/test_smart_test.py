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
