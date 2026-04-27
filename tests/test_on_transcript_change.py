# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``.agent/hooks/_shared/on_transcript_change.py``.

Named to match the hook file so that ``top_level_test_map.py`` picks
them up when the hook source changes — per-PR smart-test then runs
these tests automatically instead of relying only on the 4-hour
full-suite sweep (WI-javan).

Thematic tests that predate this file (``recently_injected``,
``parse_selection``, filter pipeline, injection-history sidecar,
session-id / per-session naming invariants) live in
``test_transcript_pipeline_properties.py`` for historical reasons and
are not moved here in this PR — moving them would muddy the diff for
the narrow fix this file accompanies (WI-ritut).
"""

from __future__ import annotations

import fcntl
import importlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
from typing import Any

import pytest


def _import_hook_module(name: str, filename: str) -> Any:
    """Import a Python file from ``.agent/hooks/_shared/`` as a module.

    Duplicated from ``test_transcript_pipeline_properties.py`` rather
    than imported from there because tests should be independently
    runnable — a test file that can't stand alone defeats pytest's
    file-level isolation guarantees.
    """
    script_path = str(
        Path(__file__).parent.parent / ".agent" / "hooks" / "_shared" / filename
    )
    loader = importlib.machinery.SourceFileLoader(name, script_path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hook_mod() -> Any:
    """Import on_transcript_change.py as a module."""
    return _import_hook_module("on_transcript_change", "on_transcript_change.py")


# ---------------------------------------------------------------------------
# Injection-state lock tests (WI-ritut — TOCTOU race fix)
# ---------------------------------------------------------------------------

class TestInjectionStateLock:
    """Tests for ``injection_state_lock`` / ``_lock_path``.

    These cover the WI-ritut fix: the transcript-change hook's critical
    section (load → decide → save on the per-session injection state file)
    is guarded by an advisory ``fcntl.flock`` so that concurrent hook
    invocations don't emit duplicate playbook injections.

    The serialization itself is tested directly via ``fcntl.LOCK_NB``
    rather than via threads/processes — synchronous, hermetic, and
    proves the exact property that matters: while one holder has the
    exclusive lock, a second non-blocking acquire fails.
    """

    def test_lock_path_encodes_session_id_beside_state_file(
        self, hook_mod, tmp_path: Path,
    ) -> None:
        lock_path = hook_mod._lock_path(str(tmp_path), "sess-abc-123")
        state_path = hook_mod._state_path(str(tmp_path), "sess-abc-123")

        # Lock file lives next to the state file in .agent/
        assert os.path.dirname(lock_path) == os.path.dirname(state_path)
        assert lock_path.endswith(
            ".transcript-injection-state.sess-abc-123.lock"
        )
        # And the lock is distinct from the state file — critical for
        # keeping the JSON-valued state file uncontaminated by flock's
        # empty placeholder file contents.
        assert lock_path != state_path

    def test_lock_path_distinct_per_session(
        self, hook_mod, tmp_path: Path,
    ) -> None:
        """Different session ids get different lock paths, so concurrent
        sessions in the same repo do not serialize against each other."""
        a = hook_mod._lock_path(str(tmp_path), "sess-a")
        b = hook_mod._lock_path(str(tmp_path), "sess-b")
        assert a != b

    def test_lock_context_creates_lockfile_on_enter(
        self, hook_mod, tmp_path: Path,
    ) -> None:
        lockfile = hook_mod._lock_path(str(tmp_path), "sess")
        assert not os.path.exists(lockfile)
        with hook_mod.injection_state_lock(str(tmp_path), "sess"):
            assert os.path.exists(lockfile)
        # File persists after exit (that's fine — it's just a sentinel).
        assert os.path.exists(lockfile)

    def test_lock_holds_exclusively_inside_context(
        self, hook_mod, tmp_path: Path,
    ) -> None:
        """While one holder is inside the ``with``, a non-blocking
        acquire on the same path must fail. This is the fcntl-level
        property that closes the WI-ritut race."""
        lockfile = hook_mod._lock_path(str(tmp_path), "sess")
        with hook_mod.injection_state_lock(str(tmp_path), "sess"):
            # A second acquire on the same path with LOCK_NB must fail
            # because the with-block already holds the exclusive lock.
            os.makedirs(os.path.dirname(lockfile), exist_ok=True)
            with open(lockfile, "w") as contender_fd:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(
                        contender_fd.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )

    def test_lock_released_after_normal_exit(
        self, hook_mod, tmp_path: Path,
    ) -> None:
        """After the with-block exits normally, the lock is released so
        the next holder can enter without blocking."""
        with hook_mod.injection_state_lock(str(tmp_path), "sess"):
            pass
        # Re-acquire immediately — non-blocking to prove no residual hold.
        lockfile = hook_mod._lock_path(str(tmp_path), "sess")
        with open(lockfile, "w") as fd:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)

    def test_lock_released_after_exception(
        self, hook_mod, tmp_path: Path,
    ) -> None:
        """Exception inside the with-block must still release the lock
        (otherwise a crashing hook would hang every subsequent one)."""
        with pytest.raises(RuntimeError):
            with hook_mod.injection_state_lock(str(tmp_path), "sess"):
                raise RuntimeError("simulated hook crash")
        # Next acquire must succeed non-blocking.
        lockfile = hook_mod._lock_path(str(tmp_path), "sess")
        with open(lockfile, "w") as fd:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)

    def test_lock_creates_agent_dir_if_missing(
        self, hook_mod, tmp_path: Path,
    ) -> None:
        """If the .agent/ subdirectory doesn't exist yet (e.g. a fresh
        session on a repo that has never seen a hook fire), the lock
        helper creates it rather than erroring. Otherwise the race fix
        would fail on the first hook of a session."""
        # Intentionally do NOT create .agent/ — tmp_path is empty.
        assert not (tmp_path / ".agent").exists()
        with hook_mod.injection_state_lock(str(tmp_path), "sess"):
            assert (tmp_path / ".agent").is_dir()


# ---------------------------------------------------------------------------
# WI-bodog: presentation helpers
# ---------------------------------------------------------------------------

class TestStripSpdxHeader:
    """Tests for ``strip_spdx_header``."""

    def test_strips_leading_spdx_html_comment(self, hook_mod) -> None:
        content = (
            "<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->\n"
            "\n"
            "## Real Heading\n"
            "Body text.\n"
        )
        out = hook_mod.strip_spdx_header(content)
        assert out.startswith("## Real Heading")
        assert "SPDX" not in out

    def test_no_spdx_left_unchanged(self, hook_mod) -> None:
        content = "## Real Heading\nBody text.\n"
        assert hook_mod.strip_spdx_header(content) == content

    def test_empty_content_returns_empty(self, hook_mod) -> None:
        assert hook_mod.strip_spdx_header("") == ""

    def test_spdx_with_no_blank_line_after(self, hook_mod) -> None:
        content = (
            "<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->\n"
            "## Heading\n"
        )
        out = hook_mod.strip_spdx_header(content)
        assert out.startswith("## Heading")

    def test_only_strips_when_first_line(self, hook_mod) -> None:
        # SPDX comment NOT at the very start of the document — leave it.
        content = (
            "## Heading\n"
            "<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->\n"
        )
        out = hook_mod.strip_spdx_header(content)
        assert "SPDX" in out


class TestExtractNaturalTitle:
    """Tests for ``extract_natural_title``."""

    def test_h1_heading(self, hook_mod) -> None:
        content = "# Long-Running Output Capture Playbook\n\nBody."
        assert (
            hook_mod.extract_natural_title(content)
            == "Long-Running Output Capture Playbook"
        )

    def test_h2_heading(self, hook_mod) -> None:
        content = "## Post-Compaction State Recovery\n\nBody."
        assert (
            hook_mod.extract_natural_title(content)
            == "Post-Compaction State Recovery"
        )

    def test_h3_heading(self, hook_mod) -> None:
        content = "### DEEP Mode Priority Queue:\n\nBody."
        assert (
            hook_mod.extract_natural_title(content)
            == "DEEP Mode Priority Queue:"
        )

    def test_skips_html_comment_lines(self, hook_mod) -> None:
        content = (
            "<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->\n"
            "\n"
            "## Real Heading\n"
        )
        assert (
            hook_mod.extract_natural_title(content) == "Real Heading"
        )

    def test_returns_empty_when_no_heading(self, hook_mod) -> None:
        content = "Just a paragraph, no heading.\n"
        assert hook_mod.extract_natural_title(content) == ""

    def test_returns_empty_for_empty(self, hook_mod) -> None:
        assert hook_mod.extract_natural_title("") == ""

    def test_strips_trailing_pound_signs(self, hook_mod) -> None:
        # Setext-style atx with closing #s — shouldn't break parser.
        content = "## Trim Me ##\nBody."
        assert hook_mod.extract_natural_title(content) == "Trim Me"

    def test_does_not_scan_past_first_non_heading(self, hook_mod) -> None:
        # If the first non-blank, non-comment line isn't a heading,
        # bail rather than scan deeper — we don't want to match a
        # heading buried inside the body.
        content = "Lead paragraph.\n\n## Buried Heading\n"
        assert hook_mod.extract_natural_title(content) == ""


class TestFormatPlaybookBlock:
    """Tests for ``format_playbook_block`` — the per-block renderer."""

    def test_block_includes_title_path_hint_and_body(self, hook_mod) -> None:
        content = (
            "<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->\n"
            "\n"
            "## Recover State Playbook\n"
            "Body content here.\n"
        )
        out = hook_mod.format_playbook_block(
            "recover-state-playbook",
            ".agent/agent_playbooks_protocols_sops_skills/recover-state-playbook.md",
            content,
        )
        # Divider: title + path
        assert "Recover State Playbook" in out
        assert (
            ".agent/agent_playbooks_protocols_sops_skills/recover-state-playbook.md"
            in out
        )
        # Framing hint
        assert "consult this instead of re-reading the file" in out
        # Body present
        assert "Body content here." in out
        # SPDX stripped from body
        assert "SPDX" not in out

    def test_block_falls_back_to_id_when_no_heading(self, hook_mod) -> None:
        content = "Just a body paragraph."
        out = hook_mod.format_playbook_block(
            "some-id",
            ".agent/path/to.md",
            content,
        )
        assert "some-id" in out
        assert ".agent/path/to.md" in out

    def test_block_starts_with_divider(self, hook_mod) -> None:
        content = "## Title\nBody."
        out = hook_mod.format_playbook_block(
            "some-id", ".agent/path/to.md", content
        )
        assert out.startswith("--- ")

    def test_em_dash_in_divider(self, hook_mod) -> None:
        # The divider uses an em dash between title and path so the
        # agent's pattern-matching for "title — path" is unambiguous.
        content = "## My Title\nBody."
        out = hook_mod.format_playbook_block(
            "id", ".agent/p.md", content
        )
        assert "My Title — .agent/p.md" in out
