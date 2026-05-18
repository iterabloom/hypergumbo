# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/auto-pr`` ``--title`` / ``--description`` flag parsing.

``auto-pr`` historically accepted only **positional** title and description
(``./scripts/auto-pr "my title" "my description"``). Agents that invoked it
with the more conventional flag form
(``./scripts/auto-pr --title "X" --description "Y"``) silently ended up
with PRs whose Forgejo title was the literal string ``--title``, because
the global flag parser at the top of the script had no ``--title)`` /
``--description)`` case branches — the unknown flags fell through the
``*)`` ``break`` and became positional ``$1`` / ``$2``. The intended title
text moved one slot right and became the description, and the actual
description was silently dropped.

This test file pins the post-fix behaviour:

* Flag form (``--title X --description Y``) populates ``TITLE`` and
  ``DESC`` correctly.
* Positional form (``X Y``) continues to work as before (BC).
* Mixed form (``--title X Y``) takes the title from the flag and the
  description from the remaining positional arg.
* ``--title`` / ``--description`` with no value fail loudly (mirroring
  the ``--tracker-id`` precedent).

The vPR queue file is the cheap observation surface: ``AUTO_PR_SIMULATE_OUTAGE=1``
queues a vPR record that includes the resolved title/description as JSON
fields. Reading the queue file lets the test verify what ``auto-pr``
resolved without round-tripping to Forgejo.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT_REAL = Path(__file__).resolve().parent.parent
AUTO_PR_PATH = REPO_ROOT_REAL / "scripts" / "auto-pr"


def _init_fake_repo(tmp_path: Path, branch: str = "feature") -> Path:
    """Build a tiny fake git repo on ``branch`` with a sentinel commit.

    Mirrors the helper in ``test_autopr_tracker_id.py`` so the test
    surface stays consistent across the auto-pr test family.
    """
    fake_root = tmp_path / "fake-repo"
    fake_root.mkdir()

    def run(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(fake_root),
            check=True,
            capture_output=True,
        )

    run("init", "-q", "-b", "dev")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    # Make the commit subject distinctive so we can tell the difference
    # between flag-supplied title and commit-message default.
    run("commit", "--allow-empty", "-m", "commit-subject-fallback-marker")
    run("remote", "add", "origin", "https://codeberg.org/test/repo.git")
    if branch != "dev":
        run("checkout", "-q", "-b", branch)
    return fake_root


def _run_autopr(
    fake_root: Path, *args: str, extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for k in (
        "FORGEJO_USER", "FORGEJO_TOKEN",
        "SELFHOSTED_FORGEJO_USER", "SELFHOSTED_FORGEJO_TOKEN",
        "AUTO_PR_SIMULATE_OUTAGE",
    ):
        env.pop(k, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(AUTO_PR_PATH), *args],
        cwd=str(fake_root),
        capture_output=True,
        text=True,
        env=env,
    )


def _outage_env() -> dict[str, str]:
    """Env block for the simulated-outage path that exercises queue_vpr.

    AUTO_PR_SKIP_MANIFEST=1 short-circuits ``scripts/smart-test --manifest``
    which would otherwise try to install hypergumbo into the fake repo.
    """
    return {
        "FORGEJO_USER": "u",
        "FORGEJO_TOKEN": "t",
        "AUTO_PR_SIMULATE_OUTAGE": "1",
        "AUTO_PR_SKIP_MANIFEST": "1",
    }


def _read_queue_entry(fake_root: Path) -> dict:
    queue = fake_root / ".git" / "PR_QUEUE"
    assert queue.exists(), "vPR queue file not created"
    line = queue.read_text().strip().splitlines()[-1]
    return json.loads(line)


class TestFlagForm:
    """``--title X --description Y`` populates TITLE and DESC correctly."""

    def test_both_flags_populate_queue_entry(self, tmp_path: Path) -> None:
        fake = _init_fake_repo(tmp_path, branch="feature")
        title = "fix(scope): a meaningful PR title"
        desc = "Body line one.\nBody line two."
        result = _run_autopr(
            fake,
            "--title", title,
            "--description", desc,
            extra_env=_outage_env(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        entry = _read_queue_entry(fake)
        assert entry["title"] == title, entry
        # ``queue_vpr`` JSON-escapes newlines as ``\\n`` on the wire;
        # ``json.loads`` reverses that, so the parsed value matches the
        # original Python string.
        assert entry["desc"] == desc, entry

    def test_title_only_falls_back_to_commit_body_for_desc(
        self, tmp_path: Path,
    ) -> None:
        """``--title X`` (no --description) → desc defaults to commit body."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        result = _run_autopr(
            fake,
            "--title", "feat: flag-only title",
            extra_env=_outage_env(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        entry = _read_queue_entry(fake)
        assert entry["title"] == "feat: flag-only title", entry
        # Description defaults to commit body — for an empty commit
        # message that's an empty string. The key behaviour is that
        # the title is NOT the literal "--title".
        assert entry["title"] != "--title"

    def test_description_only_falls_back_to_commit_subject_for_title(
        self, tmp_path: Path,
    ) -> None:
        """``--description X`` alone → title defaults to commit subject."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        result = _run_autopr(
            fake,
            "--description", "explicit body text",
            extra_env=_outage_env(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        entry = _read_queue_entry(fake)
        assert entry["title"] == "commit-subject-fallback-marker", entry
        assert entry["desc"] == "explicit body text", entry


class TestPositionalFormBackwardsCompat:
    """Positional ``X Y`` continues to populate TITLE and DESC unchanged."""

    def test_positional_args_unchanged(self, tmp_path: Path) -> None:
        fake = _init_fake_repo(tmp_path, branch="feature")
        result = _run_autopr(
            fake,
            "positional title", "positional desc",
            extra_env=_outage_env(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        entry = _read_queue_entry(fake)
        assert entry["title"] == "positional title", entry
        assert entry["desc"] == "positional desc", entry

    def test_no_args_uses_commit_message_defaults(
        self, tmp_path: Path,
    ) -> None:
        """``./scripts/auto-pr`` (no args) → title from commit subject."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        result = _run_autopr(fake, extra_env=_outage_env())
        assert result.returncode == 0, result.stdout + result.stderr
        entry = _read_queue_entry(fake)
        assert entry["title"] == "commit-subject-fallback-marker", entry


class TestMixedForm:
    """``--title X Y`` takes title from flag, description from positional."""

    def test_flag_title_with_positional_description(
        self, tmp_path: Path,
    ) -> None:
        fake = _init_fake_repo(tmp_path, branch="feature")
        result = _run_autopr(
            fake,
            "--title", "flag-supplied title",
            "positional desc",
            extra_env=_outage_env(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        entry = _read_queue_entry(fake)
        assert entry["title"] == "flag-supplied title", entry
        assert entry["desc"] == "positional desc", entry

    def test_flag_description_with_positional_title(
        self, tmp_path: Path,
    ) -> None:
        """Positional title still binds to $1 when only --description is flagged."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        result = _run_autopr(
            fake,
            "--description", "flag-supplied desc",
            "positional title",
            extra_env=_outage_env(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        entry = _read_queue_entry(fake)
        assert entry["title"] == "positional title", entry
        assert entry["desc"] == "flag-supplied desc", entry


class TestMissingValue:
    """``--title`` and ``--description`` without values fail loudly."""

    def test_missing_title_value_errors(self, tmp_path: Path) -> None:
        fake = _init_fake_repo(tmp_path, branch="feature")
        result = _run_autopr(
            fake,
            "--title",  # no value
            extra_env={"FORGEJO_USER": "u", "FORGEJO_TOKEN": "t"},
        )
        # Any non-zero exit is acceptable — the behaviour we're ruling
        # out is silently treating the next positional or another flag
        # as the title value.
        assert result.returncode != 0, result.stdout + result.stderr

    def test_title_with_flag_as_value_errors(self, tmp_path: Path) -> None:
        """``--title --description`` rejects ``--description`` as the title value."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        result = _run_autopr(
            fake,
            "--title", "--description", "y",
            extra_env={"FORGEJO_USER": "u", "FORGEJO_TOKEN": "t"},
        )
        assert result.returncode != 0, result.stdout + result.stderr

    def test_missing_description_value_errors(
        self, tmp_path: Path,
    ) -> None:
        fake = _init_fake_repo(tmp_path, branch="feature")
        result = _run_autopr(
            fake,
            "--description",  # no value
            extra_env={"FORGEJO_USER": "u", "FORGEJO_TOKEN": "t"},
        )
        assert result.returncode != 0, result.stdout + result.stderr


class TestCoexistsWithOtherFlags:
    """``--title``/``--description`` coexist with existing flags."""

    def test_with_tracker_id(self, tmp_path: Path) -> None:
        fake = _init_fake_repo(tmp_path, branch="feature")
        result = _run_autopr(
            fake,
            "--tracker-id", "WI-foo-bar-baz-quux-fuzz-fizz-buzz-barn",
            "--title", "fix: scope of work",
            "--description", "body",
            extra_env=_outage_env(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        entry = _read_queue_entry(fake)
        assert entry["title"] == "fix: scope of work", entry

    def test_does_not_break_list_subcommand(self, tmp_path: Path) -> None:
        """``--title X list`` still dispatches ``list`` cleanly."""
        fake = _init_fake_repo(tmp_path, branch="feature")
        # Empty queue → list command should succeed and report empty.
        result = _run_autopr(
            fake,
            "--title", "irrelevant", "list",
            extra_env={"FORGEJO_USER": "u", "FORGEJO_TOKEN": "t"},
        )
        assert result.returncode == 0, result.stdout + result.stderr
